"""故障智能诊断与维修工单自动生成（PRD 功能 3.2 / 指导书 5.4）。

输入：故障代码或自然语言报修描述；输出：Top3 诊断及置信度 + 维修工单
（故障描述/诊断/方案/备件/预计时长/推荐工程师 六项信息）。
"""

from __future__ import annotations

import re
from datetime import datetime, UTC

from sqlalchemy.orm import Session

from backend.app.models import Device, FaultCode, SparePart, WorkOrder
from backend.app.schemas.domain import DiagnoseResult, DiagnosisItem, WorkOrderOut

ENGINEER_POOL = {
    "ENG": "王师傅（发动机组）",
    "HYD": "李师傅（液压组）",
    "ELE": "赵师傅（电气组）",
    "TRN": "孙师傅（传动组）",
    "UND": "周师傅（底盘组）",
    "MNT": "维保班组（例行保养）",
}


def _split(text: str) -> list[str]:
    return [x.strip() for x in re.split(r"[;；,，、\s]+", text) if x.strip()]


def diagnose_code(db: Session, code: str) -> DiagnoseResult:
    fc = db.query(FaultCode).filter(FaultCode.code == code.strip().upper()).first()
    if fc is None:
        return diagnose_text(db, code)
    items = [
        DiagnosisItem(code=fc.code, name=fc.name, confidence=0.95, category=fc.category, severity=fc.severity)
    ]
    # 同类故障补充两个次选（指导书：输出 ≥3 个诊断结果及置信度）
    related = (
        db.query(FaultCode)
        .filter(FaultCode.category == fc.category, FaultCode.code != fc.code)
        .order_by(FaultCode.severity.desc())
        .limit(2)
        .all()
    )
    confs = [0.62, 0.41]
    for rc, cf in zip(related, confs, strict=False):
        items.append(
            DiagnosisItem(
                code=rc.code, name=rc.name, confidence=cf, category=rc.category, severity=rc.severity
            )
        )
    return DiagnoseResult(
        text=code,
        code=fc.code,
        top3=items,
        fix_plan=fc.fix_plan,
        parts=[{"name": p} for p in _split(fc.parts)],
        est_hours=fc.est_hours,
        engineer=ENGINEER_POOL.get(fc.category, "服务工程师"),
    )


CAT_TERMS = {"ENG": "发动机", "HYD": "液压", "ELE": "电气", "TRN": "传动", "UND": "底盘", "MNT": "保养"}


def diagnose_text(db: Session, text: str) -> DiagnoseResult:
    """自然语言报修：维保知识库关键词检索 + 名称匹配（确定性排序）。"""
    codes = db.query(FaultCode).all()
    scored: list[tuple[float, FaultCode]] = []
    for fc in codes:
        s = 0.0
        kws = [k for k in _split(fc.keywords) if len(k) >= 2]
        matched = 0
        for kw in kws:
            if kw in text:
                s += 1.0
                matched += 1
        if fc.name and any(ch in fc.name for ch in ("漏油", "高温", "异响", "启动", "制动")):
            if fc.name.split("，")[0] in text or fc.name.split("，")[0][:4] in text:
                s += 2.0
        if fc.code.lower() in text.lower():
            s += 5.0
        # 类别词出现即加权（如“液压”提升液压类命中，保证确定性）
        if CAT_TERMS.get(fc.category, "") in text:
            s += 1.0
        if s > 0:
            scored.append((s, fc))
    # 稳定排序：分数降序，同分按知识库条目序号（确定性）
    scored.sort(key=lambda x: -x[0])
    if not scored:
        # 知识库无覆盖：明确说明（防幻觉第二道防线：知识库外问题拒答）
        return DiagnoseResult(
            text=text,
            code="",
            top3=[
                DiagnosisItem(
                    code="UNKNOWN", name="维保知识库未覆盖该描述", confidence=0.0, category="", severity="L"
                )
            ],
            fix_plan="请补充故障代码或设备型号，转人工服务确认（已记录，用于知识库优化）",
            parts=[],
            est_hours=0.0,
            engineer="客服中心（转人工）",
        )
    seen: set[str] = set()
    items: list[DiagnosisItem] = []
    for rank, (score, fc) in enumerate(scored[:3]):
        if fc.code in seen:
            continue
        seen.add(fc.code)
        # 置信度按分数与名次递减（rank 权重），保证 Top3 严格降序
        conf = min(0.95, 0.5 + (score + (2 - rank) * 0.4) / 10.0)
        items.append(
            DiagnosisItem(
                code=fc.code,
                name=fc.name,
                confidence=round(conf, 2),
                category=fc.category,
                severity=fc.severity,
            )
        )
    best = scored[0][1]
    return DiagnoseResult(
        text=text,
        code=best.code,
        top3=items,
        fix_plan=best.fix_plan,
        parts=[{"name": p} for p in _split(best.parts)],
        est_hours=best.est_hours,
        engineer=ENGINEER_POOL.get(best.category, "服务工程师"),
    )


def _enrich_parts(db: Session, parts: list[dict]) -> list[dict]:
    """备件库存检查：库存不足自动生成采购建议（PRD 3.2）。"""
    out = []
    for p in parts:
        name = p.get("name", "")
        sp = db.query(SparePart).filter(SparePart.name == name).first() if name else None
        out.append(
            {
                "name": name,
                "qty": 1,
                "price_cny": sp.price_cny if sp else 0.0,
                "stock": sp.stock if sp else 0,
                "action": "" if (sp and sp.stock > 0) else "库存不足，建议采购",
            }
        )
    return out


def create_workorder(
    db: Session, device_code: str, result: DiagnoseResult, warning_id: int | None = None
) -> WorkOrderOut:
    """生成维修工单（含六项信息）。"""
    device = db.query(Device).filter(Device.code == device_code).first()
    if device is None:
        raise ValueError(f"设备 {device_code} 不存在")
    parts = _enrich_parts(db, result.parts)
    seq = db.query(WorkOrder).count() + 1
    day = datetime.now(UTC).strftime("%Y%m%d")
    wo = WorkOrder(
        code=f"WO-{day}-{seq:04d}",
        warning_id=warning_id,
        device_id=device.id,
        fault_desc=result.text or result.code,
        fault_code=result.code,
        diagnosis=[d.model_dump() for d in result.top3],
        fix_plan=result.fix_plan,
        parts=parts,
        est_hours=result.est_hours,
        engineer=result.engineer,
        status="created",
    )
    db.add(wo)
    db.commit()
    db.refresh(wo)
    return WorkOrderOut(
        code=wo.code,
        device_code=device.code,
        fault_desc=wo.fault_desc,
        fault_code=wo.fault_code,
        diagnosis=wo.diagnosis,
        fix_plan=wo.fix_plan,
        parts=wo.parts,
        est_hours=wo.est_hours,
        engineer=wo.engineer,
        status=wo.status,
    )
