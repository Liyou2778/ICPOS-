import { useEffect, useRef } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import type { DeviceItem } from '../api/types';

const PIT = { lat: 39.612, lng: 109.781, name: '采场装载点 P' };
const DUMP = { lat: 39.637, lng: 109.82, name: '排土场卸点 D' };
const STATE_HEX: Record<string, number> = {
  working: 0x52c41a, idle: 0xfaad14, fault: 0xff4d4f, maintenance: 0x1677ff,
};
const CENTER = { lat: (PIT.lat + DUMP.lat) / 2, lng: (PIT.lng + DUMP.lng) / 2 };
const SCALE = 100; // 经纬度 -> 场景单位（约 1 单位 = 100m）

function toScene(lat: number, lng: number): [number, number] {
  const x = (lng - CENTER.lng) * 111.32 * Math.cos((CENTER.lat * Math.PI) / 180) * SCALE * 0.01;
  const z = -(lat - CENTER.lat) * 110.54 * SCALE * 0.01;
  return [x, z];
}

function fakeHeight(x: number, z: number): number {
  return Math.sin(x * 0.35) * 0.35 + Math.cos(z * 0.28) * 0.3 + Math.sin((x + z) * 0.15) * 0.25;
}

function labelSprite(text: string, color = '#ffffff'): THREE.Sprite {
  const canvas = document.createElement('canvas');
  canvas.width = 256;
  canvas.height = 64;
  const ctx = canvas.getContext('2d')!;
  ctx.fillStyle = 'rgba(0,0,0,0.55)';
  ctx.fillRect(0, 0, 256, 64);
  ctx.font = 'bold 34px "Microsoft YaHei", sans-serif';
  ctx.fillStyle = color;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText(text, 128, 34);
  const tex = new THREE.CanvasTexture(canvas);
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true }));
  sprite.scale.set(6, 1.5, 1);
  return sprite;
}

interface Props {
  devices: DeviceItem[];
  track: { lat: number; lng: number }[];
  height?: number;
}

/** 离线真 3D 矿区：地形网格 + 采场/排土场 + 运输道路 + 电子围栏 + 设备几何体 + 立体轨迹 */
export default function Site3DView({ devices, track, height = 560 }: Props) {
  const boxRef = useRef<HTMLDivElement>(null);
  const stateRef = useRef<{
    scene: THREE.Scene; renderer: THREE.WebGLRenderer; camera: THREE.PerspectiveCamera;
    controls: OrbitControls; deviceGroup: THREE.Group; trackMesh: THREE.Mesh | null;
    raf: number; observer?: ResizeObserver;
  } | null>(null);

  // ---------- 初始化（仅一次） ----------
  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0xdfe9f5);
    scene.fog = new THREE.Fog(0xdfe9f5, 60, 160);

    const camera = new THREE.PerspectiveCamera(48, el.clientWidth / el.clientHeight, 0.1, 500);
    camera.position.set(26, 22, 30);

    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setSize(el.clientWidth, el.clientHeight);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.shadowMap.enabled = true;
    el.innerHTML = '';
    el.appendChild(renderer.domElement);

    scene.add(new THREE.HemisphereLight(0xffffff, 0x4a5d3a, 1.1));
    const sun = new THREE.DirectionalLight(0xffffff, 1.4);
    sun.position.set(20, 40, 18);
    sun.castShadow = true;
    sun.shadow.mapSize.set(1024, 1024);
    scene.add(sun);

    // ---------- 地形 ----------
    const terrainGeo = new THREE.PlaneGeometry(70, 70, 48, 48);
    const pos = terrainGeo.attributes.position as THREE.BufferAttribute;
    for (let i = 0; i < pos.count; i += 1) {
      const x = pos.getX(i);
      const y = pos.getY(i);
      pos.setZ(i, fakeHeight(x * 0.1, y * 0.1));
    }
    terrainGeo.computeVertexNormals();
    const terrain = new THREE.Mesh(
      terrainGeo,
      new THREE.MeshStandardMaterial({ color: 0x7d8f63, roughness: 0.95, metalness: 0.02 }),
    );
    terrain.rotation.x = -Math.PI / 2;
    terrain.receiveShadow = true;
    scene.add(terrain);

    // ---------- 采场（凹陷）与排土场（堆积） ----------
    const [pitX, pitZ] = toScene(PIT.lat, PIT.lng);
    const [dumpX, dumpZ] = toScene(DUMP.lat, DUMP.lng);

    const pit = new THREE.Mesh(
      new THREE.CylinderGeometry(5.2, 2.2, 1.6, 40, 1, true),
      new THREE.MeshStandardMaterial({ color: 0x6b5b45, roughness: 1, side: THREE.DoubleSide }),
    );
    pit.position.set(pitX, -0.8, pitZ);
    scene.add(pit);
    const pitFloor = new THREE.Mesh(
      new THREE.CircleGeometry(2.2, 32),
      new THREE.MeshStandardMaterial({ color: 0x554636, roughness: 1 }),
    );
    pitFloor.rotation.x = -Math.PI / 2;
    pitFloor.position.set(pitX, -1.55, pitZ);
    scene.add(pitFloor);

    const dump = new THREE.Mesh(
      new THREE.ConeGeometry(5.6, 2.6, 36),
      new THREE.MeshStandardMaterial({ color: 0x9a8b6f, roughness: 1 }),
    );
    dump.position.set(dumpX, 1.3, dumpZ);
    dump.castShadow = true;
    scene.add(dump);

    const pitLabel = labelSprite(PIT.name, '#eaffea');
    pitLabel.position.set(pitX, 5.4, pitZ);
    scene.add(pitLabel);
    const dumpLabel = labelSprite(DUMP.name, '#ffe9e0');
    dumpLabel.position.set(dumpX, 6.4, dumpZ);
    scene.add(dumpLabel);

    // ---------- 运输道路（曲线管道） ----------
    const roadCurve = new THREE.CatmullRomCurve3([
      new THREE.Vector3(pitX, -1.3, pitZ),
      new THREE.Vector3(pitX + (dumpX - pitX) * 0.3, 0.35, pitZ + (dumpZ - pitZ) * 0.25),
      new THREE.Vector3(pitX + (dumpX - pitX) * 0.7, 1.0, pitZ + (dumpZ - pitZ) * 0.75),
      new THREE.Vector3(dumpX, 2.4, dumpZ),
    ]);
    scene.add(new THREE.Mesh(
      new THREE.TubeGeometry(roadCurve, 60, 0.42, 10, false),
      new THREE.MeshStandardMaterial({ color: 0xb9a888, roughness: 0.9 }),
    ));

    // ---------- 电子围栏（虚线矩形） ----------
    const fencePts = [
      new THREE.Vector3(pitX - 9, 0.35, pitZ - 6),
      new THREE.Vector3(dumpX + 9, 0.35, pitZ - 6),
      new THREE.Vector3(dumpX + 9, 0.35, dumpZ + 6),
      new THREE.Vector3(pitX - 9, 0.35, dumpZ + 6),
      new THREE.Vector3(pitX - 9, 0.35, pitZ - 6),
    ];
    const fenceLine = new THREE.Line(
      new THREE.BufferGeometry().setFromPoints(fencePts),
      new THREE.LineDashedMaterial({ color: 0x1677ff, dashSize: 1.2, gapSize: 0.8 }),
    );
    fenceLine.computeLineDistances();
    scene.add(fenceLine);

    // ---------- 设备容器 ----------
    const deviceGroup = new THREE.Group();
    scene.add(deviceGroup);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.target.set((pitX + dumpX) / 2, 1.5, (pitZ + dumpZ) / 2);
    controls.maxPolarAngle = Math.PI / 2.1;
    controls.minDistance = 12;
    controls.maxDistance = 90;

    const clock = new THREE.Clock();
    const loop = () => {
      if (!stateRef.current) return;
      stateRef.current.raf = requestAnimationFrame(loop);
      const t = clock.getElapsedTime();
      deviceGroup.children.forEach((g, i) => {
        g.position.y = (g.userData.baseY as number) + Math.sin(t * 1.6 + i) * 0.06;
      });
      controls.update();
      renderer.render(scene, camera);
    };
    const raf = requestAnimationFrame(loop);

    const observer = new ResizeObserver(() => {
      if (!el.clientWidth) return;
      camera.aspect = el.clientWidth / el.clientHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(el.clientWidth, el.clientHeight);
    });
    observer.observe(el);

    stateRef.current = { scene, renderer, camera, controls, deviceGroup, trackMesh: null, raf, observer };

    return () => {
      const st = stateRef.current;
      if (st) {
        cancelAnimationFrame(st.raf);
        st.observer?.disconnect();
        st.controls.dispose();
        st.scene.traverse((obj) => {
          const mesh = obj as THREE.Mesh;
          mesh.geometry?.dispose?.();
          const mat = mesh.material as THREE.Material | THREE.Material[] | undefined;
          if (Array.isArray(mat)) mat.forEach((m) => m.dispose());
          else mat?.dispose?.();
        });
        st.renderer.dispose();
      }
      stateRef.current = null;
      el.innerHTML = '';
    };
  }, []);

  // ---------- 设备几何体（随数据刷新） ----------
  useEffect(() => {
    const st = stateRef.current;
    if (!st) return;
    const { deviceGroup } = st;
    deviceGroup.clear();
    const [dumpX, dumpZ] = toScene(DUMP.lat, DUMP.lng);
    devices.forEach((d) => {
      const [x, z] = toScene(d.lat, d.lng);
      const y = fakeHeight(x * 0.1, z * 0.1);
      const isTruck = /^T/.test(d.code);
      const color = STATE_HEX[d.work_state] ?? 0x8c8c8c;
      const g = new THREE.Group();
      const bodyMat = new THREE.MeshStandardMaterial({ color, roughness: 0.45, metalness: 0.25 });

      if (isTruck) {
        const bed = new THREE.Mesh(new THREE.BoxGeometry(2.6, 0.9, 1.5), bodyMat);
        bed.position.y = 0.75;
        bed.castShadow = true;
        const cab = new THREE.Mesh(new THREE.BoxGeometry(1.0, 0.9, 1.4),
          new THREE.MeshStandardMaterial({ color: 0xf0f0f0, roughness: 0.4 }));
        cab.position.set(1.7, 0.75, 0);
        cab.castShadow = true;
        const wheelGeo = new THREE.CylinderGeometry(0.36, 0.36, 0.3, 14);
        const wheelMat = new THREE.MeshStandardMaterial({ color: 0x2b2b2b, roughness: 0.9 });
        [[-0.9, 0.85], [0.9, 0.85], [-0.9, -0.85], [0.9, -0.85]].forEach(([wx, wz]) => {
          const w = new THREE.Mesh(wheelGeo, wheelMat);
          w.rotation.x = Math.PI / 2;
          w.position.set(wx, 0.36, wz);
          g.add(w);
        });
        g.add(bed, cab);
      } else {
        const body = new THREE.Mesh(new THREE.BoxGeometry(2.2, 0.9, 2.0), bodyMat);
        body.position.y = 0.85;
        body.castShadow = true;
        const cab = new THREE.Mesh(new THREE.BoxGeometry(0.9, 0.8, 1.0),
          new THREE.MeshStandardMaterial({ color: 0xf5d47a, roughness: 0.5 }));
        cab.position.set(-0.4, 1.7, 0);
        cab.castShadow = true;
        const arm = new THREE.Mesh(new THREE.BoxGeometry(3.0, 0.22, 0.3), bodyMat);
        arm.position.set(1.4, 1.5, 0);
        arm.rotation.z = -0.35;
        const bucket = new THREE.Mesh(new THREE.BoxGeometry(0.7, 0.5, 0.7),
          new THREE.MeshStandardMaterial({ color: 0x9e9e9e, metalness: 0.6, roughness: 0.5 }));
        bucket.position.set(2.9, 0.85, 0);
        const track1 = new THREE.Mesh(new THREE.BoxGeometry(2.4, 0.35, 0.5),
          new THREE.MeshStandardMaterial({ color: 0x333333, roughness: 1 }));
        track1.position.set(0, 0.2, 0.8);
        const track2 = track1.clone();
        track2.position.z = -0.8;
        g.add(body, cab, arm, bucket, track1, track2);
      }

      const tag = labelSprite(d.code, d.work_state === 'fault' ? '#ffb3b3' : '#e6fffb');
      tag.position.set(0, 3.2, 0);
      g.add(tag);

      g.position.set(x, y, z);
      g.userData.baseY = y;
      g.rotation.y = Math.atan2(dumpX - x, dumpZ - z);
      deviceGroup.add(g);
    });
  }, [devices]);

  // ---------- 立体轨迹 ----------
  useEffect(() => {
    const st = stateRef.current;
    if (!st) return;
    if (st.trackMesh) {
      st.scene.remove(st.trackMesh);
      st.trackMesh.geometry.dispose();
      (st.trackMesh.material as THREE.Material).dispose();
      st.trackMesh = null;
    }
    if (track.length < 2) return;
    const pts = track.map((p) => {
      const [x, z] = toScene(p.lat, p.lng);
      return new THREE.Vector3(x, fakeHeight(x * 0.1, z * 0.1) + 1.1, z);
    });
    const curve = new THREE.CatmullRomCurve3(pts);
    const mesh = new THREE.Mesh(
      new THREE.TubeGeometry(curve, Math.min(200, pts.length * 2), 0.18, 8, false),
      new THREE.MeshStandardMaterial({ color: 0x722ed1, emissive: 0x3b1263, roughness: 0.4 }),
    );
    st.scene.add(mesh);
    st.trackMesh = mesh;
  }, [track]);

  return (
    <div style={{ position: 'relative' }}>
      <div ref={boxRef} style={{ width: '100%', height, borderRadius: 8, overflow: 'hidden', background: '#dfe9f5' }} />
      <div style={{ position: 'absolute', left: 10, top: 10, background: 'rgba(255,255,255,.88)', padding: '6px 10px', borderRadius: 6, fontSize: 12 }}>
        离线 3D 矿区（Three.js）· 鼠标左键旋转 / 右键平移 / 滚轮缩放
        <div style={{ marginTop: 4 }}>
          <span style={{ color: '#52c41a' }}>● 作业中</span>{'  '}
          <span style={{ color: '#faad14' }}>● 待命</span>{'  '}
          <span style={{ color: '#ff4d4f' }}>● 故障</span>{'  '}
          <span style={{ color: '#1677ff' }}>● 维保</span>{'  '}
          <span style={{ color: '#722ed1' }}>━ 回放轨迹</span>
        </div>
      </div>
    </div>
  );
}
