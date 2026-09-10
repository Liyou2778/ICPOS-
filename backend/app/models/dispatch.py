"""调度域：派单 / 调度日志（表4-3 调度域）。"""

from __future__ import annotations

from datetime import datetime, UTC

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class DispatchPlan(Base):
    """调度方案（一次求解快照：派单表 + 统计 + 可解释原因）。"""

    __tablename__ = "sch_dispatch_plan"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("proj_project.id"), nullable=True)
    plan_type: Mapped[str] = mapped_column(String(16), default="ai")  # ai | manual
    trigger: Mapped[str] = mapped_column(String(64), default="initial")  # initial|fault|delay
    assignments: Mapped[list] = mapped_column(JSON, default=list)  # 派单表
    stats: Mapped[dict] = mapped_column(JSON, default=dict)  # 空载率/利用率/等待
    note: Mapped[str] = mapped_column(Text, default="")
    confirmed: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DispatchOrder(Base):
    """实际派单（确认下发后生成）。"""

    __tablename__ = "sch_dispatch_order"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_id: Mapped[int | None] = mapped_column(ForeignKey("sch_dispatch_plan.id"), nullable=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("proj_task.id"), nullable=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("dev_device.id"), index=True)
    from_point: Mapped[str] = mapped_column(String(64), default="")
    to_point: Mapped[str] = mapped_column(String(64), default="")
    payload_t: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(24), default="issued")  # issued|running|done|aborted
    reason: Mapped[str] = mapped_column(String(256), default="")  # 可解释：派单原因
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DispatchLog(Base):
    """调度日志（事件：派单/重调度/越界/确认）。"""

    __tablename__ = "sch_dispatch_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[int | None] = mapped_column(ForeignKey("dev_device.id"), nullable=True)
    event: Mapped[str] = mapped_column(String(32), default="")  # dispatch|reschedule|geofence|confirm
    detail: Mapped[str] = mapped_column(Text, default="")
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
