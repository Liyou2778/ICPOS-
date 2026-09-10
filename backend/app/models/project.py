"""项目域：项目 / 施工任务 / 工序进度（表4-3 项目域）。"""

from __future__ import annotations

from datetime import datetime, UTC

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class Project(Base):
    """工程项目（MVP 演示聚焦矿山开采/剥离主线）。"""

    __tablename__ = "proj_project"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("cust_customer.id"), nullable=True)
    scene_type: Mapped[str] = mapped_column(String(16), default="mining")  # mining|earthwork|agriculture
    work_volume: Mapped[float] = mapped_column(Float, default=0)  # 工程量（万吨 或 万方）
    work_volume_unit: Mapped[str] = mapped_column(String(8), default="万吨")
    duration_days: Mapped[int] = mapped_column(Integer, default=0)
    budget_cny: Mapped[float] = mapped_column(Float, default=0)  # 预算（元）
    terrain: Mapped[str] = mapped_column(String(128), default="")
    quality_std: Mapped[str] = mapped_column(String(128), default="")
    progress_pct: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(24), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Task(Base):
    """施工任务（矿山：穿孔/爆破/铲装/运输/排土 等工序任务）。"""

    __tablename__ = "proj_task"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("proj_project.id"), index=True)
    code: Mapped[str] = mapped_column(String(32), index=True)
    process: Mapped[str] = mapped_column(String(32))  # drilling|blasting|loading|hauling|dumping
    process_cn: Mapped[str] = mapped_column(String(16), default="")
    start_day: Mapped[int] = mapped_column(Integer, default=0)
    end_day: Mapped[int] = mapped_column(Integer, default=0)
    quantity: Mapped[float] = mapped_column(Float, default=0)
    unit: Mapped[str] = mapped_column(String(8), default="m3")
    plan_note: Mapped[str] = mapped_column(String(256), default="")
    progress_pct: Mapped[float] = mapped_column(Float, default=0)
    actual_vs_plan: Mapped[float] = mapped_column(Float, default=0)  # 实际-计划偏差（%）
    spec: Mapped[dict] = mapped_column(JSON, default=dict)


class TaskProgress(Base):
    """工序进度记录（进度偏差分析数据来源）。"""

    __tablename__ = "proj_task_progress"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("proj_task.id"), index=True)
    day: Mapped[int] = mapped_column(Integer, default=0)
    done_qty: Mapped[float] = mapped_column(Float, default=0)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
