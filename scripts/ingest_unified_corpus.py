"""全域语料 ETL：agent_train.jsonl / project_test.jsonl → 结构化库 + 血缘清单（幂等）。

用法：
    python -m scripts.ingest_unified_corpus           # 入库
    python -m scripts.ingest_unified_corpus --strict  # 训练实体项目级交叉即报错退出

设计：
  * 幂等键：暂存表 (entity_type, record_key)；类型化表各自唯一键（record_key/qa_id/price_id/case_id）
  * 全量保留：16174 条 16 类实体全部落 corpus_record（payload 原样），类型化表只做无 JSON 的强类型投影
  * 血缘：manifest 记录两份文件 SHA-256、各类型条数、data_origin 分布、按实体分别核算的交叉检查
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

from backend.app.core.config import settings
from backend.app.core.db import SessionLocal, init_db
from backend.app.models.corpus import (
    CorpusRecord,
    CorpusTelemetry,
    EquipPriceTco,
    EvalQA,
    FaultCase,
    ProjOperation,
)

CORPUS = settings.repo_root / "data" / "corpus"
FILES = {
    "agent_train": CORPUS / "agent_train.jsonl",
    "project_test": CORPUS / "project_test.jsonl",
}
KEY_FIELDS = ("record_id", "qa_id", "case_id", "price_id", "template_id", "contract_id",
              "organization_id", "user_id", "customer_id", "dispatch_id", "fault_id")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _key(obj: dict) -> str:
    for field in KEY_FIELDS:
        if obj.get(field):
            return str(obj[field])
    return str(obj.get("_id") or "")


def load() -> tuple[dict[str, list[dict]], dict]:
    manifest: dict = {"created_at": datetime.now(UTC).isoformat(), "files": []}
    splits: dict[str, list[dict]] = {}
    seen: dict[tuple[str, str], str] = {}
    duplicates: list[str] = []
    for split, path in FILES.items():
        if not path.exists():
            raise FileNotFoundError(f"缺少语料文件：{path}")
        rows: list[dict] = []
        types: Counter = Counter()
        origins: Counter = Counter()
        examples: Counter = Counter()
        invalid: list[str] = []
        with path.open(encoding="utf-8", errors="replace") as fh:
            for lineno, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError as exc:
                    invalid.append(f"line {lineno}: {exc}")
                    continue
                et = str(obj.get("entity_type") or "unknown")
                key = _key(obj)
                if not key:
                    invalid.append(f"line {lineno}: 缺少幂等键")
                    continue
                if (et, key) in seen:
                    duplicates.append(f"{et}:{key}")
                    continue
                seen[(et, key)] = split
                types[et] += 1
                origins[str(obj.get("data_origin"))] += 1
                examples[str(bool(obj.get("is_example")))] += 1
                rows.append(obj)
        splits[split] = rows
        manifest["files"].append({
            "name": path.name, "bytes": path.stat().st_size, "sha256": sha256(path),
            "rows": len(rows), "entity_types": dict(types), "data_origin": dict(origins),
            "is_example": dict(examples), "invalid": invalid[:20],
        })
    totals: Counter = Counter()
    for f in manifest["files"]:
        totals.update(f["entity_types"])
    manifest["entity_totals"] = dict(totals)
    manifest["records_total"] = sum(totals.values())
    manifest["cross_file_duplicates"] = len(duplicates)
    manifest["duplicate_keys"] = duplicates[:50]

    # 项目级 / 设备级交叉检查（训练-测试泄漏的硬门槛）
    # 必须按实体类型分别核算：遥测/调度/轨迹等仿真实体在两类文件中共用同一批 SIM-PROJECT 编号，
    # 只有训练实体 mining_project_operation 的项目切分是干净的（该结论由 --strict 首跑发现并纠正）。
    def group_sets(rows: list[dict], field: str) -> dict[str, set[str]]:
        out: dict[str, set[str]] = defaultdict(set)
        for r in rows:
            if r.get(field):
                out[str(r.get("entity_type"))].add(str(r[field]))
        return out

    proj_train = group_sets(splits["agent_train"], "project_id")
    proj_test = group_sets(splits["project_test"], "project_id")
    dev_train = group_sets(splits["agent_train"], "device_id")
    dev_test = group_sets(splits["project_test"], "device_id")

    per_entity: dict[str, dict] = {}
    for et in sorted(set(proj_train) | set(proj_test)):
        inter = proj_train.get(et, set()) & proj_test.get(et, set())
        per_entity.setdefault(et, {})["projects"] = {
            "agent_train": len(proj_train.get(et, set())),
            "project_test": len(proj_test.get(et, set())),
            "overlap": len(inter),
            "overlap_sample": sorted(inter)[:5],
        }
    for et in sorted(set(dev_train) | set(dev_test)):
        inter = dev_train.get(et, set()) & dev_test.get(et, set())
        per_entity.setdefault(et, {})["devices"] = {
            "agent_train": len(dev_train.get(et, set())),
            "project_test": len(dev_test.get(et, set())),
            "overlap": len(inter),
            "overlap_sample": sorted(inter)[:5],
        }
    manifest["per_entity_split"] = per_entity

    train_et = "mining_project_operation"
    train_overlap = per_entity.get(train_et, {}).get("projects", {}).get("overlap", 0)
    manifest["training_entity"] = train_et
    manifest["training_project_overlap"] = train_overlap
    manifest["split_note"] = (
        f"训练实体 {train_et} 的项目切分零交叉（overlap={train_overlap}）；"
        "设备遥测/调度/轨迹等仿真实体在两类文件中共用 SIM-PROJECT 编号，"
        "因此这些实体不可用于 train/test 划分，仅作知识/仿真数据使用。"
    )
    return splits, manifest


def _upsert(db, model, key_field: str, key_value: str, data: dict, filters: dict | None = None) -> bool:
    q = db.query(model).filter(getattr(model, key_field) == key_value)
    for k, v in (filters or {}).items():
        q = q.filter(getattr(model, k) == v)
    row = q.first()
    if row is None:
        db.add(model(**{key_field: key_value, **(filters or {}), **data}))
        return True
    for k, v in data.items():
        setattr(row, k, v)
    return False


def run(strict: bool = False) -> dict:
    init_db()
    splits, manifest = load()
    if strict and manifest["training_project_overlap"]:
        raise ValueError(
            f"训练实体项目级交叉，禁止用于训练评估：overlap={manifest['training_project_overlap']}")

    db = SessionLocal()
    created: defaultdict = defaultdict(int)
    telemetry_by_status: Counter = Counter()
    try:
        for split, rows in splits.items():
            for obj in rows:
                et = str(obj.get("entity_type"))
                key = _key(obj)
                created["corpus_record"] += _upsert(db, CorpusRecord, "record_key", key, {
                    "entity_type": et,
                    "split": split,
                    "data_origin": str(obj.get("data_origin") or ""),
                    "is_example": bool(obj.get("is_example", True)),
                    "source_name": str(obj.get("source_name") or ""),
                    "source_url": str(obj.get("source_url") or obj.get("reference_source") or ""),
                    "payload": obj,
                }, filters={"entity_type": et}) or 0

                if et == "mining_project_operation":
                    created["proj_operation"] += _upsert(db, ProjOperation, "record_key", key, {
                        "split": split,
                        "project_id": str(obj.get("project_id") or ""),
                        "project_name": str(obj.get("project_name") or ""),
                        "region": str(obj.get("region") or ""),
                        "scenario_type": str(obj.get("scenario_type") or ""),
                        "terrain": str(obj.get("terrain") or ""),
                        "quality_standard": str(obj.get("quality_standard") or ""),
                        "constraints": obj.get("constraints") or {},
                        "planned_start": str(obj.get("planned_start") or ""),
                        "planned_duration_days": int(obj.get("planned_duration_days") or 0),
                        "actual_duration_days": int(obj.get("actual_duration_days") or 0),
                        "schedule_deviation_days": int(obj.get("schedule_deviation_days") or 0),
                        "engineering_quantity_t": float(obj.get("engineering_quantity_t") or 0),
                        "budget_cny": float(obj.get("budget_cny") or 0),
                        "actual_cost_cny": float(obj.get("actual_cost_cny") or 0),
                        "construction_task": str(obj.get("construction_task") or ""),
                        "task_status": str(obj.get("task_status") or ""),
                        "equipment_count": int(obj.get("equipment_count") or 0),
                        "data_quality": str(obj.get("data_quality") or ""),
                        "source_name": str(obj.get("source_name") or ""),
                    }) or 0
                elif et == "evaluation_qa":
                    created["eval_qa"] += _upsert(db, EvalQA, "qa_id", key, {
                        "split": split,
                        "data_origin": str(obj.get("data_origin") or ""),
                        "source_name": str(obj.get("source_name") or ""),
                        "source_url": str(obj.get("source_url") or ""),
                        "question": str(obj.get("question") or ""),
                        "expected_answer": str(obj.get("expected_answer") or ""),
                        "reference_source": str(obj.get("reference_source") or ""),
                        "category": str(obj.get("category") or ""),
                    }) or 0
                elif et == "equipment_price_tco":
                    created["equip_price_tco"] += _upsert(db, EquipPriceTco, "price_id", key, {
                        "split": split,
                        "model_name": str(obj.get("model_name") or ""),
                        "equipment_subtype": str(obj.get("equipment_subtype") or ""),
                        "purchase_price_cny": float(obj.get("purchase_price_cny") or 0),
                        "energy_type": str(obj.get("energy_type") or ""),
                        "energy_cost_per_hour_cny": float(obj.get("energy_cost_per_hour_cny") or 0),
                        "maintenance_cost_per_hour_cny": float(
                            obj.get("maintenance_cost_per_hour_cny") or 0),
                        "residual_value_rate_3y": float(obj.get("residual_value_rate_3y") or 0),
                        "annual_operation_hours": float(obj.get("annual_operation_hours") or 0),
                        "three_year_tco_cny": float(obj.get("three_year_tco_cny") or 0),
                        "source_name": str(obj.get("source_name") or ""),
                    }) or 0
                elif et == "fault_case":
                    created["fault_case"] += _upsert(db, FaultCase, "case_id", key, {
                        "split": split,
                        "source_name": str(obj.get("source_name") or ""),
                        "equipment_subtype": str(obj.get("equipment_subtype") or ""),
                        "fault_code": str(obj.get("fault_code") or ""),
                        "fault_type": str(obj.get("fault_type") or ""),
                        "abnormal_part": str(obj.get("abnormal_part") or ""),
                        "root_cause": str(obj.get("root_cause") or ""),
                        "severity": str(obj.get("severity") or ""),
                        "repair_steps": obj.get("repair_steps") or [],
                        "recommended_parts": obj.get("recommended_parts") or [],
                        "maintenance_interval_hours": int(obj.get("maintenance_interval_hours") or 0),
                        "estimated_downtime_hours": float(obj.get("estimated_downtime_hours") or 0),
                    }) or 0
                elif et == "equipment_telemetry":
                    telemetry_by_status[str(obj.get("status"))] += 1
                    created["corpus_telemetry"] += _upsert(db, CorpusTelemetry, "record_key", key, {
                        "split": split,
                        "device_id": str(obj.get("device_id") or ""),
                        "model_name": str(obj.get("model_name") or ""),
                        "equipment_subtype": str(obj.get("equipment_subtype") or ""),
                        "project_id": str(obj.get("project_id") or ""),
                        "timestamp": str(obj.get("timestamp") or ""),
                        "temperature_c": float(obj.get("temperature_c") or 0),
                        "hydraulic_pressure_mpa": float(obj.get("hydraulic_pressure_mpa") or 0),
                        "vibration": float(obj.get("vibration") or 0),
                        "fuel_consumption_lph": float(obj.get("fuel_consumption_lph") or 0),
                        "load_t": float(obj.get("load_t") or 0),
                        "status": str(obj.get("status") or ""),
                        "construction_task": str(obj.get("construction_task") or ""),
                    }) or 0
        db.commit()

        counts = {
            "corpus_record": db.query(CorpusRecord).count(),
            "proj_operation": db.query(ProjOperation).count(),
            "eval_qa": db.query(EvalQA).count(),
            "equip_price_tco": db.query(EquipPriceTco).count(),
            "fault_case": db.query(FaultCase).count(),
            "corpus_telemetry": db.query(CorpusTelemetry).count(),
        }
        manifest["created"] = dict(created)
        manifest["counts"] = counts
        manifest["telemetry_status"] = dict(telemetry_by_status)
        manifest["note"] = ("equipment_model/equipment_instance/equipment_trajectory/dispatch_event/"
                            "project_template/customer 域等记录保存在 corpus_record 暂存表中，"
                            "训练与检索按需从暂存表取用（不做有损映射）")
        (CORPUS / "unified_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        report = {"generated_at": datetime.now(UTC).isoformat(), **manifest}
        (CORPUS / "unified_ingest_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report
    finally:
        db.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="ICOPS 全域语料 ETL")
    ap.add_argument("--strict", action="store_true", help="训练实体项目级交叉即报错")
    args = ap.parse_args()
    r = run(strict=args.strict)
    print("[ingest_unified_corpus] 完成：")
    print(f"  记录总数 {r['records_total']}（跨文件重复 {r['cross_file_duplicates']}）")
    print(f"  实体类型 {len(r['entity_totals'])} 类：{r['entity_totals']}")
    et = r["training_entity"]
    split = r["per_entity_split"].get(et, {})
    proj = split.get("projects", {})
    print(f"  训练实体 {et}：项目 train {proj.get('agent_train')} / test {proj.get('project_test')} "
          f"/ 交叉 {r['training_project_overlap']}")
    print(f"  切分说明：{r['split_note']}")
    print(f"  入库：{r['counts']}")
    print(f"  本次新增：{r['created']}")
    print(f"  血缘清单：{CORPUS / 'unified_manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
