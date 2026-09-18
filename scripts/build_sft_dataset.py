"""SFT 数据集构建：把全域语料转成"检索接地式"指令-答案对 + 拒答负样本，并导出评测集。

设计要点（为什么这样构造）：
  * **接地式（grounded）问答**：user 消息里带【资料】（来自结构化记录的原文事实），
    要求模型只依据资料作答、数字不得改写、资料不足时必须说"资料不足"。
    这与系统既有"防幻觉三道防线"一致：数字来自数据库直读，模型只组织语言。
  * **拒答负样本**：约 10% 样本给"问 A 却给 B 的资料"，期望答案是明确拒答，
    用于训练"不编造"，评测时也单独统计拒答正确率。
  * **评测集与训练集分离**：`evaluation_qa` 756 条**不参与训练**，只作为微调前后对比的评测集；
    同时导出外部评测集（真实案例/招标门槛）用于检查是否只会背语料。

产物：
  data/sft/agent_sft_train.jsonl   —— 训练样本（chat messages 格式）
  data/sft/agent_eval_corpus.jsonl —— 语料评测集（756 条，含 split 与 category 标记）
  data/sft/agent_eval_external.jsonl —— 外部评测集（真实案例与招标硬门槛）
  data/sft/meta.json               —— 统计、口径与数据边界说明
用法：python -m scripts.build_sft_dataset
"""

from __future__ import annotations

import hashlib
import json
import random
from collections import Counter
from datetime import UTC, datetime

from sqlalchemy import select

from backend.app.core.config import settings
from backend.app.core.db import SessionLocal
from backend.app.models.corpus import CorpusRecord, EvalQA

OUT = settings.repo_root / "data" / "sft"
SYSTEM = (
    "你是智工云枢 ICOPS 的矿山施工智能运营助手。"
    "回答必须严格依据用户提供的【资料】：数字、型号、金额、时间不得改写或推算；"
    "资料中没有的信息，直接说明“资料不足，无法回答”，不得猜测；"
    "回答末尾用括号给出资料出处。"
)
REFUSAL = "资料不足，无法回答。（未在提供的资料中找到相关信息，建议转人工或补充资料）"


def _fmt_yuan(v: float) -> str:
    return f"{v:,.0f} 元"


def _sample(user: str, assistant: str, meta: dict) -> dict:
    return {
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": user},
            {"role": "assistant", "content": assistant},
        ],
        "meta": meta,
    }


def _dedupe(rows: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for r in rows:
        key = hashlib.sha256(json.dumps(r["messages"], ensure_ascii=False).encode()).hexdigest()
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def build_train(records: dict[str, list[dict]]) -> tuple[list[dict], Counter]:
    rows: list[dict] = []
    kinds: Counter = Counter()

    # 1) 设备型号规格（真实公开）
    for m in records.get("equipment_model", []):
        specs = m.get("specifications") or {}
        spec_txt = "；".join(f"{k}={v}" for k, v in specs.items())
        ctx = (
            f"型号 {m.get('model_name')}（{m.get('equipment_subtype')}）；技术规格：{spec_txt}；"
            f"动力系统：{(m.get('configuration') or {}).get('energy_system', '—')}；"
            f"来源：{m.get('source_name')}"
        )
        for q in (
            f"{m.get('model_name')} 的技术规格参数有哪些？",
            f"{m.get('model_name')} 是什么设备、动力类型是什么？",
        ):
            rows.append(
                _sample(
                    f"问题：{q}\n【资料】{ctx}",
                    f"依据资料：{m.get('model_name')} 属于{m.get('equipment_subtype')}，"
                    f"技术规格为 {spec_txt}；动力系统为"
                    f"{(m.get('configuration') or {}).get('energy_system', '资料未给出')}。"
                    f"（来源：{m.get('source_name')}）",
                    {"kind": "equipment_model", "model": m.get("model_name"), "origin": m.get("data_origin")},
                )
            )
        kinds["equipment_model"] += 2

    # 2) 价格与 TCO
    for p in records.get("equipment_price_tco", []):
        ctx = (
            f"型号 {p.get('model_name')}；购置价 {_fmt_yuan(float(p.get('purchase_price_cny') or 0))}；"
            f"能源类型 {p.get('energy_type')}；能源成本 {float(p.get('energy_cost_per_hour_cny') or 0):.2f} 元/小时；"
            f"维保成本 {float(p.get('maintenance_cost_per_hour_cny') or 0):.2f} 元/小时；"
            f"三年残值率 {float(p.get('residual_value_rate_3y') or 0):.2f}；"
            f"年作业小时 {float(p.get('annual_operation_hours') or 0):.0f}；"
            f"三年 TCO {_fmt_yuan(float(p.get('three_year_tco_cny') or 0))}；来源：{p.get('source_name')}"
        )
        rows.append(
            _sample(
                f"问题：{p.get('model_name')} 的购置价和三年 TCO 是多少？\n【资料】{ctx}",
                f"依据资料：{p.get('model_name')} 购置价 {_fmt_yuan(float(p.get('purchase_price_cny') or 0))}，"
                f"三年 TCO 为 {_fmt_yuan(float(p.get('three_year_tco_cny') or 0))}，"
                f"其中能源 {float(p.get('energy_cost_per_hour_cny') or 0):.2f} 元/小时、"
                f"维保 {float(p.get('maintenance_cost_per_hour_cny') or 0):.2f} 元/小时。"
                f"（来源：{p.get('source_name')}；价格为示例口径，正式报价需厂商确认）",
                {"kind": "equipment_price_tco", "model": p.get("model_name"), "origin": "simulated_fallback"},
            )
        )
        kinds["equipment_price_tco"] += 1

    # 3) 故障案例（诊断/维修）
    for c in records.get("fault_case", []):
        steps = " → ".join(c.get("repair_steps") or [])
        parts = "、".join(c.get("recommended_parts") or [])
        ctx = (
            f"故障码 {c.get('fault_code')}（{c.get('fault_type')}）；适用设备 {c.get('equipment_subtype')}；"
            f"异常部件 {c.get('abnormal_part')}；根因 {c.get('root_cause')}；严重度 {c.get('severity')}；"
            f"维修步骤：{steps}；推荐备件：{parts}；预计停机 {float(c.get('estimated_downtime_hours') or 0):.0f} 小时；"
            f"保养间隔 {c.get('maintenance_interval_hours')} 小时；来源：{c.get('source_name')}"
        )
        rows.append(
            _sample(
                f"问题：{c.get('equipment_subtype')}出现 {c.get('fault_type')}（{c.get('fault_code')}）怎么处理？\n【资料】{ctx}",
                f"依据资料：{c.get('fault_code')} 对应 {c.get('fault_type')}，异常部件为 {c.get('abnormal_part')}，"
                f"根因是 {c.get('root_cause')}。处理步骤：{steps}。建议备件：{parts}。"
                f"预计停机 {float(c.get('estimated_downtime_hours') or 0):.0f} 小时，"
                f"保养间隔 {c.get('maintenance_interval_hours')} 小时。（来源：{c.get('source_name')}）",
                {"kind": "fault_case", "code": c.get("fault_code"), "origin": c.get("data_origin")},
            )
        )
        rows.append(
            _sample(
                f"问题：{c.get('fault_code')} 需要准备哪些备件？\n【资料】{ctx}",
                f"依据资料：{c.get('fault_code')}（{c.get('fault_type')}）建议备件为 {parts}。"
                f"（来源：{c.get('source_name')}）",
                {"kind": "fault_case", "code": c.get("fault_code"), "origin": c.get("data_origin")},
            )
        )
        kinds["fault_case"] += 2

    # 4) 项目运营记录（工期/成本事实查询）
    for o in records.get("mining_project_operation", []):
        ctx = (
            f"项目 {o.get('project_id')}（{o.get('project_name')}）；区域 {o.get('region')}；"
            f"工序 {o.get('construction_task')}；工程量 {float(o.get('engineering_quantity_t') or 0):,.0f} 吨；"
            f"计划工期 {o.get('planned_duration_days')} 天；实际工期 {o.get('actual_duration_days')} 天；"
            f"工期偏差 {o.get('schedule_deviation_days')} 天；预算 {_fmt_yuan(float(o.get('budget_cny') or 0))}；"
            f"实际成本 {_fmt_yuan(float(o.get('actual_cost_cny') or 0))}；设备数 {o.get('equipment_count')}；"
            f"状态 {o.get('task_status')}；来源：{o.get('source_name')}"
        )
        rows.append(
            _sample(
                f"问题：项目 {o.get('project_id')} 的工期偏差是多少天？\n【资料】{ctx}",
                f"依据资料：项目 {o.get('project_id')} 计划工期 {o.get('planned_duration_days')} 天、"
                f"实际 {o.get('actual_duration_days')} 天，工期偏差 {o.get('schedule_deviation_days')} 天。"
                f"（来源：{o.get('source_name')}）",
                {"kind": "proj_operation", "project": o.get("project_id"), "origin": o.get("data_origin")},
            )
        )
        rows.append(
            _sample(
                f"问题：项目 {o.get('project_id')} 的预算和实际成本各是多少？\n【资料】{ctx}",
                f"依据资料：预算 {_fmt_yuan(float(o.get('budget_cny') or 0))}，"
                f"实际成本 {_fmt_yuan(float(o.get('actual_cost_cny') or 0))}。"
                f"（来源：{o.get('source_name')}）",
                {"kind": "proj_operation", "project": o.get("project_id"), "origin": o.get("data_origin")},
            )
        )
        kinds["proj_operation"] += 2

    # 5) 方案模板
    seen_tpl: set[str] = set()
    for t in records.get("project_template", []):
        ttype = str(t.get("template_type"))
        if ttype in seen_tpl:
            continue
        seen_tpl.add(ttype)
        chapters = t.get("chapters") or []
        chap_txt = "；".join(f"{ch.get('order')}.{ch.get('name')}" for ch in chapters)
        ctx = f"模板类型 {ttype}；章节结构：{chap_txt}；来源：{t.get('source_name')}"
        rows.append(
            _sample(
                f"问题：{ttype}包含哪些章节？\n【资料】{ctx}",
                f"依据资料：{ttype}共 {len(chapters)} 章，依次为 {chap_txt}。（来源：{t.get('source_name')}）",
                {"kind": "project_template", "template_type": ttype, "origin": t.get("data_origin")},
            )
        )
        kinds["project_template"] += 1

    # 6) 客户与合同域
    contracts = {str(c.get("customer_id")): c for c in records.get("customer_contract", [])}
    for c in records.get("customer", []):
        con = contracts.get(str(c.get("customer_id")), {})
        ctx = (
            f"客户 {c.get('customer_name')}；行业 {c.get('industry')}；服务等级 {c.get('service_level')}；"
            f"联系人 {c.get('contact_person')}；设备清单 {'、'.join(c.get('equipment_inventory') or [])}；"
            f"合同 {con.get('contract_name', '—')}；合同金额 {_fmt_yuan(float(con.get('contract_amount_cny') or 0))}；"
            f"合同期 {con.get('contract_start', '—')} ~ {con.get('contract_end', '—')}；"
            f"来源：{c.get('source_name')}"
        )
        rows.append(
            _sample(
                f"问题：{c.get('customer_name')} 的服务等级和设备清单是什么？\n【资料】{ctx}",
                f"依据资料：{c.get('customer_name')}（{c.get('industry')}）服务等级为 {c.get('service_level')}，"
                f"设备清单包含 {'、'.join(c.get('equipment_inventory') or [])}。"
                f"（来源：{c.get('source_name')}；客户数据为仿真）",
                {"kind": "customer", "customer": c.get("customer_id"), "origin": c.get("data_origin")},
            )
        )
        kinds["customer"] += 1

    # 7) 设备实例
    for i in records.get("equipment_instance", []):
        loc = i.get("location") or {}
        ctx = (
            f"设备 {i.get('device_id')}；型号 {i.get('model_name')}（{i.get('equipment_subtype')}）；"
            f"状态 {i.get('status')}；故障码 {i.get('fault_code') or '无'}；"
            f"出厂日期 {i.get('manufacture_date')}；位置 经度 {loc.get('longitude')} 纬度 {loc.get('latitude')}；"
            f"来源：{i.get('source_name')}"
        )
        rows.append(
            _sample(
                f"问题：设备 {i.get('device_id')} 是什么型号、当前状态如何？\n【资料】{ctx}",
                f"依据资料：设备 {i.get('device_id')} 型号为 {i.get('model_name')}（{i.get('equipment_subtype')}），"
                f"当前状态 {i.get('status')}。（来源：{i.get('source_name')}）",
                {"kind": "equipment_instance", "device": i.get("device_id"), "origin": i.get("data_origin")},
            )
        )
        kinds["equipment_instance"] += 1

    return _dedupe(rows), kinds


def add_refusal_samples(
    rows: list[dict], records: dict[str, list[dict]], ratio: float = 0.1, seed: int = 20260918
) -> tuple[list[dict], int]:
    """构造拒答负样本：问题属于某类，但【资料】给的是另一条无关记录 → 期望明确拒答。"""
    rnd = random.Random(seed)
    pool = [r for r in rows if r["meta"]["kind"] in ("equipment_model", "equipment_price_tco", "fault_case")]
    if not pool:
        return rows, 0
    n = max(1, int(len(rows) * ratio))
    added: list[dict] = []
    for _ in range(n):
        src = rnd.choice(pool)
        other = rnd.choice([r for r in pool if r["meta"] != src["meta"]] or pool)
        question = src["messages"][1]["content"].split("\n")[0]
        other_ctx = other["messages"][1]["content"].split("【资料】", 1)[-1]
        added.append(
            _sample(
                f"{question}\n【资料】{other_ctx}",
                REFUSAL,
                {
                    "kind": "refusal",
                    "based_on": src["meta"].get("model") or src["meta"].get("code") or "",
                    "origin": "synthetic_negative",
                },
            )
        )
    return rows + added, len(added)


def _load_records(db) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for et, payload in db.query(CorpusRecord.entity_type, CorpusRecord.payload).all():
        obj = json.loads(payload) if isinstance(payload, str) else payload
        out.setdefault(et, []).append(obj)
    return out


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    db = SessionLocal()
    try:
        records = _load_records(db)
        train_rows, kinds = build_train(records)
        train_rows, n_refusal = add_refusal_samples(train_rows, records)
        kinds["refusal"] = n_refusal

        eval_rows = []
        for qa in db.scalars(select(EvalQA)).all():
            eval_rows.append(
                {
                    "qa_id": qa.qa_id,
                    "split": qa.split,
                    "category": qa.category,
                    "data_origin": qa.data_origin,
                    "source_name": qa.source_name,
                    "question": qa.question,
                    "expected_answer": qa.expected_answer,
                    "reference_source": qa.reference_source,
                }
            )
    finally:
        db.close()

    ext_path = settings.repo_root / "data" / "validation" / "real_cases.json"
    external = []
    if ext_path.exists():
        cases = json.loads(ext_path.read_text(encoding="utf-8"))["cases"]
        for c in cases:
            req = c["requirement"]
            facts = [f"客户：{c['customer']}", f"需求：{req.get('raw', '')}"]
            if req.get("owner_mandatory_specs"):
                sp = req["owner_mandatory_specs"]
                facts.append(
                    "招标强制门槛：采装设备斗容 ≥"
                    f"{sp['excavator']['bucket_m3_min']} m³ 且 ≥"
                    f"{sp['excavator']['count_min']} 台；运输设备载重 ≥{sp['truck']['rated_load_t_min']} t、"
                    f"无人 ≥{sp['truck']['unmanned_count_min']} 台 + 有人 ≥{sp['truck']['manned_count_min']} 台"
                )
            if req.get("duration_years"):
                facts.append(f"工期 {req['duration_years']} 年")
            external.append(
                {
                    "case_id": c["id"],
                    "title": c["title"],
                    "source_tier": c["source_tier"],
                    "source_url": c["source_url"],
                    "context": "；".join(facts),
                    "question": f"{c['title']} 的客户需求与关键约束是什么？",
                    "expected_keywords": [k for k in (req.get("constraints") or []) if isinstance(k, str)],
                    "comparability": c["comparability"],
                }
            )

    with (OUT / "agent_sft_train.jsonl").open("w", encoding="utf-8") as fh:
        for r in train_rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    with (OUT / "agent_eval_corpus.jsonl").open("w", encoding="utf-8") as fh:
        for r in eval_rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    with (OUT / "agent_eval_external.jsonl").open("w", encoding="utf-8") as fh:
        for r in external:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    cat = Counter(r["category"] for r in eval_rows)
    origin = Counter(r["data_origin"] for r in eval_rows)
    meta = {
        "generated_at": datetime.now(UTC).isoformat(),
        "system_prompt": SYSTEM,
        "train": {"n": len(train_rows), "by_kind": dict(kinds)},
        "eval_corpus": {"n": len(eval_rows), "categories": dict(cat), "origins": dict(origin)},
        "eval_external": {"n": len(external), "tiers": dict(Counter(r["source_tier"] for r in external))},
        "protocol": {
            "grounded": "user 消息内嵌【资料】，要求模型仅依据资料作答、数字不得改写",
            "refusal_negative": f"拒答负样本 {n_refusal} 条（问题与资料不匹配 → 期望明确拒答）",
            "eval_separation": "evaluation_qa 756 条不参与训练，仅作微调前后对比评测",
            "same_source_caveat": "语料评测集与训练集同源（同一批结构化记录的不同问法），"
            "分数只能说明事实一致性与格式合规，不能说明泛化能力；"
            "外部评测集来自真实招标/工程案例，用于检查泛化与边界行为",
        },
        "data_boundary": [
            "设备型号规格为真实公开产品页数据；价格/TCO、故障案例、项目运营、客户与合同为仿真构造，"
            "所有答案模板均带“来源”字段，微调后输出必须保留出处。",
            "训练样本由确定性模板生成，不含人工标注；因此只能教“接地事实表达与拒答”，不能教推理能力。",
        ],
    }
    (OUT / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print("[build_sft_dataset] 完成：")
    print(f"  训练样本 {len(train_rows)} 条（按类型 {dict(kinds)}）")
    print(f"  语料评测集 {len(eval_rows)} 条（类别 {dict(cat)}；来源 {dict(origin)}）")
    print(f"  外部评测集 {len(external)} 条（层级 {dict(Counter(r['source_tier'] for r in external))}）")
    print(f"  产物目录：{OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
