import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert, Button, Card, Col, Descriptions, Divider, Form, InputNumber, Row, Select, Space, Statistic,
  Table, Tag, Typography, message,
} from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import { api } from '../api';
import type { CostStructure, ProjectRow } from '../api/types';
import EChart from '../components/EChart';

const yuan = (v?: number | null) => {
  if (!v && v !== 0) return '—';
  if (Math.abs(v) >= 1e8) return `${(v / 1e8).toFixed(4)} 亿元`;
  if (Math.abs(v) >= 1e4) return `${(v / 1e4).toFixed(2)} 万元`;
  return `${v.toFixed(0)} 元`;
};

const PROCESS_OPTIONS = [
  { value: 'drilling', label: '穿孔凿岩' },
  { value: 'blasting', label: '爆破' },
  { value: 'loading', label: '铲装' },
  { value: 'hauling', label: '运输' },
  { value: 'dumping', label: '排土' },
];

export default function ProjectOps() {
  const [summary, setSummary] = useState<Record<string, any> | null>(null);
  const [projects, setProjects] = useState<ProjectRow[]>([]);
  const [report, setReport] = useState<Record<string, any> | null>(null);
  const [current, setCurrent] = useState<string>('');
  const [cs, setCs] = useState<CostStructure | null>(null);
  const [anchor, setAnchor] = useState<Record<string, any> | null>(null);
  const [forecast, setForecast] = useState<Record<string, any> | null>(null);
  const [risk, setRisk] = useState<Record<string, any> | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [s, p, r] = await Promise.all([api.projectSummary(), api.projectList(), api.projectReport()]);
      setSummary(s);
      setProjects(p.projects);
      setReport(r);
      const first = p.projects.find((x) => x.section_est_total_yuan > 0);
      if (first) setCurrent((c) => c || first.code);
    } catch (e: any) {
      message.warning(e?.message || '项目分析接口不可用（请先运行训练脚本生成基准）');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  useEffect(() => {
    if (!current) return;
    api.projectCostStructure(current).then(setCs).catch(() => setCs(null));
    api.projectAnchor(current).then(setAnchor).catch(() => setAnchor(null));
  }, [current]);

  const deploy = summary?.deploy;
  const costChart = useMemo(() => {
    if (!cs) return {};
    return {
      tooltip: { trigger: 'axis' },
      legend: { data: ['实测占比', '基准均值', '容差下界', '容差上界'] },
      grid: { left: 40, right: 16, top: 40, bottom: 28 },
      xAxis: { type: 'category', data: cs.items.map((i) => i.cost_type) },
      yAxis: { type: 'value', axisLabel: { formatter: '{value}%' } },
      series: [
        { name: '实测占比', type: 'bar', data: cs.items.map((i) => i.share_pct), itemStyle: { color: '#1677ff' } },
        { name: '基准均值', type: 'line', data: cs.items.map((i) => i.baseline_mean_pct), itemStyle: { color: '#fa8c16' } },
        { name: '容差下界', type: 'line', data: cs.items.map((i) => i.band_pct?.[0]), lineStyle: { type: 'dashed' }, itemStyle: { color: '#8c8c8c' } },
        { name: '容差上界', type: 'line', data: cs.items.map((i) => i.band_pct?.[1]), lineStyle: { type: 'dashed' }, itemStyle: { color: '#8c8c8c' } },
      ],
    };
  }, [cs]);

  const forecastChart = useMemo(() => {
    if (!forecast) return {};
    const items = forecast.items as any[];
    return {
      tooltip: { trigger: 'axis', valueFormatter: (v: number) => `${(v / 1e4).toFixed(1)} 万元` },
      grid: { left: 56, right: 16, top: 30, bottom: 28 },
      xAxis: { type: 'category', data: items.map((i) => i.cost_type) },
      yAxis: { type: 'value', axisLabel: { formatter: (v: number) => `${(v / 1e4).toFixed(0)}万` } },
      series: [{
        type: 'bar',
        data: items.map((i) => ({
          value: i.expected_yuan,
          itemStyle: { color: '#52c41a' },
        })),
        label: { show: true, position: 'top', formatter: (p: any) => `${(p.value / 1e4).toFixed(1)}万` },
      }],
    };
  }, [forecast]);

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Card
        size="small"
        title="项目运营分析 · 真实招标锚点 + 成本台账 + 标定基准"
        extra={<Button size="small" icon={<ReloadOutlined />} loading={loading} onClick={() => void load()}>刷新</Button>}
      >
        {deploy && (
          <Alert
            type={deploy.ml_deployed ? 'success' : 'info'}
            showIcon
            message={`分析方法：${deploy.production_method === 'ml_model' ? '机器学习模型' : '标定统计基准法（ML 未通过上线门控）'}`}
            description={
              <div style={{ fontSize: 12 }}>
                <div>门控规则：{deploy.gate_rule}</div>
                <div>证据：测试集 MAE {deploy.evidence?.test_mae_days} 天 vs 中位数基线 {deploy.evidence?.baseline_median_mae_days} 天；
                  LOPO MAE 95%CI 上界 {deploy.evidence?.lopo_mae_ci95_upper} 天；
                  分类准确率 {deploy.evidence?.classification_accuracy_vs_baseline?.[0]} vs 基线 {deploy.evidence?.classification_accuracy_vs_baseline?.[1]}</div>
                <div>结论：{deploy.reason}</div>
              </div>
            }
          />
        )}
      </Card>

      <Row gutter={12}>
        <Col span={6}><Card size="small"><Statistic title="项目总数（含锚点）" value={summary?.projects?.total ?? 0} suffix={`/ ${summary?.projects?.with_tender_anchor ?? 0}`} /></Card></Col>
        <Col span={6}><Card size="small"><Statistic title="计划总投资合计" value={(summary?.investment?.plan_invest_yuan ?? 0) / 1e8} precision={4} suffix="亿元" /></Card></Col>
        <Col span={6}><Card size="small"><Statistic title="标段预算合计" value={(summary?.investment?.section_est_total_yuan ?? 0) / 1e8} precision={4} suffix="亿元" /></Card></Col>
        <Col span={6}><Card size="small"><Statistic title="成本台账合计" value={(summary?.cost?.total_yuan ?? 0) / 1e4} precision={2} suffix="万元" /></Card></Col>
      </Row>

      <Row gutter={12}>
        <Col span={12}>
          <Card size="small" title="成本构成（全项目台账） vs 标定基准">
            <EChart height={260} option={{
              tooltip: { trigger: 'axis' },
              legend: { bottom: 0 },
              grid: { left: 40, right: 16, top: 24, bottom: 44 },
              xAxis: { type: 'category', data: (summary?.cost?.by_type ?? []).map((c: any) => c.cost_type) },
              yAxis: { type: 'value', axisLabel: { formatter: '{value}%' } },
              series: [
                { name: '实测占比', type: 'bar', data: (summary?.cost?.by_type ?? []).map((c: any) => c.share_pct), itemStyle: { color: '#1677ff' } },
                {
                  name: '基准均值', type: 'line', itemStyle: { color: '#fa8c16' },
                  data: (summary?.cost?.by_type ?? []).map((c: any) => summary?.cost?.baseline_structure_pct?.[c.cost_type]),
                },
              ],
            }} />
          </Card>
        </Col>
        <Col span={12}>
          <Card size="small" title="预算执行比率分布（合同额 / 标段预算）">
            {summary?.budget_execution_band ? (
              <Descriptions size="small" column={2} bordered>
                <Descriptions.Item label="标定项目数">{summary.budget_execution_band.n}</Descriptions.Item>
                <Descriptions.Item label="均值">{summary.budget_execution_band.mean}</Descriptions.Item>
                <Descriptions.Item label="P25">{summary.budget_execution_band.p25}</Descriptions.Item>
                <Descriptions.Item label="P50">{summary.budget_execution_band.p50}</Descriptions.Item>
                <Descriptions.Item label="P75（预警）">
                  <Tag color="orange">{summary.budget_execution_band.p75}</Tag>
                </Descriptions.Item>
                <Descriptions.Item label="P90（严重）">
                  <Tag color="red">{summary.budget_execution_band.p90}</Tag>
                </Descriptions.Item>
              </Descriptions>
            ) : <Typography.Text type="secondary">暂无数据</Typography.Text>}
            <Divider style={{ margin: '10px 0' }} />
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              口径：{summary?.budget_execution_band?.meaning}
            </Typography.Text>
          </Card>
        </Col>
      </Row>

      <Card size="small" title="项目清单（点击行查看成本构成）">
        <Table<ProjectRow>
          size="small"
          rowKey="code"
          loading={loading}
          dataSource={projects}
          pagination={{ pageSize: 8, showSizeChanger: false }}
          onRow={(r) => ({ onClick: () => setCurrent(r.code), style: { cursor: 'pointer' } })}
          rowClassName={(r) => (r.code === current ? 'ant-table-row-selected' : '')}
          columns={[
            { title: '项目编号', dataIndex: 'code', width: 175 },
            { title: '项目名称', dataIndex: 'name', ellipsis: true },
            { title: '行业', dataIndex: 'industry', width: 130, render: (v: string) => v || <Tag>内部</Tag> },
            { title: '地区', dataIndex: 'region', width: 100 },
            { title: '标段预算', dataIndex: 'section_est_total_yuan', width: 120, render: yuan, sorter: (a, b) => a.section_est_total_yuan - b.section_est_total_yuan },
            { title: '成本台账', dataIndex: 'total_cost_yuan', width: 120, render: yuan },
            {
              title: '预算执行', dataIndex: 'cost_to_budget_ratio', width: 110,
              render: (v: number | null) => (v ? <Tag color={v > 1.1748 ? 'red' : v > 1.1049 ? 'orange' : 'green'}>{v.toFixed(3)}</Tag> : '—'),
            },
            { title: '工期(天)', dataIndex: 'duration_days', width: 90 },
          ]}
        />
      </Card>

      <Row gutter={12}>
        <Col span={14}>
          <Card
            size="small"
            title={`成本构成明细${cs ? ` · ${cs.project.code} ${cs.project.name.slice(0, 20)}` : ''}`}
          >
            {cs ? (
              <>
                <EChart height={240} option={costChart} />
                <Table
                  size="small"
                  rowKey="cost_type"
                  pagination={false}
                  dataSource={cs.items}
                  columns={[
                    { title: '成本类型', dataIndex: 'cost_type', width: 90 },
                    { title: '金额', dataIndex: 'amount_yuan', render: yuan },
                    { title: '实测占比', dataIndex: 'share_pct', render: (v: number) => `${v}%` },
                    { title: '基准均值', dataIndex: 'baseline_mean_pct', render: (v: number) => (v ? `${v}%` : '—') },
                    { title: '容差带', render: (_: unknown, r: any) => (r.band_pct?.[0] ? `${r.band_pct[0]}% ~ ${r.band_pct[1]}%` : '—') },
                    { title: '偏差(pp)', dataIndex: 'deviation_pp', render: (v: number) => (v === null ? '—' : (v > 0 ? `+${v}` : `${v}`)) },
                    {
                      title: '判定', dataIndex: 'verdict',
                      render: (v: string) => <Tag color={v === '正常区间' ? 'green' : v === '偏高' ? 'red' : v === '偏低' ? 'orange' : 'default'}>{v}</Tag>,
                    },
                  ]}
                />
                <Divider style={{ margin: '10px 0' }} />
                {cs.budget_execution && (
                  <Descriptions size="small" column={2}>
                    <Descriptions.Item label="标段预算">{yuan(cs.budget_execution.section_est_total_yuan)}</Descriptions.Item>
                    <Descriptions.Item label="成本合计">{yuan(cs.budget_execution.total_cost_yuan)}</Descriptions.Item>
                    <Descriptions.Item label="执行比率">{cs.budget_execution.ratio.toFixed(4)}</Descriptions.Item>
                    <Descriptions.Item label="判定">
                      <Tag color={cs.budget_execution.level === '正常区间' ? 'green' : cs.budget_execution.level === '预警' ? 'orange' : 'red'}>
                        {cs.budget_execution.level}
                      </Tag>
                    </Descriptions.Item>
                    <Descriptions.Item label="台账期间" span={2}>{cs.periods.join('、') || '—'}</Descriptions.Item>
                    <Descriptions.Item label="分析结论" span={2}>{cs.conclusion}</Descriptions.Item>
                  </Descriptions>
                )}
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {cs.basis}；{cs.disclaimer}
                </Typography.Text>
              </>
            ) : <Typography.Text type="secondary">选择项目查看成本构成</Typography.Text>}
          </Card>
        </Col>
        <Col span={10}>
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Card size="small" title="招标锚点（真实公告口径）">
              {anchor ? (
                <Descriptions size="small" column={1} bordered>
                  <Descriptions.Item label="招标人">{anchor.tenderer || '—'}</Descriptions.Item>
                  <Descriptions.Item label="交易平台">{anchor.platform || '—'}</Descriptions.Item>
                  <Descriptions.Item label="公告日期">{anchor.publish_date || '—'}</Descriptions.Item>
                  <Descriptions.Item label="计划总投资">{yuan(anchor.plan_invest_yuan)}</Descriptions.Item>
                  <Descriptions.Item label="标段预算">{yuan(anchor.section_est_total_yuan)}</Descriptions.Item>
                  <Descriptions.Item label="中标金额">{yuan(anchor.win_amount_yuan)}</Descriptions.Item>
                  <Descriptions.Item label="资金来源">{anchor.funding_source || '—'}</Descriptions.Item>
                  <Descriptions.Item label="批复单位">{anchor.approval_authority || '—'}</Descriptions.Item>
                  <Descriptions.Item label="公告链接">
                    {anchor.source_url ? <a href={anchor.source_url} target="_blank" rel="noreferrer">{anchor.source_site}</a> : '—'}
                  </Descriptions.Item>
                </Descriptions>
              ) : <Typography.Text type="secondary">—</Typography.Text>}
            </Card>

            <Card size="small" title="成本结构测算（按标段预算）">
              <Form
                layout="inline"
                onFinish={async (v: { budget: number; days?: number }) => {
                  try {
                    setForecast(await api.projectCostForecast(Number(v.budget) * 1e4, Number(v.days || 0)));
                  } catch (e: any) {
                    message.error(e?.message || '测算失败');
                  }
                }}
              >
                <Form.Item name="budget" rules={[{ required: true, message: '请输入预算' }]}>
                  <InputNumber min={1} placeholder="标段预算（万元）" style={{ width: 168 }} />
                </Form.Item>
                <Form.Item name="days">
                  <InputNumber min={0} placeholder="工期（天）" style={{ width: 110 }} />
                </Form.Item>
                <Form.Item><Button type="primary" htmlType="submit">测算</Button></Form.Item>
              </Form>
              {forecast && (
                <div style={{ marginTop: 8 }}>
                  <Descriptions size="small" column={1}>
                    <Descriptions.Item label="预计总成本">{yuan(forecast.total_cost.expected_yuan)}</Descriptions.Item>
                    <Descriptions.Item label="合理区间">
                      {yuan(forecast.total_cost.range_yuan[0])} ~ {yuan(forecast.total_cost.range_yuan[1])}
                    </Descriptions.Item>
                    <Descriptions.Item label="P90 不利情形">{yuan(forecast.total_cost.worst_case_p90_yuan)}</Descriptions.Item>
                  </Descriptions>
                  <EChart height={180} option={forecastChart} />
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {forecast.method}；标定项目数 {forecast.sample?.calibration_projects}。{forecast.disclaimer}
                  </Typography.Text>
                </div>
              )}
            </Card>

            <Card size="small" title="工序交期风险（分位数缓冲）">
              <Form
                layout="inline"
                onFinish={async (v: any) => {
                  try {
                    setRisk(await api.projectDelayRisk({
                      process: v.process, plan_days: Number(v.plan_days || 0), workload: Number(v.workload || 0),
                    }));
                  } catch (e: any) {
                    message.error(e?.message || '查询失败');
                  }
                }}
              >
                <Form.Item name="process" initialValue="hauling">
                  <Select options={PROCESS_OPTIONS} style={{ width: 130 }} />
                </Form.Item>
                <Form.Item name="plan_days" initialValue={12}>
                  <InputNumber min={1} placeholder="计划工期(天)" style={{ width: 130 }} />
                </Form.Item>
                <Form.Item><Button htmlType="submit">评估</Button></Form.Item>
              </Form>
              {risk && (
                <div style={{ marginTop: 8 }}>
                  <Descriptions size="small" column={1}>
                    <Descriptions.Item label="样本量 / 参照">n={risk.reference.n}（{risk.reference.scope === 'by_process' ? '按工序' : '全工序'}）</Descriptions.Item>
                    <Descriptions.Item label="偏差分位">P80 {risk.reference.p80_days} 天 / P90 {risk.reference.p90_days} 天</Descriptions.Item>
                    <Descriptions.Item label="建议缓冲">
                      <Tag color="blue">{risk.suggestion.buffer_days_p80} 天</Tag>
                      建议完工 {risk.suggestion.recommended_finish_days} 天 · 风险 {risk.suggestion.risk_level}
                    </Descriptions.Item>
                  </Descriptions>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {risk.method}；{risk.why_not_ml}
                  </Typography.Text>
                </div>
              )}
            </Card>
          </Space>
        </Col>
      </Row>

      {report && (
        <Card size="small" title="模型评估报告与数据边界（可审计证据）">
          <Row gutter={12}>
            <Col span={12}>
              <Descriptions size="small" column={1} bordered>
                <Descriptions.Item label="训练时间">{report.trained_at}</Descriptions.Item>
                <Descriptions.Item label="样本">
                  已完工可标注任务 {report.dataset.labelled_tasks} / 全部任务 {report.dataset.tasks_all}；
                  成本台账 {report.dataset.costs} 条；项目 {report.dataset.projects_with_tasks} 个
                </Descriptions.Item>
                <Descriptions.Item label="划分">
                  训练项目 {report.dataset.projects_train} / 测试项目 {report.dataset.projects_test}（零交叉）
                </Descriptions.Item>
                <Descriptions.Item label="工期回归">
                  测试 MAE {report.task_delay.regression.test_mae_days} 天 / R² {report.task_delay.regression.test_r2}；
                  LOPO MAE {report.task_delay.regression.lopo_mae_mean} 天（95%CI {report.task_delay.regression.lopo_mae_ci95.join(' ~ ')}）
                </Descriptions.Item>
                <Descriptions.Item label="工期分类">
                  acc {report.task_delay.classification.accuracy} / macroF1 {report.task_delay.classification.macro_f1}；
                  基线（多数类）{report.task_delay.classification.baseline_accuracy}
                </Descriptions.Item>
                <Descriptions.Item label="协议">{report.protocol.cv}</Descriptions.Item>
              </Descriptions>
            </Col>
            <Col span={12}>
              <Alert
                type="warning"
                showIcon
                message="数据边界与限制（不得忽略）"
                description={<ul style={{ margin: 0, paddingLeft: 18, fontSize: 12 }}>
                  {(report.limitations || []).map((l: string) => <li key={l}>{l}</li>)}
                </ul>}
              />
            </Col>
          </Row>
        </Card>
      )}
    </Space>
  );
}
