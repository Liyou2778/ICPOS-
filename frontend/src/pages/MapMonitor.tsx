import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Badge, Button, Card, Col, List, Row, Segmented, Space, Spin, Tag, Typography } from 'antd';
import { PlayCircleOutlined, ReloadOutlined } from '@ant-design/icons';
import { api } from '../api';
import type { DeviceItem, FrontendConfig } from '../api/types';
import EChart from '../components/EChart';
import AmapView from '../components/AmapView';
import type { EChartsOption } from 'echarts';

// 站点锚点与“电子围栏”示意（与模拟数据生成器一致）
const PIT = { name: '采场装载点 P', lat: 39.612, lng: 109.781 };
const DUMP = { name: '排土场卸点 D', lat: 39.637, lng: 109.82 };
const LON_PAD = 0.02;
const LAT_PAD = 0.012;
const STATE_COLOR: Record<string, string> = { working: '#52c41a', idle: '#faad14', fault: '#ff4d4f', maintenance: '#1677ff' };
const STATE_CN: Record<string, string> = { working: '作业中', idle: '待命', fault: '故障', maintenance: '维保' };

export default function MapMonitor() {
  const [devices, setDevices] = useState<DeviceItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');
  const [selected, setSelected] = useState<DeviceItem | null>(null);
  const [track, setTrack] = useState<{ lat: number; lng: number }[]>([]);
  const [playing, setPlaying] = useState(false);
  const [amap, setAmap] = useState<FrontendConfig | null>(null);
  const [mode, setMode] = useState<'auto' | 'offline'>('auto');
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => { api.frontendConfig().then(setAmap).catch(() => undefined); }, []);

  const load = useCallback(async () => {
    try {
      const list = await api.devices();
      setDevices(list);
      setErr('');
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const wsProto = window.location.protocol === 'https:' ? 'wss' : 'ws';
    const connect = () => {
      const ws = new WebSocket(`${wsProto}://${window.location.host}/ws/telemetry`);
      wsRef.current = ws;
      ws.onmessage = (ev) => {
        try {
          const snap = JSON.parse(ev.data as string);
          if (snap.devices?.length) setDevices((prev) => {
            const map = new Map(prev.map((d) => [d.code, d]));
            for (const d of snap.devices) map.set(d.code, { ...map.get(d.code), ...d } as DeviceItem);
            return [...map.values()];
          });
        } catch { /* ignore */ }
      };
      ws.onclose = () => setTimeout(connect, 5000);
    };
    connect();
    return () => wsRef.current?.close();
  }, [load]);

  const replay = async (d: DeviceItem) => {
    setSelected(d);
    setPlaying(true);
    try {
      const res = await api.trajectory(d.code, 120);
      setTrack(res.points.map((p) => ({ lat: p.lat, lng: p.lng })));
    } catch (e) {
      setErr((e as Error).message);
      setTrack([]);
    } finally {
      setPlaying(false);
    }
  };

  const option = useMemo<EChartsOption>(() => {
    const xs = [PIT.lng, DUMP.lng, ...devices.map((d) => d.lng)];
    const ys = [PIT.lat, DUMP.lat, ...devices.map((d) => d.lat)];
    const minX = Math.min(...xs) - LON_PAD;
    const maxX = Math.max(...xs) + LON_PAD;
    const minY = Math.min(...ys) - LAT_PAD;
    const maxY = Math.max(...ys) + LAT_PAD;
    return {
      tooltip: { trigger: 'item' },
      grid: { left: 0, right: 0, top: 10, bottom: 10 },
      xAxis: { type: 'value', min: minX, max: maxX, splitLine: { show: false }, axisLabel: { show: false } },
      yAxis: { type: 'value', min: minY, max: maxY, inverse: true, splitLine: { show: false }, axisLabel: { show: false } },
      graphic: [
        { type: 'text', left: '3%', top: '3%', style: { text: '露天矿作业区 · 电子围栏示意', fontSize: 13, fill: '#595959' } },
      ],
      series: [
        { // 电子围栏区域（示意）
          name: '电子围栏', type: 'custom',
          renderItem: () => ({ type: 'rect', shape: { x: 0, y: 0, width: 100, height: 100 }, style: { fill: 'rgba(22,119,255,0.05)', stroke: '#69b1ff', lineDash: [6, 4] } }),
          data: [0],
        },
        { // 采场->排土场路线
          name: '运输路线', type: 'lines', coordinateSystem: 'cartesian2d',
          data: [{ coords: [[PIT.lng, PIT.lat], [DUMP.lng, DUMP.lat]] }],
          lineStyle: { color: '#91caff', width: 3, type: 'dashed' },
          effect: { show: true, symbol: 'arrow', symbolSize: 8, color: '#1677ff' },
        },
        { // 轨迹回放线
          name: '轨迹', type: 'line', data: track.map((p) => [p.lng, p.lat]),
          lineStyle: { color: '#722ed1', width: 3 }, showSymbol: false,
        },
        { // 设备散点
          name: '设备', type: 'effectScatter', coordinateSystem: 'cartesian2d',
          data: devices.map((d) => ({
            value: [d.lng, d.lat], name: `${d.code} ${STATE_CN[d.work_state] ?? d.work_state}`,
            itemStyle: { color: STATE_COLOR[d.work_state] ?? '#bfbfbf' },
          })),
          symbolSize: 14,
          rippleEffect: { brushType: 'stroke', scale: 3 },
          label: { show: true, position: 'right', formatter: '{b}', fontSize: 11 },
          emphasis: { scale: 1.4 },
        },
        { // 站点
          name: '站点', type: 'scatter', coordinateSystem: 'cartesian2d',
          data: [{ value: [PIT.lng, PIT.lat], name: PIT.name, itemStyle: { color: '#237804' }, symbolSize: 20 },
                 { value: [DUMP.lng, DUMP.lat], name: DUMP.name, itemStyle: { color: '#ad2102' }, symbolSize: 20 }],
          label: { show: true, position: 'bottom', formatter: '{b}', fontSize: 12, color: '#333' },
        },
      ],
    };
  }, [devices, track]);

  return (
    <div className="page-container">
      <Card title="设备地图监控 · 实时位置 / 轨迹回放 / 电子围栏" extra={<Space>
        <Tag color="green">WebSocket 每 5 秒推送</Tag>
        <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
      </Space>}>
        {err && <Tag color="red">{err}</Tag>}
        {amap?.amap_enabled && (
          <Segmented value={mode} onChange={(v) => setMode(v as 'auto' | 'offline')}
            options={[{ label: '高德真实地图', value: 'auto' }, { label: '离线自绘', value: 'offline' }]}
            style={{ marginBottom: 8 }} />
        )}
        <Spin spinning={loading}>
          {amap?.amap_enabled && mode === 'auto'
            ? <AmapView devices={devices} track={track} key={amap.amap_key} sec={amap.amap_security_code} />
            : <EChart option={option} height={520} />}
        </Spin>
        <Row gutter={[12, 12]} style={{ marginTop: 12 }}>
          <Col xs={24} md={8}>
            <Card size="small" title="设备清单（点击查看轨迹）" styles={{ body: { padding: 4 } }}>
              <List size="small" dataSource={devices} loading={loading}
                renderItem={(d) => (
                  <List.Item style={{ cursor: 'pointer', paddingInline: 8 }} onClick={() => replay(d)}>
                    <Space>
                      <Badge color={STATE_COLOR[d.work_state] ?? '#bbb'} />
                      <Typography.Text strong>{d.code}</Typography.Text>
                      <Typography.Text type="secondary">{d.name}</Typography.Text>
                      <Tag>{STATE_CN[d.work_state] ?? d.work_state}</Tag>
                    </Space>
                  </List.Item>
                )} />
            </Card>
          </Col>
          <Col xs={24} md={8}>
            <Card size="small" title={`轨迹回放：${selected?.code ?? '请选择设备'}`}>
              {selected ? (
                playing ? <Spin tip="加载轨迹…"><div style={{ height: 40 }} /></Spin>
                  : <Space direction="vertical">
                    <Typography.Text>轨迹点数：{track.length}（含位置/速度/载荷）</Typography.Text>
                    <Button icon={<PlayCircleOutlined />} onClick={() => replay(selected)}>重新回放</Button>
                  </Space>
              ) : <Typography.Text type="secondary">点击左侧设备查看最近轨迹</Typography.Text>}
            </Card>
          </Col>
          <Col xs={24} md={8}>
            <Card size="small" title="围栏与告警口径">
              <Typography.Paragraph type="secondary" style={{ marginBottom: 4 }}>
                • 蓝框为矿区作业电子围栏（示意）；越界将触发告警（模拟事件由后端判定）。<br />
                • 绿=作业中 / 黄=待命 / 红=故障 / 蓝=维保。<br />
                • 在 `.env` 填 AMAP_KEY + AMAP_SECURITY_CODE 后，本页自动出现“高德真实地图/离线自绘”切换。
              </Typography.Paragraph>
            </Card>
          </Col>
        </Row>
      </Card>
    </div>
  );
}
