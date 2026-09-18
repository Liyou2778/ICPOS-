"""Agent 微调前后评测：RAG 检索接地 + 生成 + 数字命中率/关键词覆盖/拒答正确率。

评测流程（与生产链路一致，避免"训练集自证"）：
  1. 对每个评测问题，先用生产 RAG（hybrid_search）检索 top-k 资料；
  2. 用待评模型（基座 或 基座+LoRA adapter）基于资料生成答案；
  3. 打分：
     * 数字命中率：期望答案中的关键数字/单位在生成答案中出现的比例（防"数字被改写/编造"）
     * 字符 bigram F1：与期望答案的字面重叠（确定性、可复现，不依赖裁判模型）
     * 拒答正确率：对"资料不含答案"的负样本，是否明确拒答而非编造
  4. 输出 before/after 两份结果并打印对比。

用法：
    python -m scripts.eval_agent_sft --tag base  --limit 120
    python -m scripts.eval_agent_sft --tag lora  --limit 120
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from datetime import UTC, datetime

from backend.app.core.config import settings
from backend.app.core.db import SessionLocal
from backend.app.services import rag

SFT = settings.repo_root / "data" / "sft"
OUT = settings.repo_root / "data" / "models" / "agent_sft"
SYSTEM = (
    "你是智工云枢 ICOPS 的矿山施工智能运营助手。"
    "回答必须严格依据用户提供的【资料】：数字、型号、金额、时间不得改写或推算；"
    "资料中没有的信息，直接说明“资料不足，无法回答”，不得猜测；"
    "回答末尾用括号给出资料出处。"
)
REFUSAL_MARKERS = ("资料不足", "无法回答", "没有相关", "未找到", "建议转人工")
NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def _load_jsonl(path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _numbers(text: str) -> set[str]:
    return {m.group(0) for m in NUM_RE.finditer(text or "") if len(m.group(0)) >= 2}


def _bigrams(text: str) -> set[str]:
    t = re.sub(r"\s+", "", text or "")
    return {t[i : i + 2] for i in range(len(t) - 1)}


def _f1(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if inter == 0:
        return 0.0
    p, r = inter / len(a), inter / len(b)
    return 2 * p * r / (p + r)


def build_context(db, question: str, top_k: int = 3) -> tuple[str, list[dict]]:
    hits = rag.hybrid_search(db, question, top_k=top_k)
    parts, cits = [], []
    for h in hits:
        parts.append(f"[{h.kb_type}] {h.title}：{h.excerpt}")
        cits.append({"title": h.title, "kb_type": h.kb_type, "source": h.source})
    return "\n".join(parts) if parts else "（无检索结果）", cits


def main() -> int:
    ap = argparse.ArgumentParser(description="Agent 微调前后评测")
    ap.add_argument("--base", default="Qwen/Qwen2.5-1.5B-Instruct")
    ap.add_argument("--adapter", default=None, help="LoRA adapter 目录；缺省评基座")
    ap.add_argument("--tag", default="base")
    ap.add_argument("--limit", type=int, default=120)
    ap.add_argument("--max-new-tokens", type=int, default=220)
    ap.add_argument("--top-k", type=int, default=3)
    ap.add_argument("--dry-run", action="store_true",
                    help="不加载模型：只跑检索并直接把【资料】当答案打分，得到'检索上界'基线")
    args = ap.parse_args()

    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    tok = None
    model = None
    if not args.dry_run:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        tok = AutoTokenizer.from_pretrained(args.base)
        model = AutoModelForCausalLM.from_pretrained(
            args.base,
            torch_dtype=torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16,
            device_map={"": 0},
        )
        if args.adapter:
            from peft import PeftModel

            model = PeftModel.from_pretrained(model, args.adapter)
        model.eval()
    else:
        print("  [dry-run] 不加载模型：输出为检索资料本身（用于测量检索上界与校验打分链路）")

    corpus = _load_jsonl(SFT / "agent_eval_corpus.jsonl")
    # 外部评测集（真实案例/招标门槛）用于系统级端到端评测，此处仅校验其存在与条数
    external_n = len(_load_jsonl(SFT / "agent_eval_external.jsonl"))
    print(f"  语料评测集 {len(corpus)} 条；外部评测集 {external_n} 条（系统级评测使用）")

    rng = __import__("random").Random(20260918)
    corpus_sample = rng.sample(corpus, min(args.limit, len(corpus)))

    db = SessionLocal()
    results = []
    t0 = time.perf_counter()
    try:
        for i, item in enumerate(corpus_sample, 1):
            ctx, cits = build_context(db, item["question"], args.top_k)
            # 拒答负样本：把资料替换为无关条目，期望模型拒答（不编造）
            negative = i % 10 == 0
            if negative:
                other = rng.choice(corpus)["question"]
                ctx, cits = build_context(db, other, args.top_k)
            user = f"问题：{item['question']}\n【资料】\n{ctx}"
            if args.dry_run:
                pred = ctx  # 检索上界：直接把资料当答案
            else:
                msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]
                prompt = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
                inputs = tok(prompt, return_tensors="pt", truncation=True, max_length=2048).to(
                    model.device)
                with torch.no_grad():
                    out = model.generate(**inputs, max_new_tokens=args.max_new_tokens, do_sample=False)
                pred = tok.decode(out[0][inputs["input_ids"].shape[1] :], skip_special_tokens=True)
            exp = item["expected_answer"]
            exp_nums, pred_nums = _numbers(exp), _numbers(pred)
            num_hit = (len(exp_nums & pred_nums) / len(exp_nums)) if exp_nums else None
            refused = any(m in pred for m in REFUSAL_MARKERS)
            results.append(
                {
                    "qa_id": item["qa_id"],
                    "category": item["category"],
                    "data_origin": item["data_origin"],
                    "negative": negative,
                    "question": item["question"],
                    "expected": exp,
                    "prediction": pred,
                    "num_hit": None if num_hit is None else round(num_hit, 4),
                    "bigram_f1": round(_f1(_bigrams(exp), _bigrams(pred)), 4),
                    "refused": refused,
                    "citations": cits,
                }
            )
            if i % 20 == 0:
                print(f"    …{i}/{len(corpus_sample)}（{time.perf_counter() - t0:.0f}s）", flush=True)
    finally:
        db.close()

    pos = [r for r in results if not r["negative"]]
    neg = [r for r in results if r["negative"]]
    num_vals = [r["num_hit"] for r in pos if r["num_hit"] is not None]
    summary = {
        "tag": args.tag,
        "base_model": args.base,
        "adapter": args.adapter,
        "evaluated": len(results),
        "positives": len(pos),
        "negatives": len(neg),
        "num_hit_mean": round(sum(num_vals) / len(num_vals), 4) if num_vals else None,
        "num_hit_full_rate": round(sum(1 for v in num_vals if v == 1.0) / len(num_vals), 4)
        if num_vals
        else None,
        "bigram_f1_mean": round(sum(r["bigram_f1"] for r in pos) / len(pos), 4) if pos else None,
        "refusal_correct_rate": round(sum(1 for r in neg if r["refused"]) / len(neg), 4) if neg else None,
        "over_refusal_rate_on_positive": round(sum(1 for r in pos if r["refused"]) / len(pos), 4)
        if pos
        else None,
        "elapsed_sec": round(time.perf_counter() - t0, 1),
        "generated_at": datetime.now(UTC).isoformat(),
    }
    path = OUT / f"eval_{args.tag}.json"
    path.write_text(
        json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n[{args.tag}] 评测完成：{json.dumps(summary, ensure_ascii=False)}")
    print(f"结果：{path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
