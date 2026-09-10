import { getToken } from '../api/client';

export type SSEHandlers = {
  onEvent: (event: string, data: Record<string, unknown>) => void;
  onError?: (err: unknown) => void;
};

/** 基于 fetch 的 SSE 流式解析（对话接口 event: route/delta/citations/done/error） */
export async function streamEvents(path: string, payload: unknown, handlers: SSEHandlers, signal?: AbortSignal): Promise<void> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json', Accept: 'text/event-stream' };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  const resp = await fetch(`/api${path}`, { method: 'POST', headers, body: JSON.stringify(payload), signal });
  if (!resp.ok || !resp.body) {
    let msg = `HTTP ${resp.status}`;
    try {
      const b = await resp.json();
      msg = typeof b.detail === 'string' ? b.detail : msg;
    } catch {
      /* ignore */
    }
    throw new Error(msg);
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const blocks = buffer.split('\n\n');
    buffer = blocks.pop() ?? '';
    for (const block of blocks) {
      let event = 'message';
      const dataLines: string[] = [];
      for (const line of block.split('\n')) {
        if (line.startsWith('event:')) event = line.slice(6).trim();
        else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim());
      }
      if (!dataLines.length) continue;
      try {
        handlers.onEvent(event, JSON.parse(dataLines.join('\n')));
      } catch (e) {
        handlers.onError?.(e);
      }
    }
  }
}
