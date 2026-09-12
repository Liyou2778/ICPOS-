import type {
  ArchiveData,
  ChatMsgMeta,
  ChatSessionsGrouped,
  CorpusDevice,
  CorpusFault,
  CorpusPrediction,
  DashboardSummary,
  DeviceItem,
  DiagnosisItem,
  FrontendConfig,
  HealthInfo,
  OperationsData,
  SlotField,
  SolutionPlan,
  TrendPoint,
  WarningItem,
  WorkOrder,
  WorkOrderDetail,
} from './types';
import { http } from './client';

// ---------- 系统 / 鉴权 ----------
export const api = {
  login: (username: string, password: string) =>
    http.post<{ token: string; user: { username: string; display_name: string; role: string } }>('/auth/login', { username, password }),
  me: () => http.get<{ username: string; display_name: string; role: string }>('/auth/me'),
  health: () => http.get<HealthInfo>('/health'),
  bootstrap: () => http.post<{ log: string[] }>('/admin/bootstrap'),

  // ---------- 驾驶舱 ----------
  dashboard: () => http.get<DashboardSummary>('/dashboard/summary'),
  devices: () => http.get<DeviceItem[]>('/dashboard/devices'),
  trends: () => http.get<{ points: TrendPoint[] }>('/dashboard/trends'),

  // ---------- 方案 ----------
  plan: (text: string, doc_type: string) =>
    http.post<{ status: string; payload: SolutionPlan; facts?: string[]; citations?: unknown[] }>('/solutions/plan', { text, doc_type }),
  exportDoc: (payload: SolutionPlan, doc_type: string, title: string) =>
    http.post<{ docx?: string; pdf?: string | null; note?: string }>('/solutions/export', { payload, doc_type, title }),

  // ---------- 对话 ----------
  chatSessions: (status: 'all' | 'active' | 'archived' = 'all') =>
    http.get<ChatSessionsGrouped>(`/chat/sessions?status=${status}`),
  createChat: (title: string) => http.post<{ session_id: number; title: string }>('/chat/sessions', { title }),
  patchChat: (sid: number, body: { title?: string; tags?: string; status?: 'active' | 'archived' }) =>
    http.patch<{ session_id: number; title: string; status: string; tags: string }>(`/chat/sessions/${sid}`, body),
  submitSlots: (sid: number, values: Record<string, string>) =>
    http.post<{
      message_id: number; agent: string; content: string; need_more: boolean;
      missing_slots?: string[]; slot_form?: SlotField[];
      slot_summary?: { key: string; label: string; value: string }[];
      citations?: { entry_id: number; title: string; kb_type: string; source: string; version: string }[];
      transfer?: boolean; provider?: string; degraded?: boolean;
    }>(`/chat/sessions/${sid}/slots`, { values }),
  chatMessages: (sid: number) =>
    http.get<{ id: number; role: string; content: string; meta?: ChatMsgMeta; created_at: string }[]>(`/chat/sessions/${sid}/messages`),

  // ---------- 调度 ----------
  dispatchRun: (trigger = 'initial', faultDeviceCode?: string) =>
    http.post<{ plan_id: number; trigger: string; assignments: unknown[]; stats: Record<string, number>; note: string }>(
      '/dispatch/run', { trigger, fault_device_code: faultDeviceCode || null }),
  dispatchConfirm: (planId: number) => http.post('/dispatch/confirm', { plan_id: planId }),
  dispatchAB: () => http.get<{ metrics: Record<string, number>; improvements: Record<string, number>; conclusion: string }>('/dispatch/ab'),
  trajectory: (deviceCode: string, limit = 240) =>
    http.get<{ device_code: string; points: { ts: string; lat: number; lng: number; state: string; speed_kmh: number; load_t: number }[] }>(
      `/dispatch/trajectory?device_code=${deviceCode}&limit=${limit}`),

  // ---------- 运维 ----------
  diagnose: (text: string) => http.post<{ text: string; code: string; top3: DiagnosisItem[]; fix_plan: string; parts: { name: string }[]; est_hours: number; engineer: string }>('/maintenance/diagnose', { text }),
  workorders: () => http.get<WorkOrder[]>('/maintenance/workorders'),
  createWorkorder: (deviceCode: string, text: string, code = '') =>
    http.post<WorkOrder>('/maintenance/workorders', { device_code: deviceCode, text, code }),
  warnings: (status = '') => http.get<WarningItem[]>(`/maintenance/warnings${status ? `?status=${status}` : ''}`),
  operations: () => http.get<OperationsData>('/maintenance/operations'),
  workorderDetail: (code: string) => http.get<WorkOrderDetail>(`/maintenance/workorders/${code}`),
  advanceWorkorder: (
    code: string,
    body: { to_status: string; note?: string; operator?: string; labor_hours?: number; repair_notes?: string; parts_used?: { name: string; qty: number }[] },
  ) => http.patch<WorkOrderDetail>(`/maintenance/workorders/${code}/status`, body),
  archive: () => http.get<ArchiveData>('/maintenance/archive'),
  predict: (deviceCode: string) =>
    http.get<{ device_code: string; anomaly_score: number; risky: boolean; top_code: string; top_conf: number; remaining_hours: number | null; note?: string }>(
      `/maintenance/predict/${deviceCode}`),

  // ---------- 知识库 ----------
  kbSearch: (q: string, topK = 5) =>
    http.get<{ query: string; hits: { entry_id: number; kb_type: string; title: string; excerpt: string; source: string; score: number }[] }>(
      `/kb/search?q=${encodeURIComponent(q)}&top_k=${topK}`),

  // ---------- 语料模型（企业级训练产物在线推理） ----------
  corpusDevices: () => http.get<{ devices: CorpusDevice[]; ready: boolean }>('/maintenance/corpus/devices'),
  corpusFaults: () => http.get<{ faults: CorpusFault[] }>('/maintenance/corpus/faults'),
  corpusReport: () => http.get<Record<string, any>>('/maintenance/corpus/model-report'),
  predictCorpus: (deviceId: string, at?: string) =>
    http.get<CorpusPrediction>(
      `/maintenance/predict-corpus/${encodeURIComponent(deviceId)}${at ? `?at=${encodeURIComponent(at)}` : ''}`),

  // ---------- 前端运行时配置（地图双模式） ----------
  frontendConfig: () => http.get<FrontendConfig>('/config/frontend'),
};
