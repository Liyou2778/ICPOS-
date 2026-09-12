import { useCallback, useEffect, useRef, useState } from 'react';
import { Button, Card, Col, Empty, Input, List, Row, Space, Tag, Typography, message } from 'antd';
import { PlusOutlined, SendOutlined, RobotOutlined } from '@ant-design/icons';
import { api } from '../api';
import type { ChatMsgMeta } from '../api/types';
import { streamEvents } from '../utils/sse';

const AGENT_CN: Record<string, string> = {
  solution: '方案生成智能体', requirement: '需求分析智能体', dispatch: '施工调度智能体',
  maintenance: '设备运维智能体', status: '运营状态查询', kb_qa: '知识库问答(RAG)',
  human: '人工客服', solution_followup: '方案追问（多轮）',
};
const AGENT_COLOR: Record<string, string> = { solution: 'blue', requirement: 'purple', dispatch: 'cyan', maintenance: 'volcano', human: 'red' };

interface Msg { id: number; role: string; content: string; meta?: ChatMsgMeta }

const DEMO_QUICK = ['年产200万吨，工期3年，预算1.5亿元，帮我生成矿山施工方案', '矿卡 T02 故障了，重新调度', '矿卡液压油温高漏油，帮我诊断并生成工单', '露天矿爆破单耗一般取多少？', 'T04 会不会出问题？帮我预测一下', '转人工客服'];

export default function ChatCenter() {
  const [sessions, setSessions] = useState<{ session_id: number; title: string }[]>([]);
  const [sid, setSid] = useState<number | null>(null);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);
  const msgSeq = useRef(1);

  const refreshSessions = useCallback(async () => {
    try {
      const list = await api.chatSessions();
      setSessions(list);
      if (list.length && sid == null) openSession(list[0].session_id);
    } catch { /* ignore */ }
  }, [sid]);

  useEffect(() => { refreshSessions(); }, [refreshSessions]);

  const openSession = async (id: number) => {
    setSid(id);
    try {
      const rows = await api.chatMessages(id);
      setMsgs(rows.map((m) => ({ ...m, meta: m.meta as ChatMsgMeta | undefined })));
    } catch { setMsgs([]); }
  };

  const newSession = async (): Promise<number> => {
    const s = await api.createChat('新对话');
    await refreshSessions();
    setSid(s.session_id);
    setMsgs([]);
    return s.session_id;
  };

  const push = (role: string, content: string, meta?: ChatMsgMeta) => {
    const m: Msg = { id: msgSeq.current++, role, content, meta };
    setMsgs((prev) => {
      const i = prev.findIndex((x) => x.id === m.id);
      if (i < 0) return [...prev, m];
      const next = [...prev];
      next[i] = m;
      return next;
    });
    return m.id;
  };

  const send = async () => {
    const text = input.trim();
    if (!text || sending) return;
    // 无会话则新建，并立刻拿到会话 id（避免用旧的 null 拼 URL）
    let target = sid;
    if (target == null) target = await newSession();
    setInput('');
    setSending(true);
    setStreaming(true);
    push('user', text);
    const aid = push('assistant', '');
    const finalMeta: ChatMsgMeta = { agent: '', citations: [], transfer: false };
    let got = false;
    try {
      await streamEvents(`/chat/sessions/${target}/messages/stream`, { message: text }, {
        onEvent: (event, data) => {
          if (event === 'route') {
            finalMeta.agent = String(data.agent || '');
          } else if (event === 'delta') {
            got = true;
            setMsgs((prev) => prev.map((x) => (x.id === aid ? { ...x, content: x.content + String(data.text ?? '') } : x)));
          } else if (event === 'citations') {
            finalMeta.citations = (data.citations as ChatMsgMeta['citations']) || [];
          } else if (event === 'done') {
            finalMeta.transfer = Boolean(data.transfer);
            finalMeta.agent = finalMeta.agent || String(data.agent || '');
            finalMeta.provider = String(data.provider || '');
            finalMeta.degraded = Boolean(data.degraded);
            const content = String(data.content ?? '');
            got = true;
            setMsgs((prev) => prev.map((x) => (x.id === aid ? { ...x, content: x.content || content, meta: finalMeta } : x)));
          } else if (event === 'error') {
            setMsgs((prev) => prev.map((x) => (x.id === aid ? { ...x, content: x.content || `生成中断：${String(data.detail ?? '')}` } : x)));
          }
        },
      });
      if (!got) {
        // 无流内容时从服务端拉取最终内容兜底
        const rows = await api.chatMessages(target);
        const last = rows[rows.length - 1];
        if (last) setMsgs((prev) => prev.map((x) => (x.id === aid ? { ...x, content: last.content, meta: last.meta as ChatMsgMeta } : x)));
      }
    } catch (e) {
      message.error((e as Error).message || '对话失败');
      setMsgs((prev) => prev.map((x) => (x.id === aid ? { ...x, content: '（发送失败：' + (e as Error).message + '）' } : x)));
    } finally {
      setSending(false);
      setStreaming(false);
    }
  };

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [msgs, streaming]);

  return (
    <div className="page-container">
      <Card style={{ height: 'calc(100vh - 130px)' }} styles={{ body: { height: '100%', padding: 0 } }}>
        <Row style={{ height: '100%' }}>
          <Col xs={24} md={6} style={{ borderRight: '1px solid #f0f0f0', height: '100%', overflow: 'auto', padding: 12 }}>
            <Space direction="vertical" style={{ width: '100%' }}>
              <Button type="primary" icon={<PlusOutlined />} block onClick={newSession}>新对话</Button>
              <List size="small" dataSource={sessions} style={{ cursor: 'pointer' }}
                renderItem={(s) => (
                  <List.Item onClick={() => openSession(s.session_id)}
                    style={{ background: sid === s.session_id ? '#e6f4ff' : undefined, borderRadius: 6, paddingInline: 8 }}>
                    <Typography.Text ellipsis>{s.title}</Typography.Text>
                  </List.Item>
                )} />
            </Space>
          </Col>
          <Col xs={24} md={18} style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
            <div style={{ flex: 1, overflow: 'auto', padding: 16 }}>
              {!sid && <Empty description="选择左侧会话或点击“新对话”开始提问" style={{ marginTop: 120 }} />}
              {msgs.map((m) => (
                <div key={m.id} style={{ display: 'flex', marginBottom: 14, justifyContent: m.role === 'user' ? 'flex-end' : 'flex-start' }}>
                  <div style={{ maxWidth: '78%' }}>
                    {m.role === 'user' ? (
                      <div style={{ background: '#1677ff', color: '#fff', padding: '10px 14px', borderRadius: 10, whiteSpace: 'pre-wrap' }}>{m.content}</div>
                    ) : (
                      <Card size="small" style={{ background: '#fafafa', borderRadius: 10 }} styles={{ body: { padding: 10 } }}>
                        <Space style={{ marginBottom: 4 }} wrap>
                          <RobotOutlined style={{ color: '#1677ff' }} />
                          {m.meta?.agent ? <Tag color={AGENT_COLOR[m.meta.agent] ?? 'default'}>{AGENT_CN[m.meta.agent] ?? m.meta.agent}</Tag> : null}
                          {m.meta?.provider
                            ? (m.meta.provider !== 'demo'
                              ? <Tag color="green">{m.meta.provider === 'deepseek' ? 'DeepSeek 生成' : `${m.meta.provider} 生成`}</Tag>
                              : <Tag color="orange">离线兜底（未用到真实大模型）</Tag>)
                            : null}
                          {m.meta?.transfer ? <Tag color="red">已转人工</Tag> : null}
                          {m.meta && m.meta.agent && m.meta.agent !== 'human' && <Typography.Text type="secondary" style={{ fontSize: 12 }}>AI 生成初稿，需人工确认</Typography.Text>}
                        </Space>
                        <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', marginBottom: 0 }}>{m.content || (streaming ? '正在思考…' : '')}</Typography.Paragraph>
                        {m.meta?.citations?.length ? (
                          <div style={{ marginTop: 6 }}>
                            {m.meta.citations.map((c, i) => <Tag key={i} color="blue">引用：{c.title}</Tag>)}
                          </div>
                        ) : null}
                      </Card>
                    )}
                  </div>
                </div>
              ))}
              <div ref={endRef} />
            </div>
            <div style={{ padding: 12, borderTop: '1px solid #f0f0f0' }}>
              <Space wrap style={{ marginBottom: 8 }}>
                {DEMO_QUICK.map((q) => <Tag key={q} style={{ cursor: 'pointer' }} onClick={() => { setInput(q); }}>{q.slice(0, 22)}…</Tag>)}
              </Space>
              <Space.Compact style={{ width: '100%' }}>
                <Input.TextArea autoSize={{ minRows: 1, maxRows: 3 }} value={input}
                  placeholder="用自然语言提问，如：帮我生成矿山施工方案 / 给 T02 诊断并生成维修工单"
                  onChange={(e) => setInput(e.target.value)} onPressEnter={(e) => { if (!e.shiftKey) { e.preventDefault(); send(); } }} />
                <Button type="primary" icon={<SendOutlined />} loading={sending} onClick={send}>发送</Button>
              </Space.Compact>
            </div>
          </Col>
        </Row>
      </Card>
    </div>
  );
}
