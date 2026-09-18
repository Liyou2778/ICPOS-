"""Agent QLoRA/LoRA 微调：在 8GB 显存上微调小参数量指令模型。

硬件与选型（实测环境：RTX 4060 Laptop 8GB）：
  * 默认基座 Qwen2.5-1.5B-Instruct（bf16 权重约 3GB，LoRA 训练峰值 < 6GB，8GB 可跑）
  * 若 bitsandbytes 可用则走 4-bit QLoRA（更省显存）；不可用自动降级 bf16 LoRA
  * 序列长度 1024、batch 1 × 梯度累积 8、梯度检查点开启

合规与可复现：
  * 基座模型通过 HF 镜像下载（HF_ENDPOINT=https://hf-mirror.com，直连 huggingface.co 在本机不可达）
  * 训练数据 data/sft/agent_sft_train.jsonl（由确定性模板生成，含拒答负样本），
    评测数据 data/sft/agent_eval_*.jsonl **不参与训练**
  * 产物：data/models/agent_sft/{adapter/,train_log.json,train_config.json}

用法：
    python -m scripts.train_agent_sft --dry-run          # 只做数据与配置校验，不加载模型
    python -m scripts.train_agent_sft --epochs 2         # 正式微调（后台运行）
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import UTC, datetime

from backend.app.core.config import settings

OUT = settings.repo_root / "data" / "models" / "agent_sft"
SFT = settings.repo_root / "data" / "sft"
DEFAULT_BASE = "Qwen/Qwen2.5-1.5B-Instruct"
MAX_LEN = 1024


def _load_jsonl(path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def dry_run() -> int:
    train = _load_jsonl(SFT / "agent_sft_train.jsonl")
    eval_corpus = _load_jsonl(SFT / "agent_eval_corpus.jsonl")
    eval_ext = _load_jsonl(SFT / "agent_eval_external.jsonl")
    kinds: dict[str, int] = {}
    for r in train:
        k = r["meta"]["kind"]
        kinds[k] = kinds.get(k, 0) + 1
    lengths = [sum(len(m["content"]) for m in r["messages"]) for r in train]
    print("[train_agent_sft] dry-run")
    print(f"  训练样本 {len(train)}；类型分布 {kinds}")
    print(f"  字符长度：中位 {sorted(lengths)[len(lengths) // 2]} / 最大 {max(lengths)}")
    print(f"  评测集：语料 {len(eval_corpus)} 条 / 外部 {len(eval_ext)} 条")
    print(f"  基座（默认）{DEFAULT_BASE}；max_len {MAX_LEN}")
    over = [n for n in lengths if n > MAX_LEN * 2]
    print(f"  超长样本（> {MAX_LEN * 2} 字符，将被截断）：{len(over)} 条")
    return 0


def train(
    base: str,
    epochs: float,
    lr: float,
    batch: int,
    grad_accum: int,
    max_len: int,
    limit: int | None,
    use_4bit: bool,
) -> int:
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

    import torch
    from datasets import Dataset
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import (
        AutoModelForCausalLM,
        AutoTokenizer,
        DataCollatorForSeq2Seq,
        Trainer,
        TrainingArguments,
    )

    OUT.mkdir(parents=True, exist_ok=True)
    rows = _load_jsonl(SFT / "agent_sft_train.jsonl")
    if limit:
        rows = rows[:limit]
    tok = AutoTokenizer.from_pretrained(base, trust_remote_code=False)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token

    def encode(rec: dict) -> dict:
        msgs = rec["messages"]
        text = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=False)
        out = tok(text, truncation=True, max_length=max_len, padding=False)
        out["labels"] = list(out["input_ids"])
        return out

    ds = Dataset.from_list(rows).map(encode, remove_columns=["messages", "meta"])

    dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    kwargs: dict = {"torch_dtype": dtype, "device_map": {"": 0}}
    quantized = False
    if use_4bit:
        try:
            from transformers import BitsAndBytesConfig

            kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=dtype,
                bnb_4bit_use_double_quant=True,
            )
            quantized = True
        except Exception as exc:  # noqa: BLE001
            print(f"  4-bit 量化不可用（{exc}），降级 bf16 LoRA")
    # transformers 5.x 推荐 dtype=，旧版用 torch_dtype=；两者都试以兼容
    try:
        model = AutoModelForCausalLM.from_pretrained(base, dtype=dtype, **kwargs)
    except TypeError:
        model = AutoModelForCausalLM.from_pretrained(base, torch_dtype=dtype, **kwargs)
    model.config.use_cache = False
    if quantized:
        model = prepare_model_for_kbit_training(model)
    model.gradient_checkpointing_enable()

    lora = LoraConfig(
        r=16,
        lora_alpha=32,
        lora_dropout=0.05,
        bias="none",
        task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
    )
    model = get_peft_model(model, lora)
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    # 4-bit 量化后 parameters() 的 numel 是打包存储单元数，不代表真实参数量；
    # 用基座配置估算真实参数量，避免对外报出误导性数字。
    cfg = model.config
    hidden, layers = cfg.hidden_size, cfg.num_hidden_layers
    inter, vocab = cfg.intermediate_size, cfg.vocab_size
    base_params = layers * (4 * hidden * hidden + 3 * hidden * inter + 2 * hidden) + vocab * hidden
    print(f"  可训练参数（LoRA）{trainable:,}")
    print(f"  基座真实参数量 ≈ {base_params / 1e9:.2f} B"
          f"（{'4-bit 量化加载' if quantized else 'bf16 加载'}）")

    args = TrainingArguments(
        output_dir=str(OUT / "checkpoints"),
        num_train_epochs=epochs,
        per_device_train_batch_size=batch,
        gradient_accumulation_steps=grad_accum,
        learning_rate=lr,
        lr_scheduler_type="cosine",
        warmup_steps=50,  # transformers 5.x 已移除 warmup_ratio
        logging_steps=20,
        save_strategy="no",
        bf16=(dtype == torch.bfloat16),
        fp16=(dtype != torch.bfloat16),
        optim="paged_adamw_8bit" if quantized else "adamw_torch",
        gradient_checkpointing=True,
        report_to=[],
        seed=20260918,
        dataloader_num_workers=0,
    )
    collator = DataCollatorForSeq2Seq(tok, padding=True, label_pad_token_id=-100)
    trainer = Trainer(model=model, args=args, train_dataset=ds, data_collator=collator)

    t0 = time.perf_counter()
    result = trainer.train()
    elapsed = time.perf_counter() - t0

    adapter_dir = OUT / "adapter"
    model.save_pretrained(str(adapter_dir))
    tok.save_pretrained(str(adapter_dir))
    log = {
        "base_model": base,
        "quantized_4bit": quantized,
        "epochs": epochs,
        "lr": lr,
        "batch": batch,
        "grad_accum": grad_accum,
        "max_len": max_len,
        "samples": len(rows),
        "trainable_params": int(trainable),
        "base_params_est": int(base_params),
        "quantized_storage_units": int(sum(p.numel() for p in model.parameters())),
        "elapsed_sec": round(elapsed, 1),
        "train_runtime_sec": round(result.metrics.get("train_runtime", 0), 1),
        "train_loss": round(float(result.metrics.get("train_loss", 0)), 4),
        "samples_per_sec": round(len(rows) * epochs / max(elapsed, 1e-6), 2),
        "finished_at": datetime.now(UTC).isoformat(),
        "adapter_dir": str(adapter_dir),
    }
    (OUT / "train_log.json").write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "train_config.json").write_text(
        json.dumps(
            {
                "base": base,
                "max_len": max_len,
                "lora_r": 16,
                "lora_alpha": 32,
                "target_modules": lora.target_modules,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(
        f"  训练完成：loss {log['train_loss']}，耗时 {log['elapsed_sec']}s，{log['samples_per_sec']} 样本/秒"
    )
    print(f"  adapter：{adapter_dir}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Agent LoRA/QLoRA 微调")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--epochs", type=float, default=2.0)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--batch", type=int, default=1)
    ap.add_argument("--grad-accum", type=int, default=8)
    ap.add_argument("--max-len", type=int, default=MAX_LEN)
    ap.add_argument("--limit", type=int, default=None, help="仅用前 N 条（冒烟用）")
    ap.add_argument("--no-4bit", action="store_true")
    args = ap.parse_args()
    if args.dry_run:
        return dry_run()
    return train(
        args.base,
        args.epochs,
        args.lr,
        args.batch,
        args.grad_accum,
        args.max_len,
        args.limit,
        not args.no_4bit,
    )


if __name__ == "__main__":
    raise SystemExit(main())
