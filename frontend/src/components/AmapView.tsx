import { useEffect, useRef, useState } from 'react';
import { Button, Spin } from 'antd';
import type { DeviceItem } from '../api/types';

const PIT = { name: '采场装载点 P', lat: 39.612, lng: 109.781 };
const DUMP = { name: '排土场卸点 D', lat: 39.637, lng: 109.82 };
const COLOR: Record<string, string> = { working: 'green', idle: 'orange', fault: 'red', maintenance: 'blue' };

declare global {
  interface Window {
    AMap: any;
    _AMapSecurityConfig?: { securityJsCode: string };
  }
}

let _scriptPromise: Promise<boolean> | null = null;
function loadAmap(key: string, sec: string): Promise<boolean> {
  if (window.AMap) return Promise.resolve(true);
  if (!key) return Promise.resolve(false);
  if (!_scriptPromise) {
    _scriptPromise = new Promise((resolve) => {
      if (sec) window._AMapSecurityConfig = { securityJsCode: sec };
      const s = document.createElement('script');
      s.src = `https://webapi.amap.com/maps?v=2.0&key=${key}`;
      s.onload = () => resolve(true);
      s.onerror = () => { _scriptPromise = null; resolve(false); }; // 失败不缓存，允许重试
      document.head.appendChild(s);
    });
  }
  return _scriptPromise;
}

interface Props {
  devices: DeviceItem[];
  track: { lat: number; lng: number }[];
  key: string;
  sec: string;
}

export default function AmapView({ devices, track, key, sec }: Props) {
  const boxRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<any>(null);
  const markersRef = useRef<any[]>([]);
  const routeRef = useRef<any>(null);
  const trackRef = useRef<any>(null);
  const [ready, setReady] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let alive = true;
    setReady(false);
    loadAmap(key, sec).then((ok) => {
      if (!alive) return;
      if (!ok) {
        setErr('高德脚本加载失败（浏览器端被拦截）。请检查该页 DevTools→Network 里 amap 请求的错误；尝试：① 换 Chrome/Edge，关闭广告/隐私拦截插件；② 若在公司网络，改用手机热点；③ 地址栏用 http://127.0.0.1:8000。');
        setReady(false);
        return;
      }
      setErr(null);
      setReady(true);
    });
    return () => { alive = false; };
  }, [key, sec, tick]);

  // 初始化地图
  useEffect(() => {
    if (!ready || !boxRef.current || mapRef.current) return;
    try {
      const A = window.AMap;
      const map = new A.Map(boxRef.current, { zoom: 14, center: [PIT.lng, PIT.lat] });
      mapRef.current = map;
      const route = new A.Polyline({
        path: [[PIT.lng, PIT.lat], [DUMP.lng, DUMP.lat]],
        strokeColor: '#1677ff', strokeWeight: 4, strokeStyle: 'dashed', showDir: true,
      });
      map.add(route);
      routeRef.current = route;
      // 电子围栏（示意矩形）
      map.add(new A.Polygon({
        path: [[PIT.lng - 0.03, PIT.lat - 0.015], [DUMP.lng + 0.03, PIT.lat - 0.015],
               [DUMP.lng + 0.03, DUMP.lat + 0.015], [PIT.lng - 0.03, DUMP.lat + 0.015]],
        strokeColor: '#69b1ff', strokeWeight: 2, fillColor: '#1677ff', fillOpacity: 0.06,
      }));
      map.add(new A.Marker({ position: [PIT.lng, PIT.lat], label: { content: PIT.name, direction: 'bottom', offset: [0, 4] } }));
      map.add(new A.Marker({ position: [DUMP.lng, DUMP.lat], label: { content: DUMP.name, direction: 'bottom', offset: [0, 4] } }));
      return () => { map.destroy(); mapRef.current = null; };
    } catch (e) {
      setErr(`高德地图初始化失败：${(e as Error)?.message ?? String(e)}（多因 Key 未开启 Web端(JS API) 或 白名单未放行）`);
      setReady(false);
      return undefined;
    }
  }, [ready]);

  // 设备标记刷新
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    markersRef.current.forEach((m) => map.remove(m));
    markersRef.current = devices.map((d) => {
      const c = COLOR[d.work_state] ?? 'gray';
      const marker = new window.AMap.Marker({
        position: [d.lng, d.lat],
        content: `<div style="width:14px;height:14px;border-radius:50%;background:${c};border:2px solid #fff;box-shadow:0 0 4px rgba(0,0,0,.4)"></div>`,
        title: `${d.code} ${d.name}`,
        offset: new window.AMap.Pixel(-7, -7),
        label: { content: d.code, direction: 'right', offset: [6, -6], fontSize: 11 },
      });
      marker.on('click', () => map.setZoomAndCenter(Math.max(map.getZoom(), 16), [d.lng, d.lat]));
      map.add(marker);
      return marker;
    });
  }, [devices, ready]);

  // 轨迹回放
  useEffect(() => {
    const map = mapRef.current;
    if (!map || ready === false) return;
    if (!trackRef.current) {
      trackRef.current = new window.AMap.Polyline({ strokeColor: '#722ed1', strokeWeight: 5 });
      map.add(trackRef.current);
    }
    if (track.length > 1) {
      trackRef.current.setPath(track.map((p) => [p.lng, p.lat]));
      trackRef.current.show();
      map.setFitView([trackRef.current]);
    } else {
      trackRef.current.hide();
    }
  }, [track, ready]);

  if (err) {
    return (
      <div style={{ height: 520, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 10 }}>
        <pre style={{ color: '#cf1322', margin: 0, whiteSpace: 'pre-wrap', maxWidth: 600 }}>{err}</pre>
        <Button size="small" onClick={() => { setErr(null); setTick((t) => t + 1); }}>重试加载高德</Button>
      </div>
    );
  }
  return <Spin spinning={!ready} tip="正在加载高德地图…">{<div ref={boxRef} style={{ width: '100%', height: 520 }} />}</Spin>;
}
