import { useMemo, useState } from 'react';
import { Alert, Button, Card, Col, Collapse, Divider, Radio, Row, Segmented, Space, Spin, Table, Tag, Typography, message } from 'antd';
import { FilePdfOutlined, FileWordOutlined, ThunderboltOutlined } from '@ant-design/icons';
import { api } from '../api';
import type { SolutionPlan } from '../api/types';
import EChart from '../components/EChart';
import type { EChartsOption } from 'echarts';
import { getToken } from '../api/client';

const PRESETS = [
  { key: 'mining', label: '矿山施工（示例）', text: '我是矿山生产主管，年产200万吨矿石，工期3年，预算1.5亿元，岩石较硬，帮我生成矿山开采施工方案并推荐设备。', doc: 'construction' },
  { key: 'bid', label: '投标方案（示例）', text: '矿山剥离项目招标：年产300万吨剥离，工期2年，预算2亿元，请生成完整投标方案并检查招标响应度。', doc: 'bid' },
  { key: 'selection', label: '设备选型采购（示例）', text: '土方工程需要采购挖掘机与运输设备，年挖填方150万方，工期2年，预算6000万元，请生成设备选型采购方案。', doc: 'selection' },
];

async function downloadFile(name: string) {
  const resp = await fetch(`/api/solutions/files/${encodeURIComponent(name)}`, { headers: { Authorization: `Bearer ${getToken()}` } });
  if (!resp.ok) throw new Error('文件下载失败');
  const blob = await resp.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

export default function Workspace() {
  const [text, setText] = useState(PRESETS[0].text);
  const [docType, setDocType] = useState('construction');
  const [running, setRunning] = useState(false);
  const [plan, setPlan] = useState<SolutionPlan | null>(null);
  const [needMore, setNeedMore] = useState<string[]>([]);
  const [latency, setLatency] = useState<number | null>(null);
  const [exporting, setExporting] = useState<string | null>(null);

  const generate = async () => {
    setRunning(true);
    setNeedMore([]);
    setLatency(null);
    try {
      const t0 = performance.now();
      const res = await api.plan(text, docType);
      setLatency((performance.now() - t0) / 1000);
      if (res.status === 'need_more') {
        setNeedMore(res.facts || []);
        setPlan(null);
      } else {
        setPlan(res.payload);
      }
    } catch (e) {
      message.error((e as Error).message || '生成失败');
    } finally {
      setRunning(false);
    }
  };

  const exportDoc = async (fmt: 'docx' | 'pdf') => {
    if (!plan) return;
    setExporting(fmt);
    try {
      const res = await api.exportDoc(plan, plan.doc_type, plan.title);
      const name = fmt === 'docx' ? res.docx : res.pdf;
      if (name) {
        await downloadFile(name);
        message.success(`已导出 ${fmt.toUpperCase()}（${name.slice(0, 30)}…）`);
      } else {
        message.info('PDF 依赖 LibreOffice，未安装时仅提供 Word 导出');
      }
    } catch (e) {
      message.error((e as Error).message || '导出失败');
    } finally {
      setExporting(null);
    }
  };

  const compareOption = useMemo<EChartsOption>(() => (plan ? {
    tooltip: { trigger: 'axis' },
    legend: { data: ['三年TCO(万元)', '日产能(百吨)'] },
    xAxis: { type: 'category', data: plan.bundles.map((b) => b.name) },
    yAxis: [{ type: 'value' }, { type: 'value' }],
    series: [
      { name: '三年TCO(万元)', type: 'bar', data: plan.bundles.map((b) => Math.round(b.tco_3y_total_cny / 1e4)), itemStyle: { color: '#1677ff' } },
      { name: '日产能(百吨)', type: 'line', yAxisIndex: 1, data: plan.bundles.map((b) => Math.round(b.daily_capacity_t / 100)), itemStyle: { color: '#52c41a' } },
    ],
  } : {}), [plan]);

  return (
    <div className="page-container">
      <Row gutter={[16, 16]}>
        <Col xs={24} lg={10}>
          <Card title="① 需求输入（自然语言 → 结构化需求分析）">
            <Segmented block options={PRESETS.map((p) => ({ label: p.label, value: p.key }))}
              onChange={(k) => { const p = PRESETS.find((x) => x.key === k)!; setText(p.text); setDocType(p.doc); }} />
            <Radio.Group value={docType} onChange={(e) => setDocType(e.target.value)} style={{ margin: '10px 0' }}
              options={[{ label: '施工组织设计', value: 'construction' }, { label: '投标方案', value: 'bid' }, { label: '设备选型', value: 'selection' }]} />
            <Typography.Paragraph type="secondary" style={{ marginBottom: 4 }}>
              需求文本（缺参数时智能体将追问，不会盲目生成）：
            </Typography.Paragraph>
            <textarea value={text} onChange={(e) => setText(e.target.value)} rows={7}
              style={{ width: '100%', padding: 8, border: '1px solid #d9d9d9', borderRadius: 6 }} />
            <Button type="primary" block loading={running} icon={<ThunderboltOutlined />} onClick={generate} style={{ marginTop: 8 }}>
              {running ? '需求分析 + 方案生成中…' : '生成方案（选型/TCO/施工组织/文档）'}
            </Button>
            {latency !== null && <Tag style={{ marginTop: 8 }} color="green">生成耗时 {latency.toFixed(2)}s（PRD 目标 ≤30s）</Tag>}
            <Divider style={{ margin: '12px 0' }} />
            <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 0 }}>
              演示剧情：需求输入 → 需求分析智能体结构化解析 → 方案生成智能体输出 ≥3 套选型（含型号/参数/配置/三年 TCO），
              并按模板装配成文档（可 Word/PDF 导出）。
            </Typography.Paragraph>
          </Card>
        </Col>
        <Col xs={24} lg={14}>
          {needMore.length > 0 && (
            <Card title="需求信息不完整 · 请补充（智能体追问，不盲目生成）">
              {needMore.map((q, i) => <Alert key={i} style={{ marginBottom: 6 }} type="warning" message={q} showIcon />)}
            </Card>
          )}
          {!plan && !needMore.length && (
            <Card><EmptyHint /></Card>
          )}
          {plan && (
            <Spin spinning={running}>
              <Card title={<>② {plan.title}</>} extra={<Space>
                <Button size="small" icon={<FileWordOutlined />} loading={exporting === 'docx'} onClick={() => exportDoc('docx')}>导出 Word</Button>
                <Button size="small" icon={<FilePdfOutlined />} loading={exporting === 'pdf'} onClick={() => exportDoc('pdf')}>导出 PDF</Button>
              </Space>}>
                <EChart option={compareOption} height={220} />
                <Table rowKey="name" size="small" pagination={false} dataSource={plan.bundles.map((b, i) => ({
                  ...b, idx: i, fleet: b.fleet.map((f) => `${f.model_name}×${f.count}`).join(' + '),
                }))}
                  columns={[
                    { title: '方案', dataIndex: 'name', render: (v, r) => <Space>{v}{r.idx === plan.best_index && <Tag color="gold">推荐</Tag>}</Space> },
                    { title: '设备组合', dataIndex: 'fleet', ellipsis: true },
                    { title: '日产能(t)', dataIndex: 'daily_capacity_t', render: (v) => v.toLocaleString() },
                    { title: '三年TCO(元)', dataIndex: 'tco_3y_total_cny', render: (v) => v.toLocaleString() },
                    { title: '利用率', dataIndex: 'utilization_est', render: (v) => `${(v * 100).toFixed(1)}%` },
                  ]} />
                <Collapse size="small" style={{ marginTop: 12 }}
                  items={plan.bundles.map((b, i) => ({
                    key: `${i}-${b.name}`,
                    label: `${b.name} · TCO 明细（购置/能耗/维保/残值）`,
                    children: (
                      <Table rowKey="model_code" size="small" pagination={false} dataSource={b.tco}
                        columns={[
                          { title: '型号', dataIndex: 'model_name' }, { title: '数量', dataIndex: 'count' },
                          { title: '购置(元)', dataIndex: 'purchase_cny', render: (v) => v.toLocaleString() },
                          { title: '能耗3年(元)', dataIndex: 'energy_3y_cny', render: (v) => v.toLocaleString() },
                          { title: '维保3年(元)', dataIndex: 'maintenance_3y_cny', render: (v) => v.toLocaleString() },
                          { title: '残值(元)', dataIndex: 'residual_cny', render: (v) => v.toLocaleString() },
                          { title: '合计(元)', dataIndex: 'total_3y_cny', render: (v) => v.toLocaleString() },
                        ]} />
                    ),
                  }))} />
                <Collapse size="small" style={{ marginTop: 8 }}
                  items={plan.chapters.map((ch, i) => ({ key: i, label: `${i + 1}. ${ch.chapter}`, children: ch.paragraphs.map((p, j) => <Typography.Paragraph key={j} style={{ marginBottom: 4 }}>· {p}</Typography.Paragraph>) }))} />
                {plan.citations?.length ? (
                  <Typography.Paragraph type="secondary" style={{ marginTop: 8, fontSize: 12 }}>
                    引用来源：{plan.citations.map((c, i) => <Tag key={i}>{c.title}</Tag>)}
                  </Typography.Paragraph>
                ) : null}
                <Alert type="info" showIcon message="AI 生成初稿，需人工确认 · 数字均来自设备参数库/知识库直读（示例参数，正式方案请厂商确认）" />
              </Card>
            </Spin>
          )}
        </Col>
      </Row>
    </div>
  );
}

function EmptyHint() {
  return (
    <Card style={{ textAlign: 'center', paddingTop: 80 }}>
      <Typography.Title level={4}>方案工作台</Typography.Title>
      <Typography.Paragraph type="secondary">
        左侧输入需求 → 一键生成 ≥3 套选型方案（Top3 对比 / 三年 TCO 四大类 / 章节化施工组织 / 投标响应度检查），
        支持 Word / PDF 导出（依据 PRD 功能 1.1 / 1.2）。
      </Typography.Paragraph>
    </Card>
  );
}
