// 与后端响应结构对齐的领域类型
export interface HealthInfo {
  app: string;
  version: string;
  llm_mode: string;
  predictive_ready: boolean;
  data: { equipment_models: number; fault_codes: number; kb_entries: number; vector_chunks: number };
}

export interface DeviceItem {
  id?: number;
  code: string;
  name: string;
  model_code?: string;
  category?: string;
  category_cn?: string;
  work_state: 'working' | 'idle' | 'fault' | 'maintenance' | string;
  lat: number;
  lng: number;
  cur_load_t: number;
  status_note?: string;
}

export interface DashboardSummary {
  device_total: number;
  device_state: Record<string, number>;
  idle_rate: number;
  warnings_open: number;
  workorders_total: number;
  yield_today_wan_t: number;
  project: { name: string; progress_pct: number };
}

export interface TrendPoint {
  day: number;
  date: string;
  moved_t: number;
  work_hours: number;
  fuel_rate_avg: number;
  faults: number;
}

export interface BundleOption {
  model_code: string;
  model_name: string;
  category_cn: string;
  count: number;
  unit_price_cny: number;
  total_price_cny: number;
  specs: Record<string, number | string>;
}

export interface TcoRow {
  model_code: string;
  model_name: string;
  count: number;
  purchase_cny: number;
  energy_3y_cny: number;
  maintenance_3y_cny: number;
  residual_cny: number;
  total_3y_cny: number;
  per_ton_cost_cny: number;
}

export interface SolutionBundle {
  name: string;
  fleet: BundleOption[];
  tco: TcoRow[];
  fleet_total_cny: number;
  tco_3y_total_cny: number;
  daily_capacity_t: number;
  utilization_est: number;
  summary: string;
  note: string;
}

export interface SolutionPlan {
  doc_type: string;
  title: string;
  requirement: Record<string, unknown>;
  bundles: SolutionBundle[];
  best_index: number;
  chapters: { chapter: string; paragraphs: string[] }[];
  bid_response_check?: { clause: string; status: string; note: string }[];
  citations: { entry_id: number; title: string; kb_type: string; source: string; version: string }[];
  document_id?: number;
}

export interface ChatMsgMeta {
  agent: string;
  citations: { entry_id: number; title: string; kb_type: string; source: string; version: string }[];
  transfer: boolean;
  provider?: string;
  degraded?: boolean;
  missing_slots?: string[];
  slot_form?: SlotField[];
  slot_summary?: { key: string; label: string; value: string }[];
}

export interface DiagnosisItem {
  code: string;
  name: string;
  confidence: number;
  category: string;
  severity: string;
}

export interface WorkOrder {
  code: string;
  device_code: string;
  fault_desc: string;
  fault_code: string;
  diagnosis: { code: string; name: string; confidence: number }[];
  fix_plan: string;
  parts: { name: string; qty: number; price_cny: number; stock: number; action: string }[];
  est_hours: number;
  engineer: string;
  status: string;
  severity?: string;
  labor_hours?: number;
  parts_used?: { name: string; qty: number }[];
  repair_notes?: string;
  status_cn?: string;
  status_color?: string;
}

export interface WorkOrderTimelineItem {
  from: string;
  from_cn: string;
  to: string;
  to_cn: string;
  note: string;
  operator: string;
  at: string;
}

export interface WorkOrderDetail extends WorkOrder {
  status_cn: string;
  status_color: string;
  timeline: WorkOrderTimelineItem[];
  created_at?: string;
  dispatched_at?: string;
  repair_started_at?: string;
  acceptance_at?: string;
  completed_at?: string;
  archived_at?: string;
}

export interface OpsDevice {
  code: string;
  name: string;
  model_code: string;
  model_name: string;
  category_cn: string;
  work_state: string;
  lat: number;
  lng: number;
  work_hours: number;
  idle_hours: number;
  utilization: number;
  fuel_l: number;
  health_score: number;
  open_warnings: number;
  high_warnings: number;
  maintenance_due: boolean;
  plan_items: string[];
  note: string;
}

export interface OperationsData {
  summary: {
    device_total: number;
    working: number;
    fault: number;
    avg_utilization: number;
    open_warnings: number;
    maintenance_due: number;
    spare_alerts: number;
    generated_at: string;
  };
  devices: OpsDevice[];
  spare_alerts: { sku: string; name: string; stock: number; price_cny: number; action: string }[];
  plan_due: { device_code: string; item: string; due_hours: number; window: string; note: string }[];
  status_flow: { key: string; label: string }[];
}

export interface ArchiveData {
  summary: {
    total_orders: number;
    archived: number;
    completed: number;
    in_progress: number;
    mttr_hours: number | null;
    recurrence_devices: number;
  };
  archived: WorkOrderDetail[];
  fault_distribution: { fault_code: string; count: number }[];
  parts_consumption: { name: string; qty: number }[];
  recurrence: { device_code: string; orders: number; note: string }[];
  status_flow: { key: string; label: string }[];
}

export interface SlotField {
  key: string;
  label: string;
  question: string;
  kind: 'select' | 'number' | 'text';
  required: boolean;
  unit?: string;
  options?: { value: string; label: string }[];
  filled: boolean;
}

export interface ChatSessionItem {
  session_id: number;
  title: string;
  status: 'active' | 'archived';
  tags: string;
  slot_summary: { key: string; label: string; value: string }[];
  pending: boolean;
  updated_at: string;
  archived_at: string;
}

export interface ChatSessionsGrouped {
  active: ChatSessionItem[];
  archived: ChatSessionItem[];
  counts: { active: number; archived: number };
}

export interface WarningItem {
  id: number;
  device_code: string;
  fault_code: string;
  predicted_part: string;
  probable_cause: string;
  severity: 'H' | 'M' | 'L' | string;
  advice: string;
  remaining_hours: number;
  model_conf: number;
  status: string;
  created_at: string;
}

export interface FrontendConfig {
  amap_enabled: boolean;
  amap_key: string;
  amap_security_code: string;
}

export interface CorpusDevice {
  device_id: string;
  model: string;
  category: string;
  role: string;
  power_kw?: number;
  mass_kg?: number;
  bucket_m3?: number;
}

export interface CorpusFault {
  device_id: string;
  code: string;
  component: string;
  mode: string;
  onset_ts: string;
  detect_ts: string;
  lead_hours: number;
  severity: string;
}

export interface CorpusClassScore {
  class: string;
  class_cn: string;
  proba: number;
  threshold: number;
  exceed: boolean;
  score: number;
}

export interface CorpusPrediction {
  device_id: string;
  at?: string;
  risky: boolean;
  top_class?: string;
  top_class_cn?: string;
  top_conf?: number;
  classes: CorpusClassScore[];
  remaining_hours?: number | null;
  note?: string;
  model_note?: string;
}

// ---------- 项目运营分析 ----------
export interface ProjectRow {
  code: string;
  name: string;
  industry: string;
  region: string;
  data_type: string;
  section_est_total_yuan: number;
  plan_invest_yuan: number;
  duration_days: number;
  total_cost_yuan: number;
  cost_to_budget_ratio: number | null;
}

export interface CostStructureItem {
  cost_type: string;
  amount_yuan: number;
  share_pct: number;
  baseline_mean_pct: number | null;
  band_pct: (number | null)[];
  deviation_pp: number | null;
  verdict: string;
}

export interface CostStructure {
  project: { code: string; name: string; industry: string; region: string; data_type: string };
  periods: string[];
  total_cost_yuan: number;
  items: CostStructureItem[];
  out_of_band: string[];
  budget_execution: {
    section_est_total_yuan: number;
    total_cost_yuan: number;
    ratio: number;
    band: { p25: number; p50: number; p75: number; p90: number };
    level: string;
  } | null;
  conclusion: string;
  basis: string;
  disclaimer: string;
}
