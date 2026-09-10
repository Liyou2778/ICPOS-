"""知识库装载：四大库结构化/文本入库（设备参数库、维保知识库、工艺文档、模板）。

指导书 5.2 阶段二 DoD：
  * 主力型号参数检索 ≤3s 且抽样正确率 ≥95%；
  * 文本经分块向量化入向量库（表格类设备参数走结构化查询，不参与分块——6.2 节）。
"""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path

from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.models import EquipmentModel, FaultCode
from backend.app.services import rag

logger = logging.getLogger("icops.kb")


def parse_spec(spec_str: str) -> dict:
    """把 'k=v;k2=v2' 形式解析为 dict（避免 CSV 内嵌 JSON 引号转义）。"""
    out: dict = {}
    if not spec_str:
        return out
    for pair in spec_str.split(";"):
        if "=" in pair:
            k, v = pair.split("=", 1)
            try:
                out[k.strip()] = float(v) if "." in v else int(v)
            except ValueError:
                out[k.strip()] = v.strip()
    return out


def load_equipment_models(db: Session, csv_path: Path) -> int:
    """设备参数库入库（upsert by code）。"""
    count = 0
    with csv_path.open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            code = row["code"].strip()
            model = db.query(EquipmentModel).filter(EquipmentModel.code == code).first()
            data = dict(
                brand=row.get("brand", "徐工"),
                series=row.get("series", ""),
                model_name=row.get("model_name", ""),
                category=row.get("category", "excavator"),
                category_cn=row.get("category_cn", ""),
                scene=row.get("scene", "mining"),
                price_cny=float(row.get("price_cny", 0) or 0),
                rated_load_t=float(row.get("rated_load_t", 0) or 0),
                bucket_m3=float(row.get("bucket_m3", 0) or 0),
                power_kw=float(row.get("power_kw", 0) or 0),
                fuel_lh=float(row.get("fuel_lh", 0) or 0),
                maintain_yearly_cny=float(row.get("maintain_yearly_cny", 0) or 0),
                lifespan_years=float(row.get("lifespan_years", 10) or 10),
                residual_ratio_3y=float(row.get("residual_ratio_3y", 0.55) or 0.55),
                spec=parse_spec(row.get("spec", "")),
                data_note=row.get("data_note", ""),
                source=row.get("source", "徐工官网/产品手册（公开渠道）"),
            )
            if model is None:
                db.add(EquipmentModel(code=code, **data))
            else:
                for k, v in data.items():
                    setattr(model, k, v)
            count += 1
    db.commit()
    return count


def load_fault_codes(db: Session, csv_path: Path) -> int:
    """维保知识库入库（upsert by code，≥50 条故障码 + 维修方案 + 保养定额）。"""
    count = 0
    with csv_path.open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            code = row["code"].strip()
            fc = db.query(FaultCode).filter(FaultCode.code == code).first()
            data = dict(
                name=row.get("name", ""),
                category=row.get("category", "ENG"),
                severity=row.get("severity", "M"),
                causes=row.get("causes", ""),
                fix_plan=row.get("fix_plan", ""),
                parts=row.get("parts", ""),
                est_hours=float(row.get("est_hours", 2) or 2),
                maintain_cost_cny=float(row.get("maintain_cost_cny", 0) or 0),
                keywords=row.get("keywords", ""),
                source=row.get("source", "维保知识库"),
            )
            if fc is None:
                db.add(FaultCode(code=code, **data))
            else:
                for k, v in data.items():
                    setattr(fc, k, v)
            count += 1
    db.commit()
    return count


def index_process_markdown(db: Session, md_dir: Path) -> int:
    """施工工艺库（矿山五道工序 + 总览）：Markdown 全文分块向量化 + 引用登记。"""
    total = 0
    for path in sorted(md_dir.glob("*.md")):
        content = path.read_text(encoding="utf-8").strip()
        title = content.splitlines()[0].lstrip("# ").strip()
        n = rag.index_entry(
            db, "process", title, content, tags="矿山,工艺,施工", source=path.name, version="V1.0"
        )
        total += n
    return total


def index_templates(db: Session, tpl_dir: Path) -> int:
    """方案模板库：章节化 JSON 结构入库（模板装配模式的骨架来源，指导书 6.7）。"""
    total = 0
    for path in sorted(tpl_dir.glob("*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        content = f"模板类型：{doc.get('doc_type')}；{doc.get('title')}。" + "".join(
            f"章节：{c['chapter']}。要点：{'；'.join(c.get('bullets', []))}。"
            for c in doc.get("chapters", [])
        )
        n = rag.index_entry(
            db,
            "template",
            doc.get("title", path.stem),
            content,
            tags="模板,方案",
            source=path.name,
            version="V1.0",
        )
        total += n
    return total


def build_all_knowledge(db: Session) -> dict:
    """一键执行四大库 + 向量化入库，返回统计（scripts/build_kb.py 调用）。"""
    root = settings.repo_root
    stats: dict = {}
    stats["equipment"] = load_equipment_models(db, root / "data" / "knowledge" / "equipment_models.csv")
    stats["fault_codes"] = load_fault_codes(db, root / "data" / "knowledge" / "maintenance_knowledge.csv")
    stats["process_chunks"] = index_process_markdown(db, root / "data" / "knowledge")
    stats["template_chunks"] = index_templates(db, root / "data" / "knowledge" / "templates")
    store = vector_store_count()
    stats["vector_entries"] = store
    return stats


def vector_store_count() -> int:
    from backend.app.services.vectorstore import vector_store

    try:
        return vector_store.get().count()
    except Exception:  # noqa: BLE001
        return 0
