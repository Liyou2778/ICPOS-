// 统一 HTTP 客户端：/api 前缀、Bearer token、统一错误（FastAPI code/message/data 约定）
const TOKEN_KEY = 'icops_token';
const USER_KEY = 'icops_user';

export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) || '';
}
export function setSession(token: string, user: { username: string; display_name: string; role: string }): void {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(USER_KEY, JSON.stringify(user));
}
export function clearSession(): void {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
}
export function getUser(): { username: string; display_name: string; role: string } | null {
  const raw = localStorage.getItem(USER_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as { username: string; display_name: string; role: string };
  } catch {
    return null;
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json', ...(options.headers as Record<string, string> | undefined) };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  const resp = await fetch(`/api${path}`, { ...options, headers });
  if (resp.status === 401 && path !== '/auth/login') {
    clearSession();
    window.location.href = '/login';
    throw new Error('登录已过期，请重新登录');
  }
  if (!resp.ok) {
    let detail = `请求失败（HTTP ${resp.status}）`;
    try {
      const body = await resp.json();
      detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail ?? body);
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return (await resp.json()) as T;
}

export const http = {
  get: <T>(path: string): Promise<T> => request<T>(path),
  post: <T>(path: string, body?: unknown): Promise<T> => request<T>(path, { method: 'POST', body: JSON.stringify(body ?? {}) }),
  put: <T>(path: string, body?: unknown): Promise<T> => request<T>(path, { method: 'PUT', body: JSON.stringify(body ?? {}) }),
};
