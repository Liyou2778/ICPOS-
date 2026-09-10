import type { ChatMsgMeta, DashboardSummary, DeviceItem, DiagnosisItem, FrontendConfig, HealthInfo, SolutionPlan, TrendPoint, WarningItem, WorkOrder } from './types';
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
  chatSessions: () => http.get<{ session_id: number; title: string; created_at: string }[]>('/chat/sessions'),
  createChat: (title: string) => http.post<{ session_id: number; title: string }>('/chat/sessions', { title }),
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
  predict: (deviceCode: string) =>
    http.get<{ device_code: string; anomaly_score: number; risky: boolean; top_code: string; top_conf: number; remaining_hours: number | null; note?: string }>(
      `/maintenance/predict/${deviceCode}`),

  // ---------- 知识库 ----------
  kbSearch: (q: string, topK = 5) =>
    http.get<{ query: string; hits: { entry_id: number; kb_type: string; title: string; excerpt: string; source: string; score: number }[] }>(
      `/kb/search?q=${encodeURIComponent(q)}&top_k=${topK}`),

  // ---------- 前端运行时配置（地图双模式） ----------
  frontendConfig: () => http.get<FrontendConfig>('/config/frontend'),
};
