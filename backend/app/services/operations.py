"""智能运维支撑服务：设备运营聚合 / 工单状态机 / 维修归档统计。

设计要点：
  * 设备运营：台账+实时状态、利用率/工时/能耗（基于遥测聚合）、保养到期、健康评分、备件库存预警
  * 工单六状态机：created → dispatched → repairing → pending_acceptance → completed → archived
    （只允许向前推进或同状态补充说明，每次变更写入 WorkOrderEvent 留痕）
  * 维修归档：MTTR、故障分布、备件消耗、复发设备统计，并支持案例回写知识库
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.models import (
    Device,
    EquipmentModel,
    MaintenancePlan,
    SparePart,
    Warning,
    WorkOrder,
    WorkOrderEvent,
)

STATUS_FLOW = ["created", "dispatched", "repairing", "pending_acceptance", "completed", "archived"]
STATUS_CN = {
    "created": "已生成",
    "dispatched": "已派工",
    "repairing": "维修中",
    "pending_acceptance": "待验收",
    "completed": "已完成",
    "archived": "已归档",
}
STATUS_COLOR = {
    "created": "blue",
    "dispatched": "geekblue",
    "repairing": "orange",
    "pending_acceptance": "gold",
    "completed": "green",
    "archived": "default",
}
SPARE_ALERT_THRESHOLD = 2


def _telemetry_aggregate() -> dict[str, dict]:
    """按设备聚合遥测：工时、利用率、油耗（离线演示数据口径）。"""
    path = settings.repo_root / "data" / "simulated" / "telemetry.csv"
    if not path.exists():
        return {}
    df = pd.read_csv(path)
    out: dict[str, dict] = {}
    for code, g in df.groupby("device_code", sort=False):
        total = len(g)
        working = int((g["state"] == "working").sum())
        idle = int((g["state"] == "idle").sum())
        fault = int((g["state"] == "fault").sum())
        fuel = float(g["fuel_rate_lh"].sum())
        out[code] = {
            "samples": total,
            "work_hours": working,
            "idle_hours": idle,
            "fault_hours": fault,
            "utilization": round(working / total, 3) if total else 0.0,
            "fuel_l": round(fuel, 1),
        }
    return out


def device_operations(db: Session) -> dict:
    """设备运营栏数据：台账/实时状态/效率/保养/健康/备件。"""
    tele = _telemetry_aggregate()
    devices = db.query(Device).order_by(Device.id).all()
    warnings = db.query(Warning).all()
    plans = db.query(MaintenancePlan).all()
    world_now = datetime.now()

    rows: list[dict] = []
    for d in devices:
        model = db.get(EquipmentModel, d.model_id)
        agg = tele.get(d.code, {})
        dev_warns = [w for w in warnings if w.device_id == d.id and w.status == "open"]
        high = sum(1 for w in dev_warns if (w.severity or "M") == "H")
        med = sum(1 for w in dev_warns if (w.severity or "M") == "M")
        health = max(0, 100 - 25 * high - 10 * med)
        if d.work_state == "fault":
            health = min(health, 40)
        due_plans = [p for p in plans if p.device_id == d.id and p.status == "planned"]
        rows.append(
            {
                "code": d.code,
                "name": d.name,
                "model_code": model.code if model else "",
                "model_name": model.model_name if model else "",
                "category_cn": model.category_cn if model else "",
                "work_state": d.work_state,
                "lat": d.lat,
                "lng": d.lng,
                "work_hours": agg.get("work_hours", 0),
                "idle_hours": agg.get("idle_hours", 0),
                "utilization": agg.get("utilization", 0.0),
                "fuel_l": agg.get("fuel_l", 0.0),
                "health_score": health,
                "open_warnings": len(dev_warns),
                "high_warnings": high,
                "maintenance_due": len(due_plans) > 0,
                "plan_items": [p.item for p in due_plans],
                "last_warning_at": max((w.created_at for w in dev_warns), default=None).isoformat()
                if dev_warns
                else "",
                "note": d.status_note or "",
            }
        )

    spare_alerts = [
        {
            "sku": s.sku,
            "name": s.name,
            "stock": s.stock,
            "price_cny": s.price_cny,
            "action": "库存不足，建议采购" if s.stock <= SPARE_ALERT_THRESHOLD else "",
        }
        for s in db.query(SparePart).order_by(SparePart.stock).all()
        if s.stock <= SPARE_ALERT_THRESHOLD
    ]
    plan_due = [
        {
            "device_code": db.get(Device, p.device_id).code if db.get(Device, p.device_id) else "",
            "item": p.item,
            "due_hours": p.due_hours,
            "window": f"第{p.window_start_day}-{p.window_end_day}天",
            "status": p.status,
            "note": p.note,
        }
        for p in plans
        if p.status == "planned"
    ]
    total = len(rows)
    avg_util = round(sum(r["utilization"] for r in rows) / total, 3) if total else 0.0
    return {
        "summary": {
            "device_total": total,
            "working": sum(1 for r in rows if r["work_state"] == "working"),
            "fault": sum(1 for r in rows if r["work_state"] == "fault"),
            "avg_utilization": avg_util,
            "open_warnings": sum(r["open_warnings"] for r in rows),
            "maintenance_due": sum(1 for r in rows if r["maintenance_due"]),
            "spare_alerts": len(spare_alerts),
            "generated_at": world_now.isoformat(),
        },
        "devices": rows,
        "spare_alerts": spare_alerts,
        "plan_due": plan_due,
        "status_flow": [{"key": k, "label": STATUS_CN[k]} for k in STATUS_FLOW],
    }


def advance_workorder(
    db: Session,
    code: str,
    to_status: str,
    note: str = "",
    operator: str = "",
    labor_hours: float | None = None,
    repair_notes: str | None = None,
    parts_used: list | None = None,
) -> dict:
    """工单状态推进（六状态机）+ 事件留痕 + 时间戳。"""
    wo = db.query(WorkOrder).filter(WorkOrder.code == code).first()
    if wo is None:
        raise ValueError(f"工单 {code} 不存在")
    if to_status not in STATUS_FLOW:
        raise ValueError(f"非法状态 {to_status}，可选：{'/'.join(STATUS_FLOW)}")
    cur = wo.status or "created"
    if STATUS_FLOW.index(to_status) < STATUS_FLOW.index(cur):
        raise ValueError(f"不允许回退状态：{STATUS_CN.get(cur, cur)} → {STATUS_CN.get(to_status, to_status)}")
    if to_status == cur and not note:
        raise ValueError("状态未变化，如需补充说明请填写备注")
    now = datetime.now()
    wo.status = to_status
    if to_status == "dispatched":
        wo.dispatched_at = now
    elif to_status == "repairing":
        wo.repair_started_at = now
    elif to_status == "pending_acceptance":
        wo.acceptance_at = now
    elif to_status == "completed":
        wo.completed_at = now
        wo.closed_at = now
    elif to_status == "archived":
        wo.archived_at = now
        if wo.completed_at is None:
            wo.completed_at = now
    if labor_hours is not None:
        wo.labor_hours = labor_hours
    if repair_notes:
        wo.repair_notes = (wo.repair_notes + "\n" + repair_notes).strip() if wo.repair_notes else repair_notes
    if parts_used:
        wo.parts_used = parts_used
    db.add(
        WorkOrderEvent(
            workorder_code=code, from_status=cur, to_status=to_status, note=note, operator=operator or "系统"
        )
    )
    db.commit()
    return workorder_detail(db, code)


def workorder_detail(db: Session, code: str) -> dict:
    wo = db.query(WorkOrder).filter(WorkOrder.code == code).first()
    if wo is None:
        raise ValueError(f"工单 {code} 不存在")
    events = (
        db.query(WorkOrderEvent)
        .filter(WorkOrderEvent.workorder_code == code)
        .order_by(WorkOrderEvent.id)
        .all()
    )
    device = db.get(Device, wo.device_id)
    return {
        "code": wo.code,
        "device_code": device.code if device else "",
        "fault_desc": wo.fault_desc,
        "fault_code": wo.fault_code,
        "severity": wo.severity or "M",
        "diagnosis": wo.diagnosis,
        "fix_plan": wo.fix_plan,
        "parts": wo.parts,
        "parts_used": wo.parts_used or [],
        "est_hours": wo.est_hours,
        "labor_hours": wo.labor_hours,
        "engineer": wo.engineer,
        "status": wo.status or "created",
        "status_cn": STATUS_CN.get(wo.status or "created", wo.status),
        "status_color": STATUS_COLOR.get(wo.status or "created", "default"),
        "repair_notes": wo.repair_notes,
        "created_at": wo.created_at.isoformat() if wo.created_at else "",
        "dispatched_at": wo.dispatched_at.isoformat() if wo.dispatched_at else "",
        "repair_started_at": wo.repair_started_at.isoformat() if wo.repair_started_at else "",
        "acceptance_at": wo.acceptance_at.isoformat() if wo.acceptance_at else "",
        "completed_at": wo.completed_at.isoformat() if wo.completed_at else "",
        "archived_at": wo.archived_at.isoformat() if wo.archived_at else "",
        "timeline": [
            {
                "from": e.from_status,
                "from_cn": STATUS_CN.get(e.from_status, e.from_status),
                "to": e.to_status,
                "to_cn": STATUS_CN.get(e.to_status, e.to_status),
                "note": e.note,
                "operator": e.operator,
                "at": e.created_at.isoformat() if e.created_at else "",
            }
            for e in events
        ],
        "status_flow": [{"key": k, "label": STATUS_CN[k]} for k in STATUS_FLOW],
    }


def archive_stats(db: Session) -> dict:
    """维修归档：归档工单 + MTTR/故障分布/备件消耗/复发统计（并可回写知识库）。"""
    orders = db.query(WorkOrder).all()
    archived = [w for w in orders if (w.status or "") == "archived"]
    completed = [w for w in orders if (w.status or "") in ("completed", "archived")]

    mttr_list = []
    for w in completed:
        if w.created_at and (w.completed_at or w.closed_at):
            end = w.completed_at or w.closed_at
            mttr_list.append((end - w.created_at).total_seconds() / 3600)
    mttr = round(sum(mttr_list) / len(mttr_list), 1) if mttr_list else None

    fault_dist: dict[str, int] = {}
    for w in orders:
        key = w.fault_code or "未分类"
        fault_dist[key] = fault_dist.get(key, 0) + 1

    parts_consumption: dict[str, int] = {}
    for w in orders:
        for p in w.parts_used or w.parts or []:
            name = p.get("name") if isinstance(p, dict) else str(p)
            if not name:
                continue
            parts_consumption[name] = parts_consumption.get(name, 0) + int(
                p.get("qty", 1) if isinstance(p, dict) else 1
            )

    device_counts: dict[int, int] = {}
    for w in orders:
        device_counts[w.device_id] = device_counts.get(w.device_id, 0) + 1
    recurrence = [
        {
            "device_code": db.get(Device, did).code if db.get(Device, did) else str(did),
            "orders": cnt,
            "note": "同一设备多次维修，建议纳入复发分析",
        }
        for did, cnt in sorted(device_counts.items(), key=lambda x: -x[1])
        if cnt >= 2
    ]

    return {
        "summary": {
            "total_orders": len(orders),
            "archived": len(archived),
            "completed": len(completed),
            "in_progress": len([w for w in orders if (w.status or "") not in ("completed", "archived")]),
            "mttr_hours": mttr,
            "recurrence_devices": len(recurrence),
        },
        "archived": [
            workorder_detail(db, w.code)
            for w in sorted(archived, key=lambda x: -(x.archived_at.timestamp() if x.archived_at else 0))
        ],
        "fault_distribution": [
            {"fault_code": k, "count": v} for k, v in sorted(fault_dist.items(), key=lambda x: -x[1])
        ],
        "parts_consumption": [
            {"name": k, "qty": v} for k, v in sorted(parts_consumption.items(), key=lambda x: -x[1])
        ],
        "recurrence": recurrence,
        "status_flow": [{"key": k, "label": STATUS_CN[k]} for k in STATUS_FLOW],
    }
