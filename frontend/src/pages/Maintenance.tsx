import { useCallback, useEffect, useState } from 'react';
import {
  Alert, Button, Card, Col, Descriptions, Drawer, Input, InputNumber, List, Modal, Progress, Row,
  Select, Space, Statistic, Table, Tabs, Tag, Timeline, Tooltip, Typography, message,
} from 'antd';
import {
  FileAddOutlined, SearchOutlined, ToolOutlined, InboxOutlined,
  DashboardOutlined, WarningOutlined, MedicineBoxOutlined, ContainerOutlined,
} from '@ant-design/icons';
import { api } from '../api';
import type {
  ArchiveData, CorpusDevice, CorpusFault, CorpusPrediction, DiagnosisItem, OperationsData,
  WarningItem, WorkOrder, WorkOrderDetail,
} from '../api/types';

const SEV = { H: { c: 'red', t: '高' }, M: { c: 'orange', t: '中' }, L: { c: 'blue', t: '低' } } as const;
const STATE_CN: Record<string, string> = { working: '作业中', idle: '待命', fault: '故障', maintenance: '维保' };
const STATE_COLOR: Record<string, string> = { working: 'green', idle: 'gold', fault: 'red', maintenance: 'blue' };

export default function Maintenance() {
  // ---------- 设备运营 ----------
  const [ops, setOps] = useState<OperationsData | null>(null);
  // ---------- 预警中心 ----------
  const [warns, setWarns] = useState<WarningItem[]>([]);
  const [activeWarn, setActiveWarn] = useState<WarningItem | null>(null);
  const [modelSource, setModelSource] = useState<'v1' | 'corpus'>('v1');
  const [cDevices, setCDevices] = useState<CorpusDevice[]>([]);
  const [cFaults, setCFaults] = useState<CorpusFault[]>([]);
  const [cPred, setCPred] = useState<CorpusPrediction | null>(null);
  // ---------- 智能诊断 ----------
  const [diagText, setDiagText] = useState('矿卡液压油温高、动作没劲，怀疑漏油');
  const [diagDevice, setDiagDevice] = useState('T02');
  const [diag, setDiag] = useState<{ top3: DiagnosisItem[]; fix_plan: string; parts: { name: string }[]; est_hours: number; engineer: string } | null>(null);
  // ---------- 工单中心 ----------
  const [orders, setOrders] = useState<WorkOrder[]>([]);
  const [detail, setDetail] = useState<WorkOrderDetail | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [advanceTo, setAdvanceTo] = useState<string>('');
  const [advanceNote, setAdvanceNote] = useState('');
  const [laborHours, setLaborHours] = useState<number | null>(null);
  const [repairNotes, setRepairNotes] = useState('');
  // ---------- 维修归档 ----------
  const [archive, setArchive] = useState<ArchiveData | null>(null);
  const [loading, setLoading] = useState(false);
  const [activeTab, setActiveTab] = useState('ops');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [o, w, wo, ar] = await Promise.all([api.operations(), api.warnings(), api.workorders(), api.archive()]);
      setOps(o);
      setWarns(w);
      setOrders(wo);
      setArchive(ar);
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setLoading(false);
    }
    try {
      const [cd, cf] = await Promise.all([api.corpusDevices(), api.corpusFaults()]);
      setCDevices(cd.devices);
      setCFaults(cf.faults);
    } catch { /* 语料模型不可用时忽略 */ }
  }, []);

  useEffect(() => { load(); }, [load]);

  // ---------- 动作 ----------
  const runDiagnose = async (text?: string) => {
    try {
      setDiag(await api.diagnose(text ?? diagText));
      message.success('诊断完成');
    } catch (e) { message.error((e as Error).message); }
  };

  const runCorpusPredict = async (deviceId: string, at?: string) => {
    try {
      setCPred(await api.predictCorpus(deviceId, at));
    } catch (e) { message.error((e as Error).message); }
  };

  const createOrder = async (deviceCode: string, code: string) => {
    try {
      const wo = await api.createWorkorder(deviceCode, '', code);
      message.success(`已生成工单 ${wo.code}`);
      load();
      setActiveTab('orders');
    } catch (e) { message.error((e as Error).message); }
  };

  const openDetail = async (code: string) => {
    try {
      const d = await api.workorderDetail(code);
      setDetail(d);
      setAdvanceTo('');
      setAdvanceNote('');
      setLaborHours(d.labor_hours || null);
      setRepairNotes('');
      setDetailOpen(true);
    } catch (e) { message.error((e as Error).message); }
  };

  const doAdvance = async () => {
    if (!detail) return;
    if (!advanceTo) { message.warning('请选择要推进到的状态'); return; }
    try {
      const d = await api.advanceWorkorder(detail.code, {
        to_status: advanceTo, note: advanceNote, operator: '当前用户',
        labor_hours: laborHours ?? undefined, repair_notes: repairNotes || undefined,
      });
      setDetail(d);
      setAdvanceTo('');
      setAdvanceNote('');
      setRepairNotes('');
      message.success(`工单已推进到「${d.status_cn}」`);
      load();
    } catch (e) { message.error((e as Error).message); }
  };

  const generateMaintenanceOrder = async (deviceCode: string) => {
    try {
      const wo = await api.createWorkorder(deviceCode, '保养计划到期，执行例行保养', 'MNT-02');
      message.success(`保养工单 ${wo.code} 已生成`);
      load();
    } catch (e) { message.error((e as Error).message); }
  };

  const nextStatuses = (cur?: string) => {
    const flow = archive?.status_flow?.map((x) => x.key) || ['created', 'dispatched', 'repairing', 'pending_acceptance', 'completed', 'archived'];
    const idx = flow.indexOf(cur || 'created');
    return archive?.status_flow?.slice(idx + 1) || [];
  };

  return (
    <div className="page-container">
      <Typography.Title level={4} style={{ marginTop: 0 }}>
        智能运维中心 · 设备运营 / 预警 / 诊断 / 工单 / 归档 全链路
      </Typography.Title>
      <Tabs activeKey={activeTab} onChange={setActiveTab} items={[
        // ================= ① 设备运营 =================
        {
          key: 'ops', label: <span><DashboardOutlined />设备运营</span>, children: (
            <Space direction="vertical" style={{ width: '100%' }} size={12}>
              <Row gutter={[12, 12]}>
                <Col xs={12} md={4}><Card size="small"><Statistic title="在役设备" value={ops?.summary.device_total ?? 0} suffix="台" /></Card></Col>
                <Col xs={12} md={4}><Card size="small"><Statistic title="作业中 / 故障" value={`${ops?.summary.working ?? 0} / ${ops?.summary.fault ?? 0}`} /></Card></Col>
                <Col xs={12} md={4}><Card size="small"><Statistic title="平均利用率" value={((ops?.summary.avg_utilization ?? 0) * 100).toFixed(1)} suffix="%" /></Card></Col>
                <Col xs={12} md={4}><Card size="small"><Statistic title="开放预警" value={ops?.summary.open_warnings ?? 0} /></Card></Col>
                <Col xs={12} md={4}><Card size="small"><Statistic title="保养到期" value={ops?.summary.maintenance_due ?? 0} /></Card></Col>
                <Col xs={12} md={4}><Card size="small"><Statistic title="备件预警" value={ops?.summary.spare_alerts ?? 0} /></Card></Col>
              </Row>
              <Card size="small" title="设备台账与实时状态（含利用率 / 工时 / 能耗 / 健康评分）" loading={loading}>
                <Table<OperationsData['devices'][number]> rowKey="code" size="small" dataSource={ops?.devices || []} pagination={false}
                  columns={[
                    { title: '设备', dataIndex: 'code', render: (v, r) => <Space><b>{v}</b><Typography.Text type="secondary">{r.category_cn}</Typography.Text></Space> },
                    { title: '型号', dataIndex: 'model_code' },
                    { title: '状态', dataIndex: 'work_state', render: (v) => <Tag color={STATE_COLOR[v] ?? 'default'}>{STATE_CN[v] ?? v}</Tag> },
                    { title: '工时(h)', dataIndex: 'work_hours', sorter: (a, b) => a.work_hours - b.work_hours },
                    { title: '利用率', dataIndex: 'utilization', render: (v) => <Progress percent={Math.round(v * 100)} size="small" style={{ width: 120 }} /> },
                    { title: '油耗(L)', dataIndex: 'fuel_l', render: (v) => v.toLocaleString() },
                    {
                      title: '健康评分', dataIndex: 'health_score', sorter: (a, b) => a.health_score - b.health_score,
                      render: (v) => <Tag color={v >= 85 ? 'green' : v >= 60 ? 'orange' : 'red'}>{v}</Tag>,
                    },
                    { title: '预警', dataIndex: 'open_warnings', render: (v, r) => v ? <Tag color={r.high_warnings ? 'red' : 'orange'}>{v}（高 {r.high_warnings}）</Tag> : '—' },
                    {
                      title: '保养', dataIndex: 'maintenance_due',
                      render: (v, r) => v ? <Tooltip title={r.plan_items.join('、')}><Tag color="gold">到期</Tag></Tooltip> : '—',
                    },
                    {
                      title: '操作', render: (_, r) => (
                        <Space size={4}>
                          <Button size="small" type="link" icon={<ToolOutlined />} onClick={() => { setDiagDevice(r.code); setDiagText(`${r.name} 出现异常，请诊断`); setActiveTab('diagnose'); }}>诊断</Button>
                          {r.maintenance_due && <Button size="small" type="link" icon={<FileAddOutlined />} onClick={() => generateMaintenanceOrder(r.code)}>保养工单</Button>}
                        </Space>
                      ),
                    },
                  ]} />
              </Card>
              <Row gutter={[12, 12]}>
                <Col xs={24} md={12}>
                  <Card size="small" title="备件库存预警（库存 ≤ 2 触发）">
                    <Table size="small" rowKey="sku" pagination={false} dataSource={ops?.spare_alerts || []}
                      columns={[
                        { title: '备件', dataIndex: 'name' }, { title: 'SKU', dataIndex: 'sku' },
                        { title: '库存', dataIndex: 'stock', render: (v) => <Tag color={v <= 1 ? 'red' : 'orange'}>{v}</Tag> },
                        { title: '单价(元)', dataIndex: 'price_cny', render: (v) => v.toLocaleString() },
                      ]} />
                  </Card>
                </Col>
                <Col xs={24} md={12}>
                  <Card size="small" title="保养计划到期提醒">
                    <List size="small" dataSource={ops?.plan_due || []}
                      renderItem={(p) => (
                        <List.Item actions={[<Button key="wo" size="small" type="link" onClick={() => generateMaintenanceOrder(p.device_code)}>生成保养工单</Button>]}>
                          <Space><Tag color="gold">{p.device_code}</Tag>{p.item}<Typography.Text type="secondary">（{p.window} / 定额 {p.due_hours}h）</Typography.Text></Space>
                        </List.Item>
                      )} />
                  </Card>
                </Col>
              </Row>
            </Space>
          ),
        },
        // ================= ② 预警中心 =================
        {
          key: 'warnings', label: <span><WarningOutlined />预警中心</span>, children: (
            <Space direction="vertical" style={{ width: '100%' }} size={12}>
              <Alert type="info" showIcon
                message="预测性维护已融合到预警中心：本矿设备使用 v1 模型（遥测温漂），语料仿真设备使用企业级多检测器模型（P1 检出 10/10、平均提前量 24.3h、误报 2%）" />
              <Space>
                <Typography.Text>模型来源：</Typography.Text>
                <Select style={{ width: 240 }} value={modelSource} onChange={setModelSource}
                  options={[{ value: 'v1', label: '本矿设备（v1 温漂模型）' }, { value: 'corpus', label: '语料仿真设备（多检测器）' }]} />
              </Space>
              {modelSource === 'v1' ? (
                <Table<WarningItem> rowKey="id" size="small" loading={loading} dataSource={warns} pagination={{ pageSize: 8 }}
                  onRow={(r) => ({ onClick: () => setActiveWarn(r), style: { cursor: 'pointer' } })}
                  columns={[
                    { title: '设备', dataIndex: 'device_code' },
                    { title: '故障码', dataIndex: 'fault_code', render: (v) => <Tag>{v || '—'}</Tag> },
                    { title: '异常部件', dataIndex: 'predicted_part', ellipsis: true },
                    { title: '严重等级', dataIndex: 'severity', render: (v) => <Tag color={SEV[v as keyof typeof SEV]?.c ?? 'blue'}>{SEV[v as keyof typeof SEV]?.t ?? v}</Tag> },
                    { title: '剩余可用(h)', dataIndex: 'remaining_hours', render: (v) => (v == null ? '未知' : v) },
                    { title: '置信度', dataIndex: 'model_conf', render: (v) => `${(v * 100).toFixed(0)}%` },
                    { title: '状态', dataIndex: 'status', render: (v) => <Tag color={v === 'open' ? 'red' : v === 'converted' ? 'blue' : 'default'}>{v === 'open' ? '待处理' : v === 'converted' ? '已转工单' : '已关闭'}</Tag> },
                    {
                      title: '操作', render: (_, r) => (
                        <Space size={4}>
                          <Button size="small" type="link" onClick={(e) => { e.stopPropagation(); setDiagDevice(r.device_code); setDiagText(`${r.device_code} ${r.predicted_part} ${r.probable_cause}，请诊断`); setActiveTab('diagnose'); }}>诊断</Button>
                          <Button size="small" type="link" disabled={r.status !== 'open'} onClick={(e) => { e.stopPropagation(); createOrder(r.device_code, r.fault_code); }}>生成工单</Button>
                        </Space>
                      ),
                    },
                  ]} />
              ) : (
                <Card size="small" title="语料设备部件级推理（可一键载入真实故障时刻验证）">
                  <Space direction="vertical" style={{ width: '100%' }}>
                    <Space wrap>
                      {cFaults.map((f) => (
                        <Tag key={f.code + f.device_id} color="blue" style={{ cursor: 'pointer' }}
                          onClick={() => {
                            const t = new Date(new Date(f.onset_ts).getTime() - 6 * 3600 * 1000).toISOString().slice(0, 16);
                            runCorpusPredict(f.device_id, t);
                          }}>
                          {f.device_id} {f.code}（{f.component}，真值提前 {f.lead_hours}h）
                        </Tag>
                      ))}
                    </Space>
                    <Space>
                      <Select style={{ width: 260 }} placeholder="选择语料设备"
                        options={cDevices.map((d) => ({ value: d.device_id, label: `${d.device_id} · ${d.model} · ${d.category}` }))}
                        onChange={(v) => runCorpusPredict(v)} />
                    </Space>
                    {cPred && (
                      <Card size="small" type="inner" title={`${cPred.device_id}${cPred.at ? ` @ ${cPred.at}` : ''}`}
                        extra={<Tag color={cPred.risky ? 'red' : 'green'}>{cPred.risky ? '风险' : '健康'}</Tag>}>
                        {cPred.classes.map((c) => (
                          <div key={c.class} style={{ marginBottom: 6 }}>
                            <Space>
                              <Tag color={c.exceed ? 'red' : 'default'}>{c.class_cn}（{c.class}）</Tag>
                              <Typography.Text type="secondary">概率 {c.proba.toExponential(2)} / 阈值 {c.threshold.toExponential(2)}（{c.score}×）</Typography.Text>
                            </Space>
                            <Progress percent={Math.min(100, c.score * 50)} showInfo={false} strokeColor={c.exceed ? '#ff4d4f' : '#d9d9d9'} />
                          </div>
                        ))}
                        <Typography.Paragraph type="secondary" style={{ marginBottom: 0, fontSize: 12 }}>{cPred.note}</Typography.Paragraph>
                      </Card>
                    )}
                  </Space>
                </Card>
              )}
            </Space>
          ),
        },
        // ================= ③ 智能诊断 =================
        {
          key: 'diagnose', label: <span><MedicineBoxOutlined />智能诊断</span>, children: (
            <Space direction="vertical" style={{ width: '100%' }} size={12}>
              <Space.Compact style={{ width: '100%' }}>
                <Input style={{ maxWidth: 160 }} value={diagDevice} onChange={(e) => setDiagDevice(e.target.value)} placeholder="设备编码" />
                <Input value={diagText} onChange={(e) => setDiagText(e.target.value)} placeholder="故障现象自然语言或故障代码（如 HYD-01）" />
                <Button type="primary" icon={<SearchOutlined />} onClick={() => runDiagnose()}>诊断</Button>
              </Space.Compact>
              {diag && (
                <Row gutter={[12, 12]}>
                  {diag.top3.map((d, i) => (
                    <Col xs={24} md={8} key={d.code}>
                      <Card size="small" title={`${i + 1}. ${d.code} ${d.name}`}
                        extra={<Tag color={d.severity === 'H' ? 'red' : 'orange'}>{SEV[d.severity as keyof typeof SEV]?.t ?? d.severity}</Tag>}>
                        <Statistic title="置信度" value={d.confidence * 100} precision={0} suffix="%" />
                      </Card>
                    </Col>
                  ))}
                  <Col span={24}>
                    <Card size="small" title="维修方案 / 备件 / 工时 / 推荐工程师"
                      extra={<Button type="primary" size="small" icon={<FileAddOutlined />} disabled={!diag.top3.length || diag.top3[0].code === 'UNKNOWN'}
                        onClick={() => createOrder(diagDevice, diag.top3[0].code)}>生成维修工单</Button>}>
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
              {diag?.top3?.[0]?.code === 'UNKNOWN' && <Alert type="warning" showIcon message="维保知识库未覆盖该描述：系统拒答并转人工（防幻觉：陈述必有出处）" />}
            </Space>
          ),
        },
        // ================= ④ 工单中心 =================
        {
          key: 'orders', label: <span><ContainerOutlined />工单中心</span>, children: (
            <Space direction="vertical" style={{ width: '100%' }} size={12}>
              <Space wrap>
                {(archive?.status_flow || []).map((s) => (
                  <Tag key={s.key} color={orders.filter((o) => o.status === s.key).length ? 'blue' : 'default'}>
                    {s.label}：{orders.filter((o) => o.status === s.key).length}
                  </Tag>
                ))}
              </Space>
              <Table<WorkOrder> rowKey="code" size="small" loading={loading} dataSource={orders} pagination={{ pageSize: 10 }}
                onRow={(r) => ({ onClick: () => openDetail(r.code), style: { cursor: 'pointer' } })}
                columns={[
                  { title: '工单号', dataIndex: 'code' },
                  { title: '设备', dataIndex: 'device_code' },
                  { title: '故障', dataIndex: 'fault_desc', ellipsis: true },
                  { title: '严重', dataIndex: 'severity', render: (v) => <Tag color={SEV[(v || 'M') as keyof typeof SEV]?.c}>{SEV[(v || 'M') as keyof typeof SEV]?.t}</Tag> },
                  {
                    title: '状态', dataIndex: 'status',
                    render: (v) => <Tag color={archive?.status_flow?.find((x) => x.key === v) ? 'blue' : 'default'}>
                      {archive?.status_flow?.find((x) => x.key === v)?.label ?? v}
                    </Tag>,
                  },
                  { title: '工程师', dataIndex: 'engineer' },
                  { title: '实际工时', dataIndex: 'labor_hours', render: (v) => (v ? `${v}h` : '—') },
                  { title: '操作', render: (_, r) => <Button size="small" type="link" onClick={(e) => { e.stopPropagation(); openDetail(r.code); }}>流转/详情</Button> },
                ]} />
            </Space>
          ),
        },
        // ================= ⑤ 维修归档 =================
        {
          key: 'archive', label: <span><InboxOutlined />维修归档</span>, children: (
            <Space direction="vertical" style={{ width: '100%' }} size={12}>
              <Row gutter={[12, 12]}>
                <Col xs={12} md={4}><Card size="small"><Statistic title="工单总数" value={archive?.summary.total_orders ?? 0} /></Card></Col>
                <Col xs={12} md={4}><Card size="small"><Statistic title="进行中" value={archive?.summary.in_progress ?? 0} /></Card></Col>
                <Col xs={12} md={4}><Card size="small"><Statistic title="已完成" value={archive?.summary.completed ?? 0} /></Card></Col>
                <Col xs={12} md={4}><Card size="small"><Statistic title="已归档" value={archive?.summary.archived ?? 0} /></Card></Col>
                <Col xs={12} md={4}><Card size="small"><Statistic title="MTTR" value={archive?.summary.mttr_hours ?? 0} suffix="h" /></Card></Col>
                <Col xs={12} md={4}><Card size="small"><Statistic title="复发设备" value={archive?.summary.recurrence_devices ?? 0} /></Card></Col>
              </Row>
              <Card size="small" title="已归档工单（案例库，可点击查看维修记录与时间线）">
                <Table<WorkOrderDetail> rowKey="code" size="small" dataSource={archive?.archived || []} pagination={{ pageSize: 8 }}
                  onRow={(r) => ({ onClick: () => openDetail(r.code), style: { cursor: 'pointer' } })}
                  columns={[
                    { title: '工单号', dataIndex: 'code' },
                    { title: '设备', dataIndex: 'device_code' },
                    { title: '故障码', dataIndex: 'fault_code', render: (v) => <Tag>{v}</Tag> },
                    { title: '维修记录', dataIndex: 'repair_notes', ellipsis: true },
                    { title: '实际工时', dataIndex: 'labor_hours', render: (v) => (v ? `${v}h` : '—') },
                    { title: '归档时间', dataIndex: 'archived_at', render: (v) => (v ? v.slice(0, 16).replace('T', ' ') : '—') },
                  ]} />
              </Card>
              <Row gutter={[12, 12]}>
                <Col xs={24} md={12}>
                  <Card size="small" title="故障分布（Top）">
                    <Table size="small" rowKey="fault_code" pagination={false} dataSource={archive?.fault_distribution || []}
                      columns={[{ title: '故障码', dataIndex: 'fault_code' }, { title: '次数', dataIndex: 'count' }]} />
                  </Card>
                </Col>
                <Col xs={24} md={12}>
                  <Card size="small" title="备件消耗统计">
                    <Table size="small" rowKey="name" pagination={false} dataSource={archive?.parts_consumption || []}
                      columns={[{ title: '备件', dataIndex: 'name' }, { title: '消耗数量', dataIndex: 'qty' }]} />
                  </Card>
                </Col>
              </Row>
              {(archive?.recurrence?.length || 0) > 0 && (
                <Alert type="warning" showIcon message="复发设备提示（同一设备多次维修，建议纳入专项分析）"
                  description={archive?.recurrence.map((r) => `${r.device_code}：${r.orders} 张工单`).join('；')} />
              )}
            </Space>
          ),
        },
      ]} />

      {/* 预警详情抽屉 */}
      <Drawer open={!!activeWarn} onClose={() => setActiveWarn(null)} width={460}
        title={activeWarn ? `预警详情 · ${activeWarn.device_code} / ${activeWarn.fault_code || '—'}` : ''}>
        {activeWarn && (
          <Descriptions column={1} size="small" bordered
            items={[
              { key: 'part', label: '异常部件', children: activeWarn.predicted_part },
              { key: 'cause', label: '可能原因', children: activeWarn.probable_cause },
              { key: 'sev', label: '严重等级', children: <Tag color={SEV[activeWarn.severity as keyof typeof SEV]?.c}>{SEV[activeWarn.severity as keyof typeof SEV]?.t ?? activeWarn.severity}</Tag> },
              { key: 'advice', label: '建议措施', children: activeWarn.advice },
              { key: 'rul', label: '预计剩余可用', children: activeWarn.remaining_hours == null ? '未知' : `${activeWarn.remaining_hours} 小时` },
              { key: 'conf', label: '模型置信度', children: `${(activeWarn.model_conf * 100).toFixed(0)}%` },
            ]} />
        )}
      </Drawer>

      {/* 工单详情 + 状态流转 */}
      <Modal open={detailOpen} onCancel={() => setDetailOpen(false)} footer={null} width={760}
        title={detail ? `工单 ${detail.code} · ${detail.status_cn}` : ''}>
        {detail && (
          <Space direction="vertical" style={{ width: '100%' }} size={12}>
            <Descriptions column={{ xs: 1, md: 2 }} size="small" bordered
              items={[
                { key: 'dev', label: '设备', children: detail.device_code },
                { key: 'code', label: '故障码', children: detail.fault_code },
                { key: 'desc', label: '故障描述', children: detail.fault_desc },
                { key: 'eng', label: '工程师', children: detail.engineer },
                { key: 'plan', label: '维修方案', children: detail.fix_plan },
                { key: 'hours', label: '预计 / 实际工时', children: `${detail.est_hours}h / ${detail.labor_hours || '—'}h` },
                { key: 'notes', label: '维修记录', children: detail.repair_notes || '—' },
              ]} />
            <Card size="small" title="维修状态实时跟踪（时间线）">
              <Timeline items={detail.timeline.map((t) => ({
                color: t.to === 'archived' ? 'gray' : t.to === 'completed' ? 'green' : t.to === 'repairing' ? 'orange' : 'blue',
                children: (
                  <Space direction="vertical" size={0}>
                    <span><b>{t.to_cn}</b> <Typography.Text type="secondary">{t.at ? t.at.slice(0, 16).replace('T', ' ') : ''}</Typography.Text></span>
                    <Typography.Text type="secondary">{t.operator}：{t.note}</Typography.Text>
                  </Space>
                ),
              }))} />
            </Card>
            <Card size="small" title="推进工单状态（六状态机，仅允许向前）">
              <Space direction="vertical" style={{ width: '100%' }}>
                <Space wrap>
                  {nextStatuses(detail.status).map((s) => (
                    <Button key={s.key} type={advanceTo === s.key ? 'primary' : 'default'} size="small"
                      onClick={() => setAdvanceTo(s.key)}>{s.label}</Button>
                  ))}
                  {nextStatuses(detail.status).length === 0 && <Typography.Text type="secondary">已到终态（已归档）</Typography.Text>}
                </Space>
                <Input.TextArea rows={2} value={advanceNote} onChange={(e) => setAdvanceNote(e.target.value)} placeholder="状态变更说明（如：已到场开始拆检 / 验收通过）" />
                <Space>
                  <InputNumber value={laborHours} onChange={(v) => setLaborHours(v)} placeholder="实际工时(h)" min={0} />
                  <Input value={repairNotes} onChange={(e) => setRepairNotes(e.target.value)} placeholder="维修记录（可选）" style={{ width: 300 }} />
                  <Button type="primary" onClick={doAdvance} disabled={!advanceTo}>确认推进</Button>
                </Space>
              </Space>
            </Card>
          </Space>
        )}
      </Modal>
    </div>
  );
}
