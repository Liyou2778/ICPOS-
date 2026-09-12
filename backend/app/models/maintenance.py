"""运维域：故障 / 预警 / 工单 / 维保计划（表4-3 运维域）。"""

from __future__ import annotations

from datetime import datetime, UTC

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class FaultCode(Base):
    """故障代码知识（维保知识库结构化主表，≥50 条，对应维修方案/备件/保养定额）。"""

    __tablename__ = "oms_fault_code"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(24), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))  # 故障名称/描述
    category: Mapped[str] = mapped_column(String(16), index=True)  # ENG|HYD|ELE|TRN|UND|...
    severity: Mapped[str] = mapped_column(String(8), default="M")  # H(高)/M(中)/L(低)
    causes: Mapped[str] = mapped_column(Text, default="")  # 可能原因（分号分隔）
    fix_plan: Mapped[str] = mapped_column(Text, default="")  # 维修方案
    parts: Mapped[str] = mapped_column(String(512), default="")  # 所需备件
    est_hours: Mapped[float] = mapped_column(Float, default=2)  # 预计维修时长
    maintain_cost_cny: Mapped[float] = mapped_column(Float, default=0)  # 保养/维修定额
    keywords: Mapped[str] = mapped_column(String(512), default="")  # 自然语言匹配关键词
    source: Mapped[str] = mapped_column(String(128), default="维保知识库（公开资料整理）")


class Warning(Base):
    """故障预警（预警五要素：异常部件/可能原因/严重等级/建议措施/剩余可用时间）。"""

    __tablename__ = "oms_warning"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("dev_device.id"), index=True)
    fault_code: Mapped[str] = mapped_column(String(24), default="")
    predicted_part: Mapped[str] = mapped_column(String(128), default="")
    probable_cause: Mapped[str] = mapped_column(String(256), default="")
    severity: Mapped[str] = mapped_column(String(8), default="M")
    advice: Mapped[str] = mapped_column(String(512), default="")
    remaining_hours: Mapped[float] = mapped_column(Float, default=0)  # 预计剩余可用时间 h
    model_conf: Mapped[float] = mapped_column(Float, default=0)  # 模型置信度
    status: Mapped[str] = mapped_column(String(24), default="open")  # open|converted|closed
    source: Mapped[str] = mapped_column(String(32), default="predictive")  # predictive|diagnose
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WorkOrder(Base):
    """维修工单（自动生成：故障描述/诊断/方案/备件/时长/推荐工程师 六项信息）。"""

    __tablename__ = "oms_work_order"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    warning_id: Mapped[int | None] = mapped_column(ForeignKey("oms_warning.id"), nullable=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("dev_device.id"), index=True)
    fault_desc: Mapped[str] = mapped_column(Text, default="")
    fault_code: Mapped[str] = mapped_column(String(24), default="")
    diagnosis: Mapped[list] = mapped_column(JSON, default=list)  # Top3 诊断与置信度
    fix_plan: Mapped[str] = mapped_column(Text, default="")
    parts: Mapped[list] = mapped_column(JSON, default=list)  # 备件清单 [{sku,name,qty,price}]
    est_hours: Mapped[float] = mapped_column(Float, default=0)
    engineer: Mapped[str] = mapped_column(String(64), default="")  # 推荐/指派工程师
    # 六状态流转：created 已生成 → dispatched 已派工 → repairing 维修中
    #            → pending_acceptance 待验收 → completed 已完成 → archived 已归档
    status: Mapped[str] = mapped_column(String(24), default="created", index=True)
    severity: Mapped[str] = mapped_column(String(8), default="M")
    repair_notes: Mapped[str] = mapped_column(Text, default="")  # 维修记录
    labor_hours: Mapped[float] = mapped_column(Float, default=0)  # 实际工时
    parts_used: Mapped[list] = mapped_column(JSON, default=list)  # 实际消耗备件
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    repair_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acceptance_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WorkOrderEvent(Base):
    """工单事件（状态流转留痕）：谁在何时把工单从什么状态推进到什么状态。"""

    __tablename__ = "oms_work_order_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workorder_code: Mapped[str] = mapped_column(String(32), index=True)
    from_status: Mapped[str] = mapped_column(String(24), default="")
    to_status: Mapped[str] = mapped_column(String(24), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    operator: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MaintenancePlan(Base):
    """维保计划（推荐保养时间窗口，结合施工进度）。"""

    __tablename__ = "oms_maintenance_plan"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("dev_device.id"), index=True)
    item: Mapped[str] = mapped_column(String(128), default="")  # 保养项目
    due_hours: Mapped[float] = mapped_column(Float, default=0)  # 下次保养里程/小时
    window_start_day: Mapped[int] = mapped_column(Integer, default=0)
    window_end_day: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="planned")
    note: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
