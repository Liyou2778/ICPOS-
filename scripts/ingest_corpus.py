"""企业级语料 ETL：把 data/corpus/{train,test}.jsonl 清洗、校验、入库、导出训练集。

产出：
  1) 设备参数库扩充：dev_equipment_model —— equipment + equipment_spec 映射
     （斗容/额定载重/功率/整备质量/油耗；价格与维保语料未含，保持示例口径）
  2) 知识库扩充：document/chunk/process_knowledge/template_section
     → kb_knowledge_entry + 向量库（Chroma / 内置）
  3) 训练数据集：data/simulated/corpus_telemetry.csv（遥测 ⋈ 轨迹）、corpus_faults.csv（故障真值）
  4) 站点/设备/基线：data/simulated/corpus_meta.json
  5) 质检报告：data/corpus/ingest_report.json

用法：
  python -m scripts.ingest_corpus [--skip-vectors] [--limit N]
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from typing import Any

import pandas as pd

from backend.app.core.config import settings
from backend.app.core.db import SessionLocal, init_db
from backend.app.models import EquipmentModel, KnowledgeEntry
from backend.app.services import rag
from backend.app.services.vectorstore import vector_store

ROOT = settings.repo_root
CORPUS_DIR = ROOT / "data" / "corpus"
SIM_DIR = ROOT / "data" / "simulated"
FILES = {"train": CORPUS_DIR / "train.jsonl", "test": CORPUS_DIR / "test.jsonl"}

# ---------- 品类/场景映射 ----------
CATEGORY_MAP = [
    (("挖掘机", "挖机"), "excavator", "挖掘机"),
    (("矿卡", "矿用自卸", "自卸车", "矿用卡车"), "mining_truck", "矿用自卸车"),
    (("装载机",), "loader", "装载机"),
    (("推土机",), "dozer", "推土机"),
    (("压路机",), "roller", "压路机"),
    (("摊铺机",), "paver", "摊铺机"),
    (("铣刨机",), "milling", "铣刨机"),
    (("起重机", "吊车"), "crane", "起重机"),
    (("钻机",), "drill", "钻机"),
    (("泵车", "车载泵"), "pump", "泵车"),
    (("洒水车", "喷洒车"), "water_truck", "洒水/喷洒车"),
    (("运输车", "牵引车", "物流车"), "transport", "运输车"),
]
EARTHWORK_HINT = ("土方", "路面", "养护", "市政", "装载", "摊铺", "压路", "铣刨", "除雪", "绿化")

SPEC_FIELDS = {
    "bucket_m3": ("铲斗容量", "斗容", "铲斗容积"),
    "rated_load_t": ("额定载重", "载重量", "额定载荷", "装载量"),
    "power_kw": ("发动机功率", "额定功率", "总功率", "电机功率", "功率"),
    "fuel_lh": ("油耗",),
    "mass_kg": ("整备质量", "工作质量", "整机质量"),
}


def map_category(cat: str) -> tuple[str, str]:
    for keys, code, cn in CATEGORY_MAP:
        if any(k in cat for k in keys):
            return code, cn
    return "other", cat or "其他设备"


def map_scene(is_mining: Any, cat: str) -> str:
    if is_mining == 1:
        return "mining"
    if any(k in (cat or "") for k in EARTHWORK_HINT):
        return "earthwork"
    return "other"


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", "").strip())
    except ValueError:
        return None


def pick_spec(specs: dict[str, dict], field: str) -> tuple[float | None, str]:
    keys = SPEC_FIELDS[field]
    for item, rec in specs.items():
        if any(k in item for k in keys):
            num = _to_float(rec.get("value_num"))
            if num is None:
                num = _to_float(rec.get("value"))
            if num is not None:
                return num, str(rec.get("unit") or "")
    return None, ""


class CorpusIngestor:
    def __init__(self, skip_vectors: bool = False, limit: int | None = None) -> None:
        self.skip_vectors = skip_vectors
        self.limit = limit
        self.types: Counter = Counter()
        self.equipment: dict[str, dict] = {}  # model -> equipment row
        self.specs: dict[tuple[str, int], dict] = {}  # (split, equipment_id) -> {item: rec}
        self.kb_entries: list[dict] = []
        self.faults: list[dict] = []
        self.devices: list[dict] = []
        self.sites: list[dict] = []
        self.metrics: list[dict] = []
        self.metas: list[dict] = []
        self.tele_rows: list[dict] = []
        self.traj_rows: list[dict] = []
        self.bad_lines = 0

    # ---------- 1) 扫描 ----------
    def scan(self) -> None:
        for split, path in FILES.items():
            if not path.exists():
                raise FileNotFoundError(f"缺少语料文件：{path}")
            with path.open(encoding="utf-8", errors="replace") as f:
                for i, line in enumerate(f):
                    if self.limit and i >= self.limit:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        o = json.loads(line)
                    except json.JSONDecodeError:
                        self.bad_lines += 1
                        continue
                    t = o.get("_type", "")
                    self.types[t] += 1
                    self._route(split, t, o)
            print(f"  [scan] {split}: 已处理 {sum(self.types.values())} 条")

    def _route(self, split: str, t: str, o: dict) -> None:
        if t == "equipment":
            model = str(o.get("model") or "").strip()
            if model:
                self.equipment[model] = o
        elif t == "equipment_spec":
            eid = o.get("equipment_id")
            if isinstance(eid, int):
                self.specs.setdefault((split, eid), {})[str(o.get("item") or "")] = o
        elif t == "chunk":
            self.kb_entries.append(
                {
                    "kb_type": "document",
                    "title": f"{o.get('source_title') or '法规文档'} · 片段{int(o.get('ord') or 0) + 1}（doc{o.get('doc_id')}）",
                    "content": str(o.get("text") or ""),
                    "tags": str(o.get("source_name") or ""),
                    "source": str(o.get("source_url") or ""),
                }
            )
        elif t == "process_knowledge":
            self.kb_entries.append(
                {
                    "kb_type": "process",
                    "title": f"{o.get('process') or '工艺'} · {o.get('keyword') or ''}（{o.get('_id')}）",
                    "content": f"{o.get('keyword')}（{o.get('process')}）：{o.get('excerpt')}",
                    "tags": str(o.get("process") or ""),
                    "source": str(o.get("source_url") or ""),
                }
            )
        elif t == "template_section":
            self.kb_entries.append(
                {
                    "kb_type": "template",
                    "title": f"{o.get('template_name')} · {o.get('chapter_no')} {o.get('chapter_title')}",
                    "content": f"{o.get('chapter_title')}：{o.get('purpose')}；数据来源：{o.get('data_source')}",
                    "tags": str(o.get("template_name") or ""),
                    "source": "方案模板库（语料）",
                }
            )
        elif t == "sim_fault":
            self.faults.append(o)
        elif t == "sim_device":
            self.devices.append(o)
        elif t == "sim_site":
            self.sites.append(o)
        elif t == "sim_metrics":
            self.metrics.append(o)
        elif t in ("sim_meta", "meta"):
            self.metas.append(o)
        elif t == "sim_telemetry":
            self.tele_rows.append(o)
        elif t == "sim_trajectory":
            self.traj_rows.append(o)

    # ---------- 2) 设备入库 ----------
    def upsert_equipment(self, db) -> dict:
        # 先按 split+equipment_id 建立 spec 索引
        by_split_id: dict[tuple[str, int], dict] = self.specs
        added = updated = 0
        for model, o in self.equipment.items():
            cat = str(o.get("category") or "")
            code_cat, cat_cn = map_category(cat)
            specs = by_split_id.get((str(o.get("_split")), int(o.get("id") or 0)), {})
            values: dict[str, Any] = {}
            derived: dict[str, Any] = {}
            for field in SPEC_FIELDS:
                num, unit = pick_spec(specs, field)
                if num is None:
                    continue
                if field == "mass_kg":  # 模型表无该列，写入 spec 扩展
                    derived["mass_kg"] = num
                else:
                    values[field] = num
                values.setdefault("_units", {})[field] = unit
            spec_json = {
                k: {"value": v.get("value"), "value_num": v.get("value_num"), "unit": v.get("unit")}
                for k, v in specs.items()
            }
            spec_json["_units"] = values.pop("_units", {})
            if derived:
                spec_json["_derived"] = derived
            row = db.query(EquipmentModel).filter(EquipmentModel.code == model).first()
            data = {
                "brand": str(o.get("brand") or "徐工"),
                "model_name": f"{model} {cat}".strip(),
                "category": code_cat,
                "category_cn": cat_cn,
                "scene": map_scene(o.get("is_mining"), cat),
                "spec": spec_json,
                "data_note": "徐工官网公开产品页采集（corpus v1）；价格/维保语料未含，维持示例口径",
                "source": str(o.get("url") or "徐工官网产品中心"),
            }
            data.update(
                {k: v for k, v in values.items() if k in ("bucket_m3", "rated_load_t", "power_kw", "fuel_lh")}
            )
            if row is None:
                db.add(
                    EquipmentModel(
                        code=model,
                        price_cny=0.0,
                        maintain_yearly_cny=0.0,
                        lifespan_years=10,
                        residual_ratio_3y=0.55,
                        **data,
                    )
                )
                added += 1
            else:
                for k, v in data.items():
                    if k == "spec":
                        # 合并：保留现有工程参数（如循环时间/效率），语料补充公开参数
                        merged_spec = {**(v or {}), **(row.spec or {})}
                        row.spec = merged_spec
                        continue
                    if k in ("source", "data_note") or not getattr(row, k, None):
                        setattr(row, k, v)
                updated += 1
        db.commit()
        return {"added": added, "updated": updated}

    # ---------- 3) 知识入库（含向量化） ----------
    def upsert_knowledge(self, db) -> dict:
        seen: set[tuple[str, str]] = set()
        created = chunks = 0
        t0 = time.perf_counter()
        for i, e in enumerate(self.kb_entries, 1):
            key = (e["kb_type"], e["title"])
            if key in seen:
                continue
            seen.add(key)
            content = e["content"].strip()
            if len(content) < 10:
                continue
            if self.skip_vectors:
                row = (
                    db.query(KnowledgeEntry)
                    .filter(KnowledgeEntry.kb_type == e["kb_type"], KnowledgeEntry.title == e["title"])
                    .first()
                )
                if row is None:
                    db.add(
                        KnowledgeEntry(
                            kb_type=e["kb_type"],
                            title=e["title"],
                            content=content,
                            tags=e["tags"],
                            source=e["source"],
                            version="corpus-v1",
                        )
                    )
                    created += 1
                else:
                    row.content, row.tags, row.source = content, e["tags"], e["source"]
                if i % 200 == 0:
                    db.commit()
                continue
            chunks += rag.index_entry(
                db, e["kb_type"], e["title"], content, tags=e["tags"], source=e["source"], version="corpus-v1"
            )
            created += 1
            if i % 100 == 0:
                print(f"  [kb] {i}/{len(self.kb_entries)} 条已入库（{time.perf_counter() - t0:.0f}s）")
        db.commit()
        return {"kb_entries": created, "chunks": chunks, "elapsed_s": round(time.perf_counter() - t0, 1)}

    # ---------- 4) 训练集导出 ----------
    def export_training_data(self) -> dict:
        SIM_DIR.mkdir(parents=True, exist_ok=True)
        tele = pd.DataFrame(self.tele_rows)
        traj = pd.DataFrame(self.traj_rows)
        if tele.empty:
            return {"telemetry_rows": 0}
        cols = [
            "device_id",
            "ts",
            "fuel_lph",
            "water_temp",
            "hyd_oil_temp",
            "trans_oil_temp",
            "hyd_pressure",
            "vibration",
            "rpm",
            "brake_pressure",
            "voltage",
            "battery_soc",
            "drive_resistance",
        ]
        tele = tele[[c for c in cols if c in tele.columns]]
        if not traj.empty:
            tcols = [
                c
                for c in ["device_id", "ts", "state", "node", "lat", "lon", "speed_kmh", "load_t"]
                if c in traj.columns
            ]
            merged = tele.merge(traj[tcols], on=["device_id", "ts"], how="left")
        else:
            merged = tele
        merged = merged.sort_values(["device_id", "ts"])
        out = SIM_DIR / "corpus_telemetry.csv"
        merged.to_csv(out, index=False)
        faults = pd.DataFrame(self.faults)
        if not faults.empty:
            fcols = [
                c
                for c in [
                    "device_id",
                    "code",
                    "component",
                    "mode",
                    "signals",
                    "onset_ts",
                    "detect_ts",
                    "lead_hours",
                    "severity",
                    "description",
                ]
                if c in faults.columns
            ]
            faults[fcols].to_csv(SIM_DIR / "corpus_faults.csv", index=False)
        meta = {"devices": self.devices, "sites": self.sites, "metrics": self.metrics, "meta": self.metas}
        (SIM_DIR / "corpus_meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return {
            "telemetry_rows": int(len(merged)),
            "faults": int(len(faults)),
            "devices": len(self.devices),
            "site_nodes": len(self.sites),
            "baseline_metrics": len(self.metrics),
        }


def main() -> int:
    ap = argparse.ArgumentParser(description="ICOPS 语料 ETL")
    ap.add_argument("--skip-vectors", action="store_true", help="仅入库结构化数据，不做向量化")
    ap.add_argument("--limit", type=int, default=None, help="调试用：每个文件最多读取 N 行")
    args = ap.parse_args()

    init_db()
    db = SessionLocal()
    t0 = time.perf_counter()
    ing = CorpusIngestor(skip_vectors=args.skip_vectors, limit=args.limit)
    print("[1/4] 扫描语料 …")
    ing.scan()
    print("[2/4] 设备参数入库 …")
    eq = ing.upsert_equipment(db)
    print(f"      设备新增 {eq['added']} / 更新 {eq['updated']}  （语料型号 {len(ing.equipment)}）")
    print("[3/4] 知识库入库（法规/工艺/模板 + 向量化）…")
    kb = ing.upsert_knowledge(db)
    print(
        f"      知识条目 {kb['kb_entries']} / 分块 {kb['chunks']} / 向量库累计 {vector_store.get().count()}"
    )
    print("[4/4] 导出训练集（遥测/轨迹/故障真值）…")
    ex = ing.export_training_data()
    print(f"      遥测 {ex.get('telemetry_rows')} 行 / 故障真值 {ex.get('faults')} 例")

    report = {
        "files": {k: str(v) for k, v in FILES.items()},
        "type_counts": dict(ing.types),
        "bad_lines": ing.bad_lines,
        "equipment": eq,
        "knowledge": kb,
        "training": ex,
        "elapsed_s": round(time.perf_counter() - t0, 1),
    }
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    (CORPUS_DIR / "ingest_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    db.close()
    print(f"[ingest_corpus] 完成，用时 {report['elapsed_s']}s；报告：{CORPUS_DIR / 'ingest_report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
