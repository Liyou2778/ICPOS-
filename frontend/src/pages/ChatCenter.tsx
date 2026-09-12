import { useCallback, useEffect, useRef, useState } from 'react';
import { Button, Card, Dropdown, Empty, Form, Input, InputNumber, List, Modal, Select, Space, Tag, Typography, message } from 'antd';
import { PlusOutlined, SendOutlined, RobotOutlined, InboxOutlined, MoreOutlined, EditOutlined, DeleteOutlined, UndoOutlined } from '@ant-design/icons';
import { api } from '../api';
import type { ChatMsgMeta, ChatSessionsGrouped, SlotField } from '../api/types';
import { streamEvents } from '../utils/sse';

const AGENT_CN: Record<string, string> = {
  solution: '方案生成智能体', requirement: '需求分析智能体', dispatch: '施工调度智能体',
  maintenance: '设备运维智能体', status: '运营状态查询', kb_qa: '知识库问答(RAG)',
  human: '人工客服', solution_followup: '方案追问（多轮）',
};
const AGENT_COLOR: Record<string, string> = { solution: 'blue', requirement: 'purple', dispatch: 'cyan', maintenance: 'volcano', human: 'red' };

interface Msg { id: number; role: string; content: string; meta?: ChatMsgMeta }

const DEMO_QUICK = ['帮我生成矿山施工方案', '矿卡 T02 故障了，重新调度', '矿卡液压油温高漏油，帮我诊断并生成工单', '露天矿爆破单耗一般取多少？', '帮我转人工客服'];

export default function ChatCenter() {
  const [sessions, setSessions] = useState<ChatSessionsGrouped>({ active: [], archived: [], counts: { active: 0, archived: 0 } });
  const [sid, setSid] = useState<number | null>(null);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [slotForm, setSlotForm] = useState<SlotField[]>([]);
  const [form] = Form.useForm();
  const [renameOpen, setRenameOpen] = useState(false);
  const [renameTitle, setRenameTitle] = useState('');
  const endRef = useRef<HTMLDivElement>(null);
  const msgSeq = useRef(1);

  const currentSession = [...sessions.active, ...sessions.archived].find((s) => s.session_id === sid);

  const openSession = useCallback(async (id: number) => {
    setSid(id);
    setSlotForm([]);
    try {
      const rows = await api.chatMessages(id);
      setMsgs(rows.map((m) => ({ ...m, meta: m.meta as ChatMsgMeta | undefined })));
      const lastNeed = [...rows].reverse().find((m) => (m.meta as ChatMsgMeta | undefined)?.missing_slots);
      const meta = lastNeed?.meta as ChatMsgMeta | undefined;
      if (meta?.slot_form?.length) setSlotForm(meta.slot_form as SlotField[]);
    } catch {
      setMsgs([]);
    }
  }, []);

  const refreshSessions = useCallback(async (autoOpen = false) => {
    try {
      const grouped = await api.chatSessions();
      setSessions(grouped);
      if (autoOpen && grouped.active.length) await openSession(grouped.active[0].session_id);
    } catch { /* ignore */ }
  }, [openSession]);

  useEffect(() => { refreshSessions(true); }, [refreshSessions]);

  const newSession = async (): Promise<number> => {
    const s = await api.createChat('新对话');
    await refreshSessions();
    setSid(s.session_id);
    setMsgs([]);
    setSlotForm([]);
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
    let target = sid;
    if (target == null) target = await newSession();
    setInput('');
    setSending(true);
    setStreaming(true);
    setSlotForm([]);
    push('user', text);
    const aid = push('assistant', '');
    const finalMeta: ChatMsgMeta = { agent: '', citations: [], transfer: false };
    let got = false;
    try {
      await streamEvents(`/chat/sessions/${target}/messages/stream`, { message: text }, {
        onEvent: (event, data) => {
          if (event === 'route') {
            finalMeta.agent = String(data.agent || '');
            if (Array.isArray(data.slot_form) && data.slot_form.length) setSlotForm(data.slot_form as SlotField[]);
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
            if (Array.isArray(data.slot_form) && data.slot_form.length) {
              finalMeta.missing_slots = data.missing_slots as string[];
              finalMeta.slot_form = data.slot_form as SlotField[];
              setSlotForm(data.slot_form as SlotField[]);
            }
            const content = String(data.content ?? '');
            got = true;
            setMsgs((prev) => prev.map((x) => (x.id === aid ? { ...x, content: x.content || content, meta: finalMeta } : x)));
          } else if (event === 'error') {
            setMsgs((prev) => prev.map((x) => (x.id === aid ? { ...x, content: x.content || `生成中断：${String(data.detail ?? '')}` } : x)));
          }
        },
      });
      if (!got) {
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
      refreshSessions();
    }
  };

  const submitSlotForm = async (values: Record<string, string | number>) => {
    if (sid == null) return;
    setSending(true);
    try {
      const res = await api.submitSlots(sid, values as Record<string, string>);
      push('user', `（补录）${Object.entries(values).map(([k, v]) => `${k}=${v}`).join('；')}`);
      push('assistant', res.content, {
        agent: res.agent, citations: res.citations || [], transfer: false,
        provider: res.provider, degraded: res.degraded,
        missing_slots: res.missing_slots, slot_form: res.slot_form,
      } as ChatMsgMeta);
      setSlotForm(res.need_more ? res.slot_form || [] : []);
      form.resetFields();
      if (!res.need_more) message.success('参数已齐备，已自动继续生成方案');
      refreshSessions();
    } catch (e) {
      message.error((e as Error).message || '补录失败');
    } finally {
      setSending(false);
    }
  };

  const act = async (id: number, action: 'archive' | 'restore' | 'rename' | 'tags') => {
    try {
      if (action === 'archive') await api.patchChat(id, { status: 'archived' });
      else if (action === 'restore') await api.patchChat(id, { status: 'active' });
      else if (action === 'rename') {
        setRenameTitle(currentSession?.title || '');
        setRenameOpen(true);
        return;
      } else if (action === 'tags') {
        const tags = window.prompt('输入标签（逗号分隔，例如：矿山,投标）', currentSession?.tags || '');
        if (tags === null) return;
        await api.patchChat(id, { tags });
      }
      message.success('已更新');
      refreshSessions();
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  const doRename = async () => {
    if (sid == null) return;
    await api.patchChat(sid, { title: renameTitle });
    setRenameOpen(false);
    message.success('会话已重命名');
    refreshSessions();
  };

  const renderSessionItem = (s: ChatSessionsGrouped['active'][number]) => (
    <List.Item
      key={s.session_id}
      onClick={() => openSession(s.session_id)}
      style={{ background: sid === s.session_id ? '#e6f4ff' : undefined, borderRadius: 6, paddingInline: 8, cursor: 'pointer' }}
      actions={[
        <Dropdown
          key="menu"
          trigger={['click']}
          menu={{
            items: s.status === 'archived'
              ? [{ key: 'restore', icon: <UndoOutlined />, label: '恢复会话' },
                 { key: 'rename', icon: <EditOutlined />, label: '重命名' }]
              : [{ key: 'rename', icon: <EditOutlined />, label: '重命名' },
                 { key: 'tags', icon: <EditOutlined />, label: '设置标签' },
                 { key: 'archive', icon: <DeleteOutlined />, label: '归档会话' }],
            onClick: ({ key, domEvent }) => { domEvent.stopPropagation(); act(s.session_id, key as 'archive'); },
          }}
        >
          <MoreOutlined onClick={(e) => e.stopPropagation()} />
        </Dropdown>,
      ]}
    >
      <Space direction="vertical" size={0} style={{ width: '100%' }}>
        <Typography.Text ellipsis>{s.title}</Typography.Text>
        <Space size={4} wrap>
          {s.pending && <Tag color="orange">待补参数</Tag>}
          {s.tags ? <Tag>{s.tags}</Tag> : null}
          {s.slot_summary?.slice(0, 2).map((x) => <Tag key={x.key} color="blue">{x.label}:{x.value}</Tag>)}
        </Space>
      </Space>
    </List.Item>
  );

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [msgs, streaming, slotForm]);

  return (
    <div className="page-container">
      <Card style={{ height: 'calc(100vh - 130px)' }} styles={{ body: { height: '100%', padding: 0 } }}>
        <div style={{ display: 'flex', height: '100%' }}>
          <div style={{ width: 280, borderRight: '1px solid #f0f0f0', height: '100%', overflow: 'auto', padding: 12 }}>
            <Space direction="vertical" style={{ width: '100%' }}>
              <Button type="primary" icon={<PlusOutlined />} block onClick={newSession}>新对话</Button>
              <Typography.Text type="secondary">进行中（{sessions.counts.active}）</Typography.Text>
              <List size="small" dataSource={sessions.active} renderItem={renderSessionItem} />
              <Typography.Text type="secondary"><InboxOutlined /> 已归档（{sessions.counts.archived}）</Typography.Text>
              <List size="small" dataSource={sessions.archived} renderItem={renderSessionItem} />
            </Space>
          </div>
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', height: '100%' }}>
            {currentSession?.slot_summary?.length ? (
              <div style={{ padding: '8px 16px', borderBottom: '1px solid #f0f0f0', background: '#fafcff' }}>
                <Space size={4} wrap>
                  <Typography.Text type="secondary">需求槽位：</Typography.Text>
                  {currentSession.slot_summary.map((x) => <Tag key={x.key} color="green">{x.label}：{x.value}</Tag>)}
                  {currentSession.pending && <Tag color="orange">待补参数后将自动继续</Tag>}
                </Space>
              </div>
            ) : null}
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
              {slotForm.length > 0 && (
                <Card size="small" title="需求参数补录（补全后自动继续生成，无需重述需求）" style={{ maxWidth: 620, borderColor: '#faad14' }}>
                  <Form form={form} layout="vertical" onFinish={(v) => submitSlotForm(v)}>
                    {slotForm.map((f) => (
                      <Form.Item key={f.key} name={f.key} label={`${f.label}${f.unit ? `（${f.unit}）` : ''}`} rules={[{ required: f.required }]}>
                        {f.kind === 'select' ? (
                          <Select placeholder="请选择" options={f.options} />
                        ) : f.kind === 'number' ? (
                          <InputNumber style={{ width: '100%' }} placeholder={f.question} />
                        ) : (
                          <Input placeholder={f.question} />
                        )}
                      </Form.Item>
                    ))}
                    <Space>
                      <Button type="primary" htmlType="submit" loading={sending}>提交并继续生成</Button>
                      <Button onClick={() => { setSlotForm([]); form.resetFields(); }}>暂不补录</Button>
                    </Space>
                    <Typography.Paragraph type="secondary" style={{ marginTop: 8, marginBottom: 0, fontSize: 12 }}>
                      提示：数值型可带单位（如 1.5亿、200万吨、18个月），系统会按语义自动换算。
                    </Typography.Paragraph>
                  </Form>
                </Card>
              )}
              <div ref={endRef} />
            </div>
            <div style={{ padding: 12, borderTop: '1px solid #f0f0f0' }}>
              <Space wrap style={{ marginBottom: 8 }}>
                {DEMO_QUICK.map((q) => <Tag key={q} style={{ cursor: 'pointer' }} onClick={() => setInput(q)}>{q}</Tag>)}
              </Space>
              <Space.Compact style={{ width: '100%' }}>
                <Input.TextArea autoSize={{ minRows: 1, maxRows: 3 }} value={input}
                  placeholder="用自然语言提问；参数不全时我会先追问，补录后自动继续"
                  onChange={(e) => setInput(e.target.value)} onPressEnter={(e) => { if (!e.shiftKey) { e.preventDefault(); send(); } }} />
                <Button type="primary" icon={<SendOutlined />} loading={sending} onClick={send}>发送</Button>
              </Space.Compact>
            </div>
          </div>
        </div>
      </Card>
      <Modal open={renameOpen} title="重命名会话" onOk={doRename} onCancel={() => setRenameOpen(false)}>
        <Input value={renameTitle} onChange={(e) => setRenameTitle(e.target.value)} placeholder="请输入会话标题" />
      </Modal>
    </div>
  );
}
