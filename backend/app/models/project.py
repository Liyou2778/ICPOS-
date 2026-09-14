"""项目域：项目 / 施工任务 / 工序进度（表4-3 项目域）。"""

from __future__ import annotations

from datetime import datetime, UTC

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class Project(Base):
    """工程项目（矿山开采/剥离主线；含真实招标锚点字段，来自项目运营语料）。"""

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
    # ---- 招标锚点（real 数据，用于项目运营与成本分析）----
    tenderer: Mapped[str] = mapped_column(String(128), default="")  # 招标人
    industry: Mapped[str] = mapped_column(String(64), default="")  # 行业
    region: Mapped[str] = mapped_column(String(64), default="")  # 地区
    platform: Mapped[str] = mapped_column(String(128), default="")  # 交易平台
    publish_date: Mapped[str] = mapped_column(String(32), default="")  # 公告发布
    plan_invest_yuan: Mapped[float] = mapped_column(Float, default=0)  # 计划总投资（元）
    section_est_total_yuan: Mapped[float] = mapped_column(Float, default=0)  # 标段预算合计（元）
    win_amount_yuan: Mapped[float] = mapped_column(Float, default=0)  # 中标金额（元）
    funding_source: Mapped[str] = mapped_column(String(64), default="")  # 资金来源
    approval_authority: Mapped[str] = mapped_column(String(128), default="")
    source_url: Mapped[str] = mapped_column(String(256), default="")
    source_site: Mapped[str] = mapped_column(String(128), default="")
    data_type: Mapped[str] = mapped_column(String(16), default="")  # real|simulated
    tender_meta: Mapped[dict] = mapped_column(JSON, default=dict)  # 其余锚点字段
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
    # ---- 项目运营语料扩展：排期 / 工作量 / 资源 / 状态 ----
    task_code: Mapped[str] = mapped_column(String(32), default="", index=True)  # 语料 task_id
    phase: Mapped[str] = mapped_column(String(32), default="")  # 阶段
    cycle: Mapped[int] = mapped_column(Integer, default=0)  # 第 N 循环
    plan_start: Mapped[str] = mapped_column(String(16), default="")
    plan_end: Mapped[str] = mapped_column(String(16), default="")
    actual_start: Mapped[str] = mapped_column(String(16), default="")
    actual_end: Mapped[str] = mapped_column(String(16), default="")
    workload: Mapped[float] = mapped_column(Float, default=0)
    workload_unit: Mapped[str] = mapped_column(String(8), default="")
    status: Mapped[str] = mapped_column(String(16), default="")
    team: Mapped[str] = mapped_column(String(64), default="")
    device_ids: Mapped[str] = mapped_column(String(128), default="")
    device_models: Mapped[str] = mapped_column(String(256), default="")
    data_type: Mapped[str] = mapped_column(String(16), default="")
    sched_meta: Mapped[dict] = mapped_column(JSON, default=dict)


class ProjectCost(Base):
    """项目成本台账（actual_cost 语料入库；支撑成本构成分析与模型训练）。"""

    __tablename__ = "proj_cost"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cost_key: Mapped[str] = mapped_column(String(96), unique=True, index=True)  # 语料 _id（幂等键）
    project_id: Mapped[int] = mapped_column(ForeignKey("proj_project.id"), index=True)
    cost_date: Mapped[str] = mapped_column(String(16), default="")
    period: Mapped[str] = mapped_column(String(8), default="")  # 2026-08
    cost_item: Mapped[str] = mapped_column(String(32), default="")  # 人工费/材料费/...
    cost_type: Mapped[str] = mapped_column(String(16), default="", index=True)  # 人工/材料/机械/其他/管理
    amount: Mapped[float] = mapped_column(Float, default=0)  # 元
    unit: Mapped[str] = mapped_column(String(8), default="元")
    payee: Mapped[str] = mapped_column(String(64), default="")
    invoice_no: Mapped[str] = mapped_column(String(64), default="")
    badge_no: Mapped[str] = mapped_column(String(64), default="")
    data_type: Mapped[str] = mapped_column(String(16), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TaskProgress(Base):
    """工序进度记录（进度偏差分析数据来源）。"""

    __tablename__ = "proj_task_progress"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("proj_task.id"), index=True)
    day: Mapped[int] = mapped_column(Integer, default=0)
    done_qty: Mapped[float] = mapped_column(Float, default=0)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
