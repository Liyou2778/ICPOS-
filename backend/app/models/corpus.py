"""全域语料（agent_train.jsonl / project_test.jsonl）入库模型。

设计原则：
  * **全量暂存**：`corpus_record` 以 (entity_type, record_key) 为幂等键保存全部 16184 条原始记录
    （payload 原样 JSON），保证任何后续训练都可从原始口径重建，不做有损映射；
  * **类型化表**：对训练/评测/RAG 直接消费的实体另建强类型表，避免训练脚本解析 JSON；
  * **可溯源**：每条记录保留 split（agent_train/project_test）、data_origin（real_public/simulated_fallback）、
    source_name/source_url 与 is_example，训练样本筛选与口径声明的唯一依据。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class CorpusRecord(Base):
    """全域语料暂存（17 类实体全量保留，幂等键= entity_type + record_key）。"""

    __tablename__ = "corpus_record"
    __table_args__ = (UniqueConstraint("entity_type", "record_key", name="uq_corpus_entity_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(32), index=True)
    record_key: Mapped[str] = mapped_column(String(64), index=True)
    split: Mapped[str] = mapped_column(String(16), index=True)  # agent_train | project_test
    data_origin: Mapped[str] = mapped_column(String(24), default="")  # real_public | simulated_fallback
    is_example: Mapped[bool] = mapped_column(Boolean, default=True)
    source_name: Mapped[str] = mapped_column(String(160), default="")
    source_url: Mapped[str] = mapped_column(String(320), default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProjOperation(Base):
    """项目运营记录（mining_project_operation，2500 条）：工期/成本偏差建模的唯一数据源。"""

    __tablename__ = "corpus_proj_operation"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    record_key: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    split: Mapped[str] = mapped_column(String(16), index=True)
    project_id: Mapped[str] = mapped_column(String(32), index=True)
    project_name: Mapped[str] = mapped_column(String(96), default="")
    region: Mapped[str] = mapped_column(String(48), default="")
    scenario_type: Mapped[str] = mapped_column(String(32), default="")
    terrain: Mapped[str] = mapped_column(String(32), default="")
    quality_standard: Mapped[str] = mapped_column(String(96), default="")
    constraints: Mapped[dict] = mapped_column(JSON, default=dict)
    planned_start: Mapped[str] = mapped_column(String(16), default="")
    planned_duration_days: Mapped[int] = mapped_column(Integer, default=0)
    actual_duration_days: Mapped[int] = mapped_column(Integer, default=0)
    schedule_deviation_days: Mapped[int] = mapped_column(Integer, default=0)
    engineering_quantity_t: Mapped[float] = mapped_column(Float, default=0)
    budget_cny: Mapped[float] = mapped_column(Float, default=0)
    actual_cost_cny: Mapped[float] = mapped_column(Float, default=0)
    construction_task: Mapped[str] = mapped_column(String(16), default="", index=True)
    task_status: Mapped[str] = mapped_column(String(16), default="")
    equipment_count: Mapped[int] = mapped_column(Integer, default=0)
    data_quality: Mapped[str] = mapped_column(String(64), default="")
    source_name: Mapped[str] = mapped_column(String(160), default="")


class EvalQA(Base):
    """Agent 评测问答（evaluation_qa，756 条；其中 114 条 real_public 来自徐工官网）。"""

    __tablename__ = "corpus_eval_qa"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    qa_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    split: Mapped[str] = mapped_column(String(16), index=True)
    data_origin: Mapped[str] = mapped_column(String(24), default="")
    source_name: Mapped[str] = mapped_column(String(160), default="")
    source_url: Mapped[str] = mapped_column(String(320), default="")
    question: Mapped[str] = mapped_column(Text)
    expected_answer: Mapped[str] = mapped_column(Text)
    reference_source: Mapped[str] = mapped_column(String(320), default="")
    category: Mapped[str] = mapped_column(String(32), default="", index=True)


class EquipPriceTco(Base):
    """设备价格与 TCO（equipment_price_tco，15 条）：设备选型报价口径的结构化来源。"""

    __tablename__ = "corpus_equip_price_tco"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    price_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    split: Mapped[str] = mapped_column(String(16), default="")
    model_name: Mapped[str] = mapped_column(String(48), index=True)
    equipment_subtype: Mapped[str] = mapped_column(String(32), default="")
    purchase_price_cny: Mapped[float] = mapped_column(Float, default=0)
    energy_type: Mapped[str] = mapped_column(String(16), default="")
    energy_cost_per_hour_cny: Mapped[float] = mapped_column(Float, default=0)
    maintenance_cost_per_hour_cny: Mapped[float] = mapped_column(Float, default=0)
    residual_value_rate_3y: Mapped[float] = mapped_column(Float, default=0)
    annual_operation_hours: Mapped[float] = mapped_column(Float, default=0)
    three_year_tco_cny: Mapped[float] = mapped_column(Float, default=0)
    source_name: Mapped[str] = mapped_column(String(200), default="")


class FaultCase(Base):
    """故障案例（fault_case，105 条）：诊断/维修步骤与备件建议的知识来源。"""

    __tablename__ = "corpus_fault_case"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    split: Mapped[str] = mapped_column(String(16), default="")
    source_name: Mapped[str] = mapped_column(String(160), default="")
    equipment_subtype: Mapped[str] = mapped_column(String(32), default="", index=True)
    fault_code: Mapped[str] = mapped_column(String(16), default="", index=True)
    fault_type: Mapped[str] = mapped_column(String(48), default="")
    abnormal_part: Mapped[str] = mapped_column(String(48), default="")
    root_cause: Mapped[str] = mapped_column(Text, default="")
    severity: Mapped[str] = mapped_column(String(16), default="")
    repair_steps: Mapped[list] = mapped_column(JSON, default=list)
    recommended_parts: Mapped[list] = mapped_column(JSON, default=list)
    maintenance_interval_hours: Mapped[int] = mapped_column(Integer, default=0)
    estimated_downtime_hours: Mapped[float] = mapped_column(Float, default=0)


class CorpusTelemetry(Base):
    """语料遥测（equipment_telemetry，10800 条）：预测性维护模型的重训数据源。"""

    __tablename__ = "corpus_telemetry"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    record_key: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    split: Mapped[str] = mapped_column(String(16), index=True)
    device_id: Mapped[str] = mapped_column(String(32), index=True)
    model_name: Mapped[str] = mapped_column(String(48), default="")
    equipment_subtype: Mapped[str] = mapped_column(String(32), default="")
    project_id: Mapped[str] = mapped_column(String(32), default="")
    timestamp: Mapped[str] = mapped_column(String(32), index=True)
    temperature_c: Mapped[float] = mapped_column(Float, default=0)
    hydraulic_pressure_mpa: Mapped[float] = mapped_column(Float, default=0)
    vibration: Mapped[float] = mapped_column(Float, default=0)
    fuel_consumption_lph: Mapped[float] = mapped_column(Float, default=0)
    load_t: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(16), default="")
    construction_task: Mapped[str] = mapped_column(String(16), default="")
