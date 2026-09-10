"""领域 DTO：智能体/服务/API 之间交换的结构化对象。"""

from __future__ import annotations

from pydantic import BaseModel, Field


# ---------- 需求解析 ----------
class ParsedRequirement(BaseModel):
    """需求分析智能体输出：结构化需求参数（对齐 PRD 表12 需求分析 Agent 输入/输出）。"""

    scene_type: str = ""  # mining | earthwork | agriculture
    scene_cn: str = ""
    annual_t: float = 0.0  # 年产能（吨/年）
    daily_t: float = 0.0  # 折算日产能（吨/日）
    duration_years: float = 0.0  # 工期（年）
    total_t: float = 0.0  # 总工程量（吨）
    budget_cny: float = 0.0  # 预算（元）
    constraints: list[str] = Field(default_factory=list)  # 如 电动化/高原/纯电
    missing: list[str] = Field(default_factory=list)  # 缺失关键参数（追问）
    raw: str = ""
    followup_questions: list[str] = Field(default_factory=list)


# ---------- 选型 / TCO ----------
class TcoRow(BaseModel):
    """三年 TCO（四大类：购置/能耗/维保/残值）。"""

    model_code: str
    model_name: str
    count: int = 1
    purchase_cny: float = 0.0
    energy_3y_cny: float = 0.0
    maintenance_3y_cny: float = 0.0
    residual_cny: float = 0.0
    total_3y_cny: float = 0.0
    per_ton_cost_cny: float = 0.0  # 单位成本 元/吨


class EquipmentOption(BaseModel):
    """单型号设备选型条目。"""

    model_code: str
    model_name: str
    brand: str = ""
    category_cn: str = ""
    count: int = 1
    unit_price_cny: float = 0.0
    total_price_cny: float = 0.0
    specs: dict = Field(default_factory=dict)
    score: float = 0.0
    reasons: list[str] = Field(default_factory=list)


class Bundle(BaseModel):
    """一套组合方案（设备组 + TCO 汇总）。"""

    name: str
    fleet: list[EquipmentOption] = Field(default_factory=list)
    tco: list[TcoRow] = Field(default_factory=list)
    fleet_total_cny: float = 0.0
    tco_3y_total_cny: float = 0.0
    daily_capacity_t: float = 0.0
    utilization_est: float = 0.0
    summary: str = ""
    note: str = ""


class SelectionResult(BaseModel):
    """方案生成智能体选型部分输出（≥3 套）。"""

    bundles: list[Bundle] = Field(default_factory=list)
    best_index: int = 0
    assumptions: list[str] = Field(default_factory=list)
    citations: list[dict] = Field(default_factory=list)
    generated_at: str = ""


# ---------- 施工组织 ----------
class GanttItem(BaseModel):
    process: str
    process_cn: str
    start_day: int
    end_day: int
    key_path: bool = False
    note: str = ""


class ConstructionPlan(BaseModel):
    """施工组织设计（进度甘特 + 设备分配 + 瓶颈分析）。"""

    process_sequence: list[str] = Field(default_factory=list)
    gantt: list[GanttItem] = Field(default_factory=list)
    fleet: list[EquipmentOption] = Field(default_factory=list)
    quality_control: list[str] = Field(default_factory=list)
    safety_risks: list[str] = Field(default_factory=list)
    bottlenecks: list[str] = Field(default_factory=list)
    citations: list[dict] = Field(default_factory=list)


# ---------- 调度 ----------
class DispatchAssignment(BaseModel):
    order_no: int
    device_code: str
    device_name: str = ""
    process_cn: str = ""
    from_point: str
    to_point: str
    distance_km: float = 0.0
    load_t: float = 0.0
    cycle_min: float = 0.0
    reason: str = ""  # 可解释性：为什么派这台设备


class DispatchPlanOut(BaseModel):
    plan_id: int = 0
    plan_type: str = "ai"
    trigger: str = "initial"
    assignments: list[DispatchAssignment] = Field(default_factory=list)
    stats: dict = Field(default_factory=dict)  # idle_rate/utilization/avg_wait_min/empty_ratio
    note: str = ""
    is_suggestion: bool = True  # 全部调度结论为“建议执行”，需人工确认（PRD 安全边界）


class ABReport(BaseModel):
    project_name: str = ""
    metrics: dict = Field(default_factory=dict)  # manual vs ai 关键指标
    improvements: dict = Field(default_factory=dict)  # 空载率下降等
    evidence_files: list[str] = Field(default_factory=list)
    conclusion: str = ""


# ---------- 运维 ----------
class DiagnosisItem(BaseModel):
    code: str
    name: str
    confidence: float
    category: str = ""
    severity: str = "M"


class DiagnoseResult(BaseModel):
    text: str = ""
    code: str = ""
    top3: list[DiagnosisItem] = Field(default_factory=list)  # ≥3 诊断结果及置信度
    fix_plan: str = ""
    parts: list[dict] = Field(default_factory=list)
    est_hours: float = 0.0
    engineer: str = ""


class WorkOrderOut(BaseModel):
    code: str
    device_code: str = ""
    fault_desc: str = ""
    fault_code: str = ""
    diagnosis: list[dict] = Field(default_factory=list)
    fix_plan: str = ""
    parts: list[dict] = Field(default_factory=list)
    est_hours: float = 0.0
    engineer: str = ""
    status: str = "created"


class PredictionOut(BaseModel):
    device_code: str = ""
    anomaly_score: float = 0.0
    risky: bool = False
    top_code: str = ""
    top_conf: float = 0.0
    remaining_hours: float = 0.0
    note: str = ""


# ---------- 编排 ----------
class AgentRunResult(BaseModel):
    agent: str
    ok: bool = True
    message: str = ""
    payload: dict = Field(default_factory=dict)
    citations: list[dict] = Field(default_factory=list)
    human_transfer: bool = False
