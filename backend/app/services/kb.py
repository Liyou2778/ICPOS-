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


def index_project_corpus(db: Session) -> int:
    """项目运营库（真实招标锚点 + 成本台账 + 标定基准）：按项目建档，可溯源到公告链接。

    数据边界：project_budget 为公开招标公告锚点（real）；施工任务/成本台账为按锚点仿真生成
    （simulated），文档内显式标注，避免被当作真实施工记录引用。
    招标锚点**不依赖**模型产物即可入库；成本构成/预算执行部分需标定基准存在才附带（否则省略）。
    """
    from backend.app.services import project_analytics as pj

    baseline_ready = pj.artifacts_ready()
    if not baseline_ready:
        logger.warning("项目标定基准缺失：仅索引招标锚点，成本/预算口径部分省略")
    total = 0
    for p in pj.project_list(db, limit=500):
        anchor = pj.tender_anchor(db, p["code"])
        parts = [
            f"项目编号：{anchor['code']}",
            f"项目名称：{anchor['name']}",
            f"招标人：{anchor['tenderer'] or '—'}",
            f"行业：{anchor['industry'] or '—'}",
            f"地区：{anchor['region'] or '—'}",
            f"交易平台：{anchor['platform'] or '—'}",
            f"公告日期：{anchor['publish_date'] or '—'}",
            f"计划总投资：{anchor['plan_invest_yuan']:.0f} 元",
            f"标段预算合计：{anchor['section_est_total_yuan']:.0f} 元",
            f"中标金额：{anchor['win_amount_yuan']:.0f} 元",
            f"计划工期：{anchor['duration_days']} 天",
            f"资金来源：{anchor['funding_source'] or '—'}",
            f"批复单位：{anchor['approval_authority'] or '—'}",
            f"数据口径：{anchor['budget_scope'] or '公告原文口径'}",
        ]
        if baseline_ready and p["total_cost_yuan"]:
            cs = pj.cost_structure(db, p["code"])
            parts.append(f"成本台账合计：{cs['total_cost_yuan']:.0f} 元（期间 {'、'.join(cs['periods'])}）")
            parts.append(
                "成本构成："
                + "；".join(
                    f"{i['cost_type']} {i['share_pct']}%（基准 {i['baseline_mean_pct']}%，容差带 "
                    f"{i['band_pct'][0]}~{i['band_pct'][1]}%，判定 {i['verdict']}）"
                    for i in cs["items"]
                )
            )
            if cs.get("budget_execution"):
                be = cs["budget_execution"]
                parts.append(
                    f"预算执行比率：{be['ratio']:.4f}，判定 {be['level']}"
                    f"（历史 P25~P75：{be['band']['p25']}~{be['band']['p75']}）"
                )
            parts.append(f"分析结论：{cs['conclusion']}")
        parts.append(
            "数据边界：招标锚点为公开公告真实数据；施工任务与成本台账为按其仿真生成，"
            "用于方法验证，不代表真实施工记录。"
        )
        content = "。".join(parts) + "。"
        total += rag.index_entry(
            db,
            "project",
            f"项目运营档案：{anchor['code']} {anchor['name']}",
            content,
            tags="项目,招标,标段预算,成本构成,预算执行",
            source=anchor["source_url"] or f"project_corpus:{anchor['code']}",
            version="V1.0",
        )
    if baseline_ready:
        total += rag.index_entry(
            db,
            "project",
            "项目运营分析方法与模型评估结论",
            "项目成本分析与工期缓冲采用标定统计基准法（calibrated_statistical_baseline），"
            "不使用机器学习逐任务预测。评估协议：按项目划分训练/测试（零交叉），训练集内"
            "Leave-One-Project-Out 交叉验证，独立测试集仅最终评估一次，全部指标与朴素基线对照。"
            "评估结论：工期偏差回归在测试集 MAE 大于中位数基线、交叉验证 R² 小于 0，"
            "单特征最大相关系数约 0.12，训练 R² 约 0.999（仅记忆噪声）；因此 ML 未通过上线门控。"
            "生产方法：工期采用历史偏差分位数缓冲（P80 作为缓冲建议），成本采用结构占比 P25~P75 "
            "容差带与预算执行比率分位数（P75 预警、P90 严重偏差）。"
            "数据边界：标签为按真实招标锚点仿真生成，样本量小，结论不可外推为行业规律，"
            "输出仅作决策参考并需人工确认。",
            tags="项目,成本分析,模型评估,上线门控,数据边界",
            source="data/models/project/eval_report.json",
            version="V1.0",
        )
    return total


# ---------------------------------------------------------------- 全域语料（新）入库

def index_unified_corpus(db: Session) -> dict:
    """把新全域语料（agent_train/project_test）中的知识型实体索引进知识库。

    入选实体：设备型号档案（真实公开规格）/ 价格与 TCO / 故障案例 / 方案模板（去重）/ 客户与合同域。
    **不入库**：evaluation_qa —— 该 756 条问答作为 Agent 微调前后对比的黄金评测集，
    一旦入库会被检索命中，导致"自己考自己"的评测污染。
    """
    from backend.app.models.corpus import CorpusRecord

    rows = db.query(CorpusRecord.entity_type, CorpusRecord.payload).all()
    by_type: dict[str, list[dict]] = {}
    for et, payload in rows:
        obj = json.loads(payload) if isinstance(payload, str) else payload
        by_type.setdefault(et, []).append(obj)

    stats: dict[str, int] = {}

    # 1) 设备型号档案（真实公开）
    n = 0
    for m in by_type.get("equipment_model", []):
        specs = m.get("specifications") or {}
        cfg = m.get("configuration") or {}
        content = "。".join(
            [
                f"型号：{m.get('model_name')}",
                f"设备子类型：{m.get('equipment_subtype')}",
                "技术规格：" + "；".join(f"{k}={v}" for k, v in specs.items()),
                f"动力系统：{cfg.get('energy_system', '—')}",
                f"作业方式：{cfg.get('operation_mode', '—')}",
                f"工作装置：{cfg.get('work_attachment', '—')}",
                f"安全配置：{'、'.join(cfg.get('safety_package') or []) or '—'}",
                f"数据来源：{m.get('source_name')}（{m.get('data_origin')}）",
                f"来源链接：{m.get('source_url')}",
                "口径说明：规格为公开产品页数据；选配项与出厂日期为仿真补充，需厂商确认。",
            ]
        )
        n += rag.index_entry(db, "equipment", f"设备型号档案：{m.get('model_name')}", content,
                             tags=f"设备,型号,{m.get('equipment_subtype')},规格参数",
                             source=m.get("source_url") or m.get("source_name") or "unified_corpus",
                             version="V1.0")
    stats["equipment_model"] = n

    # 2) 价格与 TCO
    n = 0
    for p in by_type.get("equipment_price_tco", []):
        content = "。".join(
            [
                f"型号：{p.get('model_name')}（{p.get('equipment_subtype')}）",
                f"购置价：{float(p.get('purchase_price_cny') or 0):,.0f} 元",
                f"能源类型：{p.get('energy_type')}；能源成本：{float(p.get('energy_cost_per_hour_cny') or 0):.2f} 元/小时",
                f"维保成本：{float(p.get('maintenance_cost_per_hour_cny') or 0):.2f} 元/小时",
                f"三年残值率：{float(p.get('residual_value_rate_3y') or 0):.2f}",
                f"年作业小时：{float(p.get('annual_operation_hours') or 0):.0f} 小时",
                f"三年 TCO：{float(p.get('three_year_tco_cny') or 0):,.0f} 元",
                f"口径：{p.get('source_name')}",
                "数据边界：整机售价为公开渠道未披露项，价格按子类型市场区间构造，报价需厂商确认。",
            ]
        )
        n += rag.index_entry(db, "price", f"价格与三年 TCO：{p.get('model_name')}", content,
                             tags="价格,TCO,购置,能耗,维保,残值",
                             source=p.get("source_name") or "unified_corpus", version="V1.0")
    stats["equipment_price_tco"] = n

    # 3) 故障案例
    n = 0
    for c in by_type.get("fault_case", []):
        content = "。".join(
            [
                f"故障码：{c.get('fault_code')}（{c.get('fault_type')}）",
                f"适用设备：{c.get('equipment_subtype')}",
                f"异常部件：{c.get('abnormal_part')}",
                f"根因：{c.get('root_cause')}",
                f"严重度：{c.get('severity')}；预计停机：{float(c.get('estimated_downtime_hours') or 0):.0f} 小时",
                f"保养间隔：{c.get('maintenance_interval_hours')} 小时",
                "维修步骤：" + " → ".join(c.get("repair_steps") or []),
                "推荐备件：" + "、".join(c.get("recommended_parts") or []),
                f"口径：{c.get('source_name')}",
            ]
        )
        n += rag.index_entry(db, "maintenance", f"故障案例：{c.get('case_id')} {c.get('fault_code')} {c.get('fault_type')}",
                             content, tags=f"故障,维修,{c.get('equipment_subtype')},{c.get('fault_code')}",
                             source=c.get("source_name") or "unified_corpus", version="V1.0")
    stats["fault_case"] = n

    # 4) 方案模板（同类型多条记录内容重复，按模板类型去重后入库）
    seen: set[str] = set()
    n = 0
    for t in by_type.get("project_template", []):
        ttype = str(t.get("template_type"))
        if ttype in seen:
            continue
        seen.add(ttype)
        chapters = t.get("chapters") or []
        content = "。".join(
            [
                f"模板类型：{ttype}",
                "章节结构：" + "；".join(
                    f"{ch.get('order')}. {ch.get('name')}（需填字段：{'、'.join(ch.get('required_fields') or [])}）"
                    for ch in chapters
                ),
                f"口径：{t.get('source_name')}",
            ]
        )
        n += rag.index_entry(db, "template", f"方案模板：{ttype}", content,
                             tags=f"模板,方案,{ttype}", source=t.get("source_name") or "unified_corpus",
                             version="V1.0")
    stats["project_template"] = n
    stats["project_template_types"] = len(seen)

    # 5) 客户与合同域
    contracts = {str(c.get("customer_id")): c for c in by_type.get("customer_contract", [])}
    n = 0
    for c in by_type.get("customer", []):
        con = contracts.get(str(c.get("customer_id")), {})
        content = "。".join(
            [
                f"客户名称：{c.get('customer_name')}",
                f"行业：{c.get('industry')}；服务等级：{c.get('service_level')}",
                f"联系人：{c.get('contact_person')}",
                f"设备清单：{'、'.join(c.get('equipment_inventory') or [])}",
                f"服务合同：{con.get('contract_name', '—')}；合同金额：{float(con.get('contract_amount_cny') or 0):,.0f} 元",
                f"合同期：{con.get('contract_start', '—')} ~ {con.get('contract_end', '—')}",
                f"口径：{c.get('source_name')}",
                "数据边界：客户与合同为仿真数据（公开资料无可复用客户清单），不得作为真实客户信息引用。",
            ]
        )
        n += rag.index_entry(db, "customer", f"客户档案：{c.get('customer_name')}", content,
                             tags=f"客户,合同,{c.get('industry')},设备清单",
                             source=c.get("source_name") or "unified_corpus", version="V1.0")
    stats["customer"] = n

    stats["eval_qa_excluded"] = len(by_type.get("evaluation_qa", []))
    return stats


def rebuild_all_knowledge(db: Session) -> dict:
    """整体重建知识库：清空既有条目与向量 → 五大库 + 全域语料重新入库。"""
    from backend.app.models.knowledge import KnowledgeEntry
    from backend.app.services.vectorstore import vector_store

    cleared_kb = db.query(KnowledgeEntry).delete()
    db.commit()
    try:
        cleared_vec = vector_store.get().reset()
    except Exception as exc:  # noqa: BLE001
        logger.warning("向量库清空失败（将覆盖写入）：%s", exc)
        cleared_vec = 0
    stats = build_all_knowledge(db)
    stats["cleared_kb_entries"] = int(cleared_kb)
    stats["cleared_vectors"] = int(cleared_vec)
    stats["unified_corpus"] = index_unified_corpus(db)
    stats["vector_entries"] = vector_store_count()
    return stats


def build_all_knowledge(db: Session) -> dict:
    """一键执行五大库 + 向量化入库，返回统计（scripts/build_kb.py 调用）。"""
    root = settings.repo_root
    stats: dict = {}
    stats["equipment"] = load_equipment_models(db, root / "data" / "knowledge" / "equipment_models.csv")
    stats["fault_codes"] = load_fault_codes(db, root / "data" / "knowledge" / "maintenance_knowledge.csv")
    stats["process_chunks"] = index_process_markdown(db, root / "data" / "knowledge")
    stats["template_chunks"] = index_templates(db, root / "data" / "knowledge" / "templates")
    stats["project_chunks"] = index_project_corpus(db)
    store = vector_store_count()
    stats["vector_entries"] = store
    return stats


def vector_store_count() -> int:
    from backend.app.services.vectorstore import vector_store

    try:
        return vector_store.get().count()
    except Exception:  # noqa: BLE001
        return 0
