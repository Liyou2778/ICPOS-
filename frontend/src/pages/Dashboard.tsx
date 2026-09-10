import { useEffect, useMemo, useState } from 'react';
import { Card, Col, Progress, Row, Statistic, Table, Tag, Typography } from 'antd';
import { CheckCircleOutlined, WarningOutlined, FieldTimeOutlined, RiseOutlined } from '@ant-design/icons';
import { api } from '../api';
import type { DashboardSummary, TrendPoint, WarningItem } from '../api/types';
import EChart from '../components/EChart';
import type { EChartsOption } from 'echarts';

export default function Dashboard() {
  const [sum, setSum] = useState<DashboardSummary | null>(null);
  const [trends, setTrends] = useState<TrendPoint[]>([]);
  const [ab, setAb] = useState<{ metrics: Record<string, number> } | null>(null);
  const [warns, setWarns] = useState<WarningItem[]>([]);
  const [err, setErr] = useState('');

  const load = () => {
    api.dashboard().then(setSum).catch((e) => setErr((e as Error).message));
    api.trends().then((r) => setTrends(r.points || [])).catch(() => undefined);
    api.dispatchAB().then(setAb).catch(() => undefined);
    api.warnings('open').then((r) => setWarns(r.slice(0, 6))).catch(() => undefined);
  };
  useEffect(() => {
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, []);

  const trendOption = useMemo<EChartsOption>(() => ({
    tooltip: { trigger: 'axis' },
    legend: { data: ['日运量(吨)', '故障告警(次)'] },
    grid: { left: 50, right: 50, top: 40, bottom: 30 },
    xAxis: { type: 'category', data: trends.map((p) => `D${p.day}`) },
    yAxis: [{ type: 'value', name: '吨' }, { type: 'value', name: '次' }],
    series: [
      { name: '日运量(吨)', type: 'bar', data: trends.map((p) => Math.round(p.moved_t)), itemStyle: { color: '#1677ff' } },
      { name: '故障告警(次)', type: 'line', yAxisIndex: 1, data: trends.map((p) => p.faults), smooth: true, itemStyle: { color: '#fa541c' } },
    ],
  }), [trends]);

  const statePie = useMemo<EChartsOption>(() => {
    const d = sum?.device_state || {};
    const map = { working: '作业中', idle: '待命', fault: '故障', maintenance: '维保' };
    return {
      tooltip: { trigger: 'item' },
      legend: { bottom: 0 },
      series: [{
        type: 'pie', radius: ['40%', '68%'],
        data: Object.entries(d).map(([k, v]) => ({ name: map[k as keyof typeof map] ?? k, value: v })),
        label: { formatter: '{b}: {c}' },
      }],
    };
  }, [sum]);

  const abOption = useMemo<EChartsOption>(() => (ab ? {
    tooltip: { trigger: 'axis' },
    legend: { data: ['人工调度基线', 'AI 动态调度'] },
    xAxis: { type: 'category', data: ['空载率', '设备利用率'] },
    yAxis: { type: 'value', axisLabel: { formatter: '{value}%' } },
    series: [
      { name: '人工调度基线', type: 'bar', data: [ab.metrics.manual_idle_rate * 100, ab.metrics.manual_utilization * 100], itemStyle: { color: '#bfbfbf' } },
      { name: 'AI 动态调度', type: 'bar', data: [ab.metrics.ai_idle_rate * 100, ab.metrics.ai_utilization * 100], itemStyle: { color: '#1677ff' } },
    ],
  } : {}), [ab]);

  const st = sum?.device_state || {};
  return (
    <div className="page-container">
      <Typography.Title level={4} style={{ marginTop: 0 }}>运营驾驶舱 · 鄂尔多斯露天煤矿（P-MINING-001）</Typography.Title>
      {err && <Tag color="red">接口异常：{err}</Tag>}
      <Row gutter={[12, 12]}>
        <Col xs={12} md={6}><Card><Statistic title="在役设备（台）" value={sum?.device_total ?? 0} prefix={<CheckCircleOutlined style={{ color: '#52c41a' }} />} suffix={
          <span style={{ fontSize: 12, color: '#999' }}>作业 {st.working ?? 0} · 故障 {st.fault ?? 0}</span>} />
        </Card></Col>
        <Col xs={12} md={6}><Card><Statistic title="设备空载率" value={((sum?.idle_rate ?? 0) * 100).toFixed(1)} suffix="%" prefix={<RiseOutlined style={{ color: '#fa8c16' }} />} />
          <Typography.Text type="secondary">行业人工基线约 25%</Typography.Text></Card></Col>
        <Col xs={12} md={6}><Card><Statistic title="今日产量" value={sum?.yield_today_wan_t ?? 0} precision={1} suffix="万吨" prefix={<FieldTimeOutlined style={{ color: '#1677ff' }} />} /></Card></Col>
        <Col xs={12} md={6}><Card><Statistic title="开放预警 / 累计工单" value={sum?.warnings_open ?? 0} suffix={`/ ${sum?.workorders_total ?? 0}`} prefix={<WarningOutlined style={{ color: '#f5222d' }} />} /></Card></Col>
      </Row>
      <Row gutter={[12, 12]} style={{ marginTop: 12 }}>
        <Col xs={24} md={12}><Card title="近 30 天产量与故障趋势（模拟数据）"><EChart option={trendOption} height={300} /></Card></Col>
        <Col xs={24} md={12}>
          <Card title="AI 调度 vs 人工基线（同一模拟数据集 A/B）">
            <EChart option={abOption} height={300} />
            <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
              人工空载率 {ab?.metrics.manual_idle_rate ? (ab.metrics.manual_idle_rate * 100).toFixed(1) : '-'}% → AI {(ab?.metrics.ai_idle_rate ?? 0) * 100 >= 0 ? ((ab?.metrics.ai_idle_rate ?? 0) * 100).toFixed(1) : '-'}%
            </Typography.Paragraph>
          </Card>
        </Col>
        <Col xs={24} md={12}><Card title="设备状态分布"><EChart option={statePie} height={260} /></Card></Col>
        <Col xs={24} md={12}>
          <Card title="最新开放预警（前 6 条）">
            <Table<WarningItem> size="small" rowKey="id" pagination={false} dataSource={warns}
              columns={[
                { title: '设备', dataIndex: 'device_code' },
                { title: '故障码', dataIndex: 'fault_code', render: (v) => <Tag>{v || '-'}</Tag> },
                { title: '预测部件', dataIndex: 'predicted_part', ellipsis: true },
                { title: '等级', dataIndex: 'severity', render: (v) => <Tag color={v === 'H' ? 'red' : v === 'M' ? 'orange' : 'blue'}>{v}</Tag> },
                { title: '剩余(h)', dataIndex: 'remaining_hours' },
              ]} />
          </Card>
        </Col>
      </Row>
      <Card title="项目进度" style={{ marginTop: 12 }}>
        <Typography.Text>{sum?.project?.name}：完成度 {sum?.project?.progress_pct ?? 0}%</Typography.Text>
        <Progress percent={sum?.project?.progress_pct ?? 0} status="active" />
      </Card>
    </div>
  );
}
