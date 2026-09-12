import { useCallback, useEffect, useState } from 'react';
import { Alert, Button, Card, Col, Descriptions, Input, List, Progress, Row, Select, Space, Statistic, Table, Tabs, Tag, Typography, message } from 'antd';
import { ExperimentOutlined, FileAddOutlined, SearchOutlined } from '@ant-design/icons';
import { api } from '../api';
import type { CorpusDevice, CorpusFault, CorpusPrediction, DiagnosisItem, WarningItem, WorkOrder } from '../api/types';

const SEV = { H: { c: 'red', t: '高' }, M: { c: 'orange', t: '中' }, L: { c: 'blue', t: '低' } } as const;

export default function Maintenance() {
  const [warns, setWarns] = useState<WarningItem[]>([]);
  const [orders, setOrders] = useState<WorkOrder[]>([]);
  const [diagText, setDiagText] = useState('矿卡液压油温高、动作没劲，怀疑漏油');
  const [diag, setDiag] = useState<{ top3: DiagnosisItem[]; fix_plan: string; parts: { name: string }[]; est_hours: number; engineer: string } | null>(null);
  const [diagLoading, setDiagLoading] = useState(false);
  const [predCode, setPredCode] = useState('T04');
  const [pred, setPred] = useState<{ device_code: string; risky: boolean; top_code: string; top_conf: number; remaining_hours: number | null; anomaly_score: number; note?: string } | null>(null);
  const [predLoading, setPredLoading] = useState(false);
  const [deviceCodes, setDeviceCodes] = useState<string[]>([]);
  const [loadingList, setLoadingList] = useState(false);
  // ---- 语料模型（企业级训练产物） ----
  const [cDevices, setCDevices] = useState<CorpusDevice[]>([]);
  const [cFaults, setCFaults] = useState<CorpusFault[]>([]);
  const [cReport, setCReport] = useState<any>(null);
  const [cDevice, setCDevice] = useState('DEV-011');
  const [cAt, setCAt] = useState('');
  const [cPred, setCPred] = useState<CorpusPrediction | null>(null);
  const [cLoading, setCLoading] = useState(false);

  const load = useCallback(async () => {
    setLoadingList(true);
    try {
      const [w, o, d] = await Promise.all([api.warnings(), api.workorders(), api.devices()]);
      setWarns(w);
      setOrders(o);
      setDeviceCodes(d.map((x) => x.code));
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setLoadingList(false);
    }
    try {
      const [cd, cf, rep] = await Promise.all([api.corpusDevices(), api.corpusFaults(), api.corpusReport()]);
      setCDevices(cd.devices);
      setCFaults(cf.faults);
      setCReport(rep);
    } catch {
      /* 语料模型未训练时忽略 */
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const runCorpus = async (device?: string, at?: string) => {
    const dev = device ?? cDevice;
    const when = at ?? (cAt || undefined);
    setCLoading(true);
    try {
      const r = await api.predictCorpus(dev, when);
      setCPred(r);
      setCDevice(dev);
      if (when) setCAt(when);
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setCLoading(false);
    }
  };

  const runDiag = async () => {
    setDiagLoading(true);
    try {
      setDiag(await api.diagnose(diagText));
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setDiagLoading(false);
    }
  };

  const runPred = async () => {
    setPredLoading(true);
    try {
      const r = await api.predict(predCode.trim());
      setPred(r);
      if (r.risky) message.warning(`设备 ${r.device_code} 存在风险（${r.top_code}）`);
      else message.success(`设备 ${r.device_code} 运行正常`);
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setPredLoading(false);
    }
  };

  const toOrder = async (w: WarningItem) => {
    try {
      const wo = await api.createWorkorder(w.device_code, '', w.fault_code || '');
      message.success(`已根据预警一键生成工单 ${wo.code}`);
      load();
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  return (
    <div className="page-container">
      <Typography.Title level={4} style={{ marginTop: 0 }}>智能运维中心 · 预测性维护 / 故障诊断 / 工单闭环</Typography.Title>
      <Tabs defaultActiveKey="warnings" items={[
        {
          key: 'warnings', label: '故障预警（五要素）', children: (
            <Table<WarningItem> rowKey="id" loading={loadingList} dataSource={warns} pagination={{ pageSize: 8 }}
              columns={[
                { title: '设备', dataIndex: 'device_code' },
                { title: '故障码', dataIndex: 'fault_code', render: (v) => <Tag>{v || '—'}</Tag> },
                { title: '异常部件', dataIndex: 'predicted_part', ellipsis: true },
                { title: '严重等级', dataIndex: 'severity', render: (v) => <Tag color={SEV[v as keyof typeof SEV]?.c ?? 'blue'}>{SEV[v as keyof typeof SEV]?.t ?? v}</Tag> },
                { title: '可能原因', dataIndex: 'probable_cause', ellipsis: true },
                { title: '剩余可用(h)', dataIndex: 'remaining_hours', render: (v) => (v == null ? '未知' : v) },
                { title: '置信度', dataIndex: 'model_conf', render: (v) => `${(v * 100).toFixed(0)}%` },
                { title: '操作', render: (_, r) => <Button size="small" type="link" icon={<FileAddOutlined />} disabled={r.status !== 'open'} onClick={() => toOrder(r)}>一键生成工单</Button> },
              ]} />
          ),
        },
        {
          key: 'diagnose', label: '故障诊断（Top3 + 置信度）', children: (
            <Space direction="vertical" style={{ width: '100%' }}>
              <Space.Compact style={{ width: '100%' }}>
                <Input value={diagText} onChange={(e) => setDiagText(e.target.value)} placeholder="输入自然语言描述或故障代码（如 HYD-01）" />
                <Button type="primary" icon={<SearchOutlined />} loading={diagLoading} onClick={runDiag}>诊断</Button>
              </Space.Compact>
              {diag && (
                <Row gutter={[12, 12]}>
                  {diag.top3.map((d, i) => (
                    <Col xs={24} md={8} key={d.code}>
                      <Card size="small" title={`${i + 1}. ${d.code} ${d.name}`} extra={<Tag color={d.severity === 'H' ? 'red' : 'orange'}>{SEV[d.severity as keyof typeof SEV]?.t ?? d.severity}</Tag>}>
                        <Statistic title="置信度" value={d.confidence * 100} precision={0} suffix="%" />
                      </Card>
                    </Col>
                  ))}
                  <Col span={24}>
                    <Card size="small" title="维修方案 / 备件 / 工时 / 工程师">
                      <Descriptions column={{ xs: 1, md: 2 }} size="small"
                        items={[
                          { key: 'plan', label: '维修方案', children: diag.fix_plan },
                          { key: 'parts', label: '备件', children: diag.parts.map((p) => p.name).join('、') || '无' },
                          { key: 'hours', label: '预计工时', children: `${diag.est_hours} 小时` },
                          { key: 'eng', label: '推荐工程师', children: diag.engineer },
                        ]} />
                    </Card>
                  </Col>
                </Row>
              )}
              {diag?.top3?.[0]?.code === 'UNKNOWN' && <Alert type="warning" showIcon message="维保知识库未覆盖该描述：系统已拒答并转人工（防幻觉：陈述必有出处）" />}
            </Space>
          ),
        },
        {
          key: 'predict', label: '预测性维护', children: (
            <Space direction="vertical" style={{ width: '100%' }}>
              <Space.Compact style={{ width: '100%', maxWidth: 480 }}>
                <Input value={predCode} onChange={(e) => setPredCode(e.target.value)} placeholder="输入设备编码，如 T04" />
                <Button type="primary" icon={<ExperimentOutlined />} loading={predLoading} onClick={runPred}>预测</Button>
              </Space.Compact>
              <List size="small" dataSource={deviceCodes.slice(0, 7)} renderItem={(c) => <List.Item><a onClick={() => setPredCode(c)}>{c}</a></List.Item>} />
              {pred && (
                <Card size="small" title={`设备 ${pred.device_code} 预测结果`}>
                  <Alert type={pred.risky ? 'error' : 'success'} showIcon
                    message={pred.risky ? `风险预警：最可能故障 ${pred.top_code}（置信度 ${(pred.top_conf * 100).toFixed(0)}%）` : '当前运行正常'}
                    description={pred.note || undefined} />
                  <Descriptions column={{ xs: 1, md: 3 }} style={{ marginTop: 8 }}
                    items={[
                      { key: 'a', label: '异常分', children: pred.anomaly_score },
                      { key: 'r', label: '预计剩余可用', children: pred.remaining_hours == null ? '未知' : `${pred.remaining_hours} 小时` },
                      { key: 'm', label: '模型口径', children: '模拟数据训练（V1.1 真实试点）' },
                    ]} />
                </Card>
              )}
            </Space>
          ),
        },
        {
          key: 'corpus', label: '语料模型（企业级训练）', children: (
            <Space direction="vertical" style={{ width: '100%' }}>
              <Alert type="success" showIcon message="语料训练成果（13 台设备 · 112,320 条遥测 · 12 条故障真值）"
                description={cReport ? (
                  <span>
                    P1 实例时序留出：检出 <b>{cReport?.P1_temporal_holdout?.detected}/{cReport?.P1_temporal_holdout?.detectable_faults}</b>，
                    提前量 平均 <b>{cReport?.P1_temporal_holdout?.lead_hours_avg}h</b>（最小 {cReport?.P1_temporal_holdout?.lead_hours_min}h），
                    部件识别 Top1 <b>{((cReport?.P1_temporal_holdout?.component_top1_acc ?? 0) * 100).toFixed(0)}%</b>，
                    误报 <b>{((cReport?.P1_temporal_holdout?.healthy_false_alarm_rate ?? 0) * 100).toFixed(1)}%</b>；
                    P2 跨设备参考检出率 {cReport?.P2_cross_device?.detection_rate}
                  </span>
                ) : '报告加载中（若为空请先运行 scripts.train_models_corpus）'} />
              <Space wrap>
                <Select style={{ width: 260 }} value={cDevice} onChange={setCDevice}
                  options={cDevices.map((d) => ({ value: d.device_id, label: `${d.device_id} · ${d.model} · ${d.category}` }))} />
                <Input style={{ width: 220 }} value={cAt} onChange={(e) => setCAt(e.target.value)}
                  placeholder="推理时刻（可选，如 2026-09-08T10:00）" />
                <Button type="primary" icon={<ExperimentOutlined />} loading={cLoading} onClick={() => runCorpus()}>部件级风险推理</Button>
              </Space>
              <div>
                <Typography.Text type="secondary">一键载入真实故障时刻（onset 前 6h，验证模型是否命中对应部件）：</Typography.Text>
                <div style={{ marginTop: 6 }}>
                  {cFaults.map((f) => (
                    <Tag key={f.code + f.device_id} color="blue" style={{ cursor: 'pointer', marginBottom: 4 }}
                      onClick={() => {
                        const t = new Date(new Date(f.onset_ts).getTime() - 6 * 3600 * 1000);
                        const iso = t.toISOString().slice(0, 16);
                        runCorpus(f.device_id, iso);
                      }}>
                      {f.device_id} {f.code}（{f.component}，真值提前 {f.lead_hours}h）
                    </Tag>
                  ))}
                </div>
              </div>
              {cPred && (
                <Card size="small" title={`推理结果 · ${cPred.device_id}${cPred.at ? ` @ ${cPred.at}` : ''}`}
                  extra={<Tag color={cPred.risky ? 'red' : 'green'}>{cPred.risky ? '风险' : '健康'}</Tag>}>
                  <Alert type={cPred.risky ? 'warning' : 'success'} showIcon style={{ marginBottom: 8 }}
                    message={cPred.risky
                      ? `命中部件：${cPred.top_class_cn}（${cPred.top_class}），置信度 ${((cPred.top_conf ?? 0) * 100).toFixed(2)}%`
                      : '各部件检测器均未超阈值（健康）'}
                    description={cPred.note} />
                  {cPred.classes.map((c) => (
                    <div key={c.class} style={{ marginBottom: 4 }}>
                      <Space>
                        <Tag color={c.exceed ? 'red' : 'default'}>{c.class_cn}（{c.class}）</Tag>
                        <Typography.Text type="secondary">
                          概率 {c.proba.toExponential(2)} / 阈值 {c.threshold.toExponential(2)} · 超阈倍数 {c.score}×
                        </Typography.Text>
                      </Space>
                      <Progress percent={Math.min(100, (c.score ?? 0) * 50)} showInfo={false}
                        strokeColor={c.exceed ? '#ff4d4f' : '#d9d9d9'} />
                    </div>
                  ))}
                  <Typography.Paragraph type="secondary" style={{ marginTop: 6, marginBottom: 0, fontSize: 12 }}>
                    {cPred.model_note}
                  </Typography.Paragraph>
                </Card>
              )}
            </Space>
          ),
        },
        {
          key: 'orders', label: '维修工单', children: (
            <Table<WorkOrder> rowKey="code" loading={loadingList} dataSource={orders} pagination={{ pageSize: 8 }}
              columns={[
                { title: '工单号', dataIndex: 'code' }, { title: '设备', dataIndex: 'device_code' },
                { title: '故障', dataIndex: 'fault_desc', ellipsis: true },
                { title: '状态', dataIndex: 'status', render: (v) => <Tag color={v === 'closed' ? 'green' : v === 'created' ? 'blue' : 'orange'}>{v === 'created' ? '已生成' : v}</Tag> },
                { title: '工程师', dataIndex: 'engineer' },
                { title: '预计(h)', dataIndex: 'est_hours' },
                { title: '备件', dataIndex: 'parts', render: (p: WorkOrder['parts']) => p?.length ? p.map((x) => `${x.name}${x.action ? '(缺货)' : ''}`).join('、') : '—' },
              ]} />
          ),
        },
      ]} />
    </div>
  );
}
