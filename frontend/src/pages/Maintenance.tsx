import { useCallback, useEffect, useState } from 'react';
import { Alert, Button, Card, Col, Descriptions, Input, List, Row, Space, Statistic, Table, Tabs, Tag, Typography, message } from 'antd';
import { ExperimentOutlined, FileAddOutlined, SearchOutlined } from '@ant-design/icons';
import { api } from '../api';
import type { DiagnosisItem, WarningItem, WorkOrder } from '../api/types';

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
  }, []);

  useEffect(() => { load(); }, [load]);

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
