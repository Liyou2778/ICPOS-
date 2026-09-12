import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert, Button, Card, Col, Collapse, Divider, Form, Input, InputNumber, Radio, Row, Segmented,
  Select, Space, Statistic, Steps, Table, Tag, Typography, message,
} from 'antd';
import { FilePdfOutlined, FileWordOutlined, ReloadOutlined, RobotOutlined, SendOutlined, ThunderboltOutlined } from '@ant-design/icons';
import { api } from '../api';
import type { SolutionBundle, SolutionPlan, TcoRow } from '../api/types';
import EChart from '../components/EChart';
import type { EChartsOption } from 'echarts';
import { getToken } from '../api/client';
import { streamEvents } from '../utils/sse';

const PRESETS = [
  { key: 'mining', label: '矿山施工（示例）', text: '我是矿山生产主管，年产200万吨矿石，工期3年，预算1.5亿元，岩石较硬，帮我生成矿山开采施工方案并推荐设备。', doc: 'construction' },
  { key: 'bid', label: '投标方案（示例）', text: '矿山剥离项目招标：年产300万吨剥离，工期2年，预算2亿元，请生成完整投标方案并检查招标响应度。', doc: 'bid' },
  { key: 'selection', label: '设备选型采购（示例）', text: '土方工程需要采购挖掘机与运输设备，年挖填方150万方，工期2年，预算6000万元，请生成设备选型采购方案。', doc: 'selection' },
];
const DOC_CN: Record<string, string> = { construction: '施工组织设计', bid: '投标方案', selection: '设备选型方案' };

async function downloadFile(name: string) {
  const resp = await fetch(`/api/solutions/files/${encodeURIComponent(name)}`, {
    headers: { Authorization: `Bearer ${getToken()}` },
  });
  if (!resp.ok) throw new Error('文件下载失败');
  const blob = await resp.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

interface ChatLine { role: 'user' | 'assistant'; content: string }

export default function Workspace() {
  const [step, setStep] = useState(0);
  const [text, setText] = useState(PRESETS[0].text);
  const [docType, setDocType] = useState('construction');
  const [running, setRunning] = useState(false);
  const [needMore, setNeedMore] = useState<string[]>([]);
  const [latency, setLatency] = useState<number | null>(null);
  const [slotForm] = Form.useForm();
  const [plan, setPlan] = useState<SolutionPlan | null>(null);
  const [counts, setCounts] = useState<Record<number, Record<string, number>>>({});
  const [best, setBest] = useState(0);
  const [exporting, setExporting] = useState<string | null>(null);
  const [sid, setSid] = useState<number | null>(null);
  const [lines, setLines] = useState<ChatLine[]>([]);
  const [ask, setAsk] = useState('');
  const [asking, setAsking] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  const ensureSession = useCallback(async (): Promise<number> => {
    if (sid != null) return sid;
    const s = await api.createChat('方案工作台助手');
    setSid(s.session_id);
    return s.session_id;
  }, [sid]);

  useEffect(() => { ensureSession().catch(() => undefined); }, [ensureSession]);
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [lines]);

  const generate = async (overrideText?: string) => {
    const body = overrideText ?? text;
    setRunning(true);
    setNeedMore([]);
    setLatency(null);
    try {
      const t0 = performance.now();
      const res = await api.plan(body, docType);
      setLatency((performance.now() - t0) / 1000);
      if (res.status === 'need_more') {
        setNeedMore(res.facts || []);
        message.warning('关键参数缺失：已阻塞生成并列出追问清单');
      } else {
        setPlan(res.payload);
        setBest(res.payload.best_index ?? 0);
        setCounts({});
        setStep(1);
        message.success('已生成 ≥3 套方案，请在第 2 步比选与调参');
      }
    } catch (e) {
      message.error((e as Error).message || '生成失败');
    } finally {
      setRunning(false);
    }
  };

  const submitSlots = async (values: Record<string, string | number>) => {
    const sceneCn = ({ mining: '露天矿山开采', earthwork: '土方工程', agriculture: '农业作业' } as Record<string, string>)[String(values.scene_type)] ?? '';
    const parts: string[] = [];
    if (sceneCn) parts.push(`场景：${sceneCn}`);
    if (values.annual_t) parts.push(`年作业量 ${values.annual_t} 万吨`);
    if (values.duration_years) parts.push(`工期 ${values.duration_years} 年`);
    if (values.budget_cny) parts.push(`预算 ${values.budget_cny} 亿元`);
    await generate(`${parts.join('，')}，请生成${DOC_CN[docType]}。需求原文：${text}`);
  };

  const exportDoc = async (fmt: 'docx' | 'pdf') => {
    if (!plan) return;
    setExporting(fmt);
    try {
      const res = await api.exportDoc(plan, plan.doc_type, plan.title);
      const name = fmt === 'docx' ? res.docx : res.pdf;
      if (name) {
        await downloadFile(name);
        message.success(`已导出 ${fmt.toUpperCase()}`);
      } else {
        message.info('PDF 依赖 LibreOffice，未安装时仅提供 Word 导出');
      }
    } catch (e) {
      message.error((e as Error).message || '导出失败');
    } finally {
      setExporting(null);
    }
  };

  const askAssistant = async () => {
    const q = ask.trim();
    if (!q || asking) return;
    setAsk('');
    setLines((prev) => [...prev, { role: 'user', content: q }]);
    setAsking(true);
    const target = await ensureSession();
    let acc = '';
    setLines((prev) => [...prev, { role: 'assistant', content: '' }]);
    try {
      await streamEvents(`/chat/sessions/${target}/messages/stream`, { message: q }, {
        onEvent: (event, data) => {
          if (event === 'delta') {
            acc += String(data.text ?? '');
            setLines((prev) => prev.map((l, i) => (i === prev.length - 1 ? { role: 'assistant', content: acc } : l)));
          } else if (event === 'done' && !acc) {
            setLines((prev) => prev.map((l, i) => (i === prev.length - 1 ? { role: 'assistant', content: String(data.content ?? '') } : l)));
          }
        },
      });
    } catch (e) {
      setLines((prev) => prev.map((l, i) => (i === prev.length - 1 ? { role: 'assistant', content: `（失败：${(e as Error).message}）` } : l)));
    } finally {
      setAsking(false);
    }
  };

  const effectiveBundles = useMemo<SolutionBundle[]>(() => {
    if (!plan) return [];
    return plan.bundles.map((b, bi) => {
      const override = counts[bi] || {};
      const fleet = b.fleet.map((f) => {
        const c = override[f.model_code] ?? f.count;
        return { ...f, count: c, total_price_cny: f.unit_price_cny * c };
      });
      const tco: TcoRow[] = b.tco.map((row) => {
        const c = override[row.model_code] ?? row.count;
        const old = row.count || 1;
        const purchase = (row.purchase_cny / old) * c;
        const energy = (row.energy_3y_cny / old) * c;
        const maintenance = (row.maintenance_3y_cny / old) * c;
        const residual = (row.residual_cny / old) * c;
        const total = purchase + energy + maintenance - residual;
        const movedEst = row.per_ton_cost_cny > 0 ? row.total_3y_cny / row.per_ton_cost_cny : 0;
        return {
          ...row, count: c, purchase_cny: purchase, energy_3y_cny: energy,
          maintenance_3y_cny: maintenance, residual_cny: residual, total_3y_cny: total,
          per_ton_cost_cny: movedEst > 0 ? Number((total / movedEst).toFixed(2)) : row.per_ton_cost_cny,
        };
      });
      return {
        ...b, fleet, tco,
        tco_3y_total_cny: tco.reduce((s, r) => s + r.total_3y_cny, 0),
        fleet_total_cny: fleet.reduce((s, f) => s + f.total_price_cny, 0),
      } as SolutionBundle;
    });
  }, [plan, counts]);

  const compareOption = useMemo<EChartsOption>(() => (effectiveBundles.length ? {
    tooltip: { trigger: 'axis' },
    legend: { data: ['三年TCO(万元)', '日产能(百吨)'] },
    xAxis: { type: 'category', data: effectiveBundles.map((b) => b.name) },
    yAxis: [{ type: 'value' }, { type: 'value' }],
    series: [
      { name: '三年TCO(万元)', type: 'bar', data: effectiveBundles.map((b) => Math.round(b.tco_3y_total_cny / 1e4)), itemStyle: { color: '#1677ff' } },
      { name: '日产能(百吨)', type: 'line', yAxisIndex: 1, data: effectiveBundles.map((b) => Math.round(b.daily_capacity_t / 100)), itemStyle: { color: '#52c41a' } },
    ],
  } : {}), [effectiveBundles]);

  const summary = useMemo(() => {
    const b = effectiveBundles[best];
    if (!b) return null;
    return {
      purchase: b.tco.reduce((s, r) => s + r.purchase_cny, 0),
      total: b.tco_3y_total_cny,
      perTon: b.tco[0]?.per_ton_cost_cny ?? 0,
    };
  }, [effectiveBundles, best]);

  return (
    <div className="page-container">
      <Card styles={{ body: { paddingTop: 12 } }}>
        <Steps current={step} size="small"
          items={[{ title: '① 需求输入' }, { title: '② 方案比选与调参' }, { title: '③ 方案定稿与导出' }]} />
      </Card>
      <Row gutter={[12, 12]} style={{ marginTop: 12 }}>
        <Col xs={24} xl={16}>
          {step === 0 && (
            <Card title="① 需求输入（自然语言 + 结构化补录双通道）" extra={<Tag color="orange">参数不全将阻塞生成并追问</Tag>}>
              <Segmented block options={PRESETS.map((p) => ({ label: p.label, value: p.key }))}
                onChange={(k) => { const p = PRESETS.find((x) => x.key === k)!; setText(p.text); setDocType(p.doc); }} />
              <Radio.Group value={docType} onChange={(e) => setDocType(e.target.value)} style={{ margin: '10px 0' }}
                options={[{ label: '施工组织设计', value: 'construction' }, { label: '投标方案', value: 'bid' }, { label: '设备选型', value: 'selection' }]} />
              <Input.TextArea rows={6} value={text} onChange={(e) => setText(e.target.value)} />
              <Space style={{ marginTop: 8 }}>
                <Button type="primary" icon={<ThunderboltOutlined />} loading={running} onClick={() => generate()}>生成方案</Button>
                <Button icon={<ReloadOutlined />} onClick={() => { setPlan(null); setNeedMore([]); }}>重置</Button>
                {latency !== null && <Tag color="green">耗时 {latency.toFixed(2)}s（目标 ≤30s）</Tag>}
              </Space>
              {needMore.length > 0 && (
                <Card size="small" type="inner" title="关键参数缺失 · 请补录（提交后自动继续生成）" style={{ marginTop: 12, borderColor: '#faad14' }}>
                  {needMore.map((n, i) => <Alert key={i} type="warning" showIcon message={n} style={{ marginBottom: 6 }} />)}
                  <Form form={slotForm} layout="inline" onFinish={(v) => submitSlots(v as Record<string, string | number>)}>
                    <Form.Item name="scene_type" label="作业场景" rules={[{ required: true }]}>
                      <Select
                        style={{ width: 160 }}
                        options={[
                          { value: 'mining', label: '露天矿山开采' },
                          { value: 'earthwork', label: '土方工程' },
                          { value: 'agriculture', label: '农业作业' },
                        ]}
                      />
                    </Form.Item>
                    <Form.Item name="annual_t" label="年作业量(万吨)" rules={[{ required: true }]}><InputNumber min={1} /></Form.Item>
                    <Form.Item name="duration_years" label="工期(年)" rules={[{ required: true }]}><InputNumber min={0.5} step={0.5} /></Form.Item>
                    <Form.Item name="budget_cny" label="预算(亿元)" rules={[{ required: true }]}><InputNumber min={0.1} step={0.1} /></Form.Item>
                    <Form.Item><Button type="primary" htmlType="submit" loading={running}>提交并继续生成</Button></Form.Item>
                  </Form>
                </Card>
              )}
              <Divider style={{ margin: '12px 0' }} />
              <Typography.Paragraph type="secondary" style={{ marginBottom: 0, fontSize: 12 }}>
                流程：需求输入 → 需求分析智能体结构化解析（缺参追问）→ 生成 ≥3 套选型（型号/参数/配置/三年 TCO）
                → 第 2 步比选与调参（实时重算）→ 第 3 步定稿导出 Word/PDF。
              </Typography.Paragraph>
            </Card>
          )}

          {step === 1 && plan && (
            <Space direction="vertical" style={{ width: '100%' }} size={12}>
              <Card title={`② 方案比选与调参 · ${plan.title}`}
                extra={<Space>
                  <Button size="small" onClick={() => setStep(0)}>返回需求</Button>
                  <Button type="primary" size="small" onClick={() => setStep(2)}>进入定稿</Button>
                </Space>}>
                <EChart option={compareOption} height={220} />
                <Row gutter={[12, 12]}>
                  {effectiveBundles.map((b, i) => (
                    <Col xs={24} md={8} key={b.name}>
                      <Card size="small" hoverable onClick={() => setBest(i)}
                        style={{ borderColor: best === i ? '#1677ff' : undefined, borderWidth: best === i ? 2 : 1 }}>
                        <Space direction="vertical" size={4} style={{ width: '100%' }}>
                          <Space>
                            <Tag color={best === i ? 'blue' : 'default'}>{b.name}</Tag>
                            {plan.best_index === i && <Tag color="gold">系统推荐</Tag>}
                          </Space>
                          <Typography.Text type="secondary" style={{ fontSize: 12 }}>{b.summary}</Typography.Text>
                          <Statistic title="三年 TCO（万元）" value={Math.round(b.tco_3y_total_cny / 1e4)} />
                          <Statistic title="日产能（吨）" value={Math.round(b.daily_capacity_t)} />
                          <Divider style={{ margin: '6px 0' }} />
                          <Typography.Text style={{ fontSize: 12 }}>设备数量调整（实时重算 TCO）</Typography.Text>
                          {b.fleet.map((f) => (
                            <Space key={f.model_code} size={6}>
                              <Tag>{f.model_code}</Tag>
                              <InputNumber size="small" min={1} max={99} value={f.count}
                                onChange={(v) => setCounts((prev) => ({ ...prev, [i]: { ...(prev[i] || {}), [f.model_code]: Number(v) || 1 } }))} />
                              <Typography.Text type="secondary" style={{ fontSize: 12 }}>{f.model_name.slice(0, 14)}</Typography.Text>
                            </Space>
                          ))}
                        </Space>
                      </Card>
                    </Col>
                  ))}
                </Row>
              </Card>
              <Card size="small" title="TCO 明细（购置 / 能耗 / 维保 / 残值）">
                <Table rowKey="model_code" size="small" pagination={false} dataSource={effectiveBundles[best]?.tco || []}
                  columns={[
                    { title: '型号', dataIndex: 'model_name' }, { title: '数量', dataIndex: 'count' },
                    { title: '购置(万元)', dataIndex: 'purchase_cny', render: (v: number) => (v / 1e4).toFixed(1) },
                    { title: '能耗3年(万元)', dataIndex: 'energy_3y_cny', render: (v: number) => (v / 1e4).toFixed(1) },
                    { title: '维保3年(万元)', dataIndex: 'maintenance_3y_cny', render: (v: number) => (v / 1e4).toFixed(1) },
                    { title: '残值(万元)', dataIndex: 'residual_cny', render: (v: number) => (v / 1e4).toFixed(1) },
                    { title: '合计(万元)', dataIndex: 'total_3y_cny', render: (v: number) => <b>{(v / 1e4).toFixed(1)}</b> },
                  ]} />
              </Card>
            </Space>
          )}

          {step === 2 && plan && summary && (
            <Card title={`③ 方案定稿 · ${plan.title}`}
              extra={<Space>
                <Button size="small" onClick={() => setStep(1)}>返回比选</Button>
                <Button size="small" icon={<FileWordOutlined />} loading={exporting === 'docx'} onClick={() => exportDoc('docx')}>导出 Word</Button>
                <Button size="small" icon={<FilePdfOutlined />} loading={exporting === 'pdf'} onClick={() => exportDoc('pdf')}>导出 PDF</Button>
              </Space>}>
              <Row gutter={[12, 12]}>
                <Col xs={12} md={6}><Statistic title="选定方案" value={effectiveBundles[best]?.name} /></Col>
                <Col xs={12} md={6}><Statistic title="购置成本(万元)" value={(summary.purchase / 1e4).toFixed(1)} /></Col>
                <Col xs={12} md={6}><Statistic title="三年 TCO(万元)" value={(summary.total / 1e4).toFixed(1)} /></Col>
                <Col xs={12} md={6}><Statistic title="吨成本(元/吨)" value={summary.perTon} /></Col>
              </Row>
              <Divider style={{ margin: '12px 0' }} />
              <Collapse size="small" defaultActiveKey={['0']}
                items={plan.chapters.map((ch, i) => ({
                  key: String(i),
                  label: `${i + 1}. ${ch.chapter}`,
                  children: ch.paragraphs.map((p, j) => <Typography.Paragraph key={j} style={{ marginBottom: 4 }}>· {p}</Typography.Paragraph>),
                }))} />
              {plan.bid_response_check?.length ? (
                <>
                  <Divider style={{ margin: '12px 0' }}>招标响应度检查</Divider>
                  <Table rowKey="clause" size="small" pagination={false} dataSource={plan.bid_response_check}
                    columns={[{ title: '招标条款/章节', dataIndex: 'clause' },
                              { title: '响应情况', dataIndex: 'status', render: (v: string) => <Tag color="green">{v}</Tag> },
                              { title: '说明', dataIndex: 'note' }]} />
                </>
              ) : null}
              {plan.citations?.length ? (
                <Typography.Paragraph type="secondary" style={{ marginTop: 8, fontSize: 12 }}>
                  引用来源：{plan.citations.map((c, i) => <Tag key={i}>{c.title}</Tag>)}
                </Typography.Paragraph>
              ) : null}
              <Alert type="info" showIcon style={{ marginTop: 8 }}
                message={`AI 生成初稿，需人工确认 · 数字均来自设备参数库/知识库直读${plan.document_id ? ` · 已存档（ID ${plan.document_id}）` : ''}`} />
            </Card>
          )}

          {!plan && step > 0 && <Card><Typography.Text type="secondary">尚未生成方案，请返回第 1 步输入需求。</Typography.Text></Card>}
        </Col>

        <Col xs={24} xl={8}>
          <Card size="small" title={<Space><RobotOutlined />AI 方案助手（可追问 / 改参数）</Space>}
            styles={{ body: { display: 'flex', flexDirection: 'column', height: 560 } }}>
            <div style={{ flex: 1, overflow: 'auto', paddingRight: 4 }}>
              {lines.length === 0 && (
                <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
                  试试：<br />· 第二套方案的三年 TCO 明细是多少？<br />· 招标响应度检查结果如何？<br />· 推荐方案的空载率改善有多少？
                </Typography.Paragraph>
              )}
              {lines.map((l, i) => (
                <div key={i} style={{ marginBottom: 8, textAlign: l.role === 'user' ? 'right' : 'left' }}>
                  <div style={{
                    display: 'inline-block', padding: '6px 10px', borderRadius: 8, maxWidth: '92%', textAlign: 'left',
                    background: l.role === 'user' ? '#1677ff' : '#f5f5f5', color: l.role === 'user' ? '#fff' : '#262626',
                    whiteSpace: 'pre-wrap', fontSize: 13,
                  }}>{l.content || (asking ? '思考中…' : '')}</div>
                </div>
              ))}
              <div ref={bottomRef} />
            </div>
            <Space.Compact style={{ width: '100%', marginTop: 8 }}>
              <Input value={ask} onChange={(e) => setAsk(e.target.value)} placeholder="向助手追问方案细节…" onPressEnter={askAssistant} />
              <Button type="primary" icon={<SendOutlined />} loading={asking} onClick={askAssistant}>发送</Button>
            </Space.Compact>
          </Card>
        </Col>
      </Row>
    </div>
  );
}
