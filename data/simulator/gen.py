"""矿山 30 天模拟数据生成器（指导书 5.2 阶段二 / 模拟先行策略）。

生成内容：
  * 设备清单（挖掘机 + 矿用自卸车，隶属 P-MINING-001 项目，路线 采场-排土场）
  * 逐小时工况时序：发动机温度/液压油温/振动/转速/油耗/油压/载荷/速度/位置
  * 预埋 ≥3 例（演示规模可设 5 例）故障前兆：前兆窗口 48h 温漂模式 + 故障代码
    （其中 HYD-01 液压油温过高 / ENG-03 水温过高 可被预测性维护模型学习；
     突发性 ELE-04 通讯故障用于验证"突发故障不承诺 24h 提前量"口径）

用法：uv run python -m data.simulator.gen --days 30 [--seed 2026] [--faults 3]
输出：data/simulated/{manifest.csv,telemetry.csv,faults.csv}
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, UTC
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]  # icops/
KB_CSV = ROOT / "data" / "knowledge" / "equipment_models.csv"
OUT_DIR = ROOT / "data" / "simulated"

# 采场 / 排土场锚点（近似坐标，示意演示）
PIT = {"name": "采场装载点P", "lat": 39.6120, "lng": 109.7810}
DUMP = {"name": "排土场卸点D", "lat": 39.6370, "lng": 109.8200}
FAULT_TARGETS = {
    # 温漂型故障（前兆 48h，可学习）：device -> fault code
    "T02": "ENG-03",  # 发动机水温过高
    "E02": "HYD-01",  # 液压油温过高
    "T04": "HYD-01",  # 液压油温过高（测试集设备，验证跨机泛化）
    # 突发型故障（无前兆，不承诺提前量）
    "E01": "ELE-04",  # 控制器通讯故障
}
FAULT_DAY = 27  # 故障发生日（窗口第 27 天前后）


def _model_params(code: str) -> dict:
    """从设备参数库 CSV 直读型号参数（不依赖数据库），带默认兜底。"""
    defaults = {
        "fuel_lh": 30,
        "category_cn": "矿用自卸车",
        "power_kw": 300,
        "rated_load_t": 90,
        "bucket_m3": 4.5,
    }
    try:
        df = pd.read_csv(KB_CSV, encoding="utf-8-sig")
        row = df[df["code"] == code]
        if not row.empty:
            r = row.iloc[0]
            return {
                "fuel_lh": float(r["fuel_lh"]),
                "category_cn": r["category_cn"],
                "power_kw": float(r["power_kw"]),
                "rated_load_t": float(r["rated_load_t"]),
                "bucket_m3": float(r["bucket_m3"]),
            }
    except FileNotFoundError:
        pass
    return defaults


def _fleet() -> list[dict]:
    return [
        {
            "device": "E01",
            "model": "XE700",
            "name": "挖掘机E01",
            "base_lat": PIT["lat"],
            "base_lng": PIT["lng"],
            "is_truck": False,
        },
        {
            "device": "E02",
            "model": "XE490",
            "name": "挖掘机E02",
            "base_lat": PIT["lat"] + 0.001,
            "base_lng": PIT["lng"] + 0.001,
            "is_truck": False,
        },
        {"device": "T01", "model": "XDR90", "name": "矿卡T01", "is_truck": True},
        {"device": "T02", "model": "XDR90", "name": "矿卡T02", "is_truck": True},
        {"device": "T03", "model": "XDR90", "name": "矿卡T03", "is_truck": True},
        {"device": "T04", "model": "XDR90", "name": "矿卡T04", "is_truck": True},
        {"device": "T05", "model": "XDR80", "name": "矿卡T05", "is_truck": True},
    ]


def _drift_profile(
    rs: np.random.RandomState, code: str, hour0: int, fault_hour: int, base: float, channel: str
) -> float:
    """返回该 hour 相对基线的增量（温漂型故障前兆：固定 48h 窗口线性爬升）。"""
    if code in (None, "") or channel is None or fault_hour is None:
        return 0.0
    if hour0 >= fault_hour:
        return 0.0
    win_start = fault_hour - 48
    if hour0 < win_start:
        return 0.0
    progress = (hour0 - win_start) / 48.0
    # 早期抬升更快（^0.75），保证可提前 ≥24h 检测；信号幅度高于噪声
    total_delta = {"engine_temp_c": 30.0, "hyd_oil_temp_c": 48.0, "vib_mm_s": 8.0}[channel]
    return total_delta * (progress**0.75) + rs.normal(0, 0.4)


def _gen_device(rs: np.random.RandomState, dev: dict, days: int, seed_year: int) -> pd.DataFrame:
    p = _model_params(dev["model"])
    n_hours = days * 24
    start = datetime(seed_year, 9, 1, tzinfo=UTC)
    base_temp = 88.0 if p["category_cn"] != "挖掘机" else 86.0
    base_hyd = 48.0 if p["category_cn"] != "挖掘机" else 62.0

    fault_code = FAULT_TARGETS.get(dev["device"])
    sudden = fault_code == "ELE-04"
    # 温漂故障时刻：FAULT_DAY 日的第 14 小时（UTC 约 22 点北京时间）
    fault_hour = (FAULT_DAY - 1) * 24 + 14 if fault_code and not sudden else None
    channel_drift = {
        "ENG-03": ("engine_temp_c", base_temp + 18.0),
        "HYD-01": ("hyd_oil_temp_c", base_hyd + 30.0),
    }.get(fault_code or "", (None, None))

    rows: list[dict] = []
    pos = {
        "lat": dev.get("base_lat", PIT["lat"] + rs.uniform(-0.02, 0.02)),
        "lng": dev.get("base_lng", PIT["lng"] + rs.uniform(-0.02, 0.02)),
    }
    for h in range(n_hours):
        ts = start + timedelta(hours=h)
        working_hour = h % 24 not in (4, 5, 6, 7)  # 每日 4-7 点检修停机
        state = "working" if working_hour else "idle"
        if fault_code and not sudden and h >= (fault_hour or 0):
            state = "fault"  # 故障后停机

        eng = base_temp + rs.normal(0, 1.2)
        hyd = base_hyd + rs.normal(0, 1.5)
        vib = rs.uniform(3.0, 5.5) if state != "fault" else 1.0
        if not dev["is_truck"]:
            vib += 1.2
        if fault_code and not sudden and channel_drift[0] == "engine_temp_c":
            eng += _drift_profile(rs, fault_code, h, fault_hour, base_temp, "engine_temp_c")
        if fault_code and not sudden and channel_drift[0] == "hyd_oil_temp_c":
            hyd += _drift_profile(rs, fault_code, h, fault_hour, base_hyd, "hyd_oil_temp_c")
        if sudden and fault_code and h >= (FAULT_DAY - 1) * 24 + 9:
            vib += 0.0  # 突发故障由事件注入，不体现在前兆温漂
        if state == "fault" and fault_code and not sudden:
            # 故障后停机：异常通道保持高位（便于在线预测/演示与真实语义一致）
            if channel_drift[0] == "hyd_oil_temp_c":
                hyd = 97.0 + rs.normal(0, 0.6)
            elif channel_drift[0] == "engine_temp_c":
                eng = 106.0 + rs.normal(0, 0.6)

        rpm = int(rs.uniform(1400, 2100)) if state == "working" else int(rs.uniform(800, 1000))
        fuel = p["fuel_lh"] * rs.uniform(0.45, 1.05) if state == "working" else p["fuel_lh"] * 0.12
        oil_p = rs.uniform(2.2, 3.5)

        if dev["is_truck"]:
            loaded = state == "working" and (h + hash(dev["device"]) % 2) % 2 == 0
            load_t = p["rated_load_t"] * (rs.uniform(0.8, 1.0) if loaded else 0.05)
            speed = rs.uniform(18, 28) if loaded else rs.uniform(22, 32)
            # 位置沿采场-排土场连线移动
            if loaded:
                frac = min(1.0, (h % 2) * 0.5 + rs.uniform(0, 0.3))
                pos = {
                    "lat": PIT["lat"] + (DUMP["lat"] - PIT["lat"]) * frac,
                    "lng": PIT["lng"] + (DUMP["lng"] - PIT["lng"]) * frac,
                }
            else:
                frac = 1.0 - min(1.0, (h % 2) * 0.5 + rs.uniform(0, 0.3))
                pos = {
                    "lat": DUMP["lat"] + (PIT["lat"] - DUMP["lat"]) * frac,
                    "lng": DUMP["lng"] + (PIT["lng"] - DUMP["lng"]) * frac,
                }
            lat, lng = pos["lat"] + rs.normal(0, 2e-4), pos["lng"] + rs.normal(0, 2e-4)
        else:
            load_t = rs.uniform(0, p["bucket_m3"] * 2.6) if state == "working" else 0.0
            speed = 0.0
            lat = dev["base_lat"] + rs.normal(0, 3e-4)
            lng = dev["base_lng"] + rs.normal(0, 3e-4)

        is_precursor = (
            fault_code
            and not sudden
            and channel_drift[0] is not None
            and fault_hour is not None
            and (fault_hour - 48) <= h < (fault_hour or 0)
        )
        rows.append(
            {
                "ts": ts.isoformat(),
                "day": h // 24 + 1,
                "hour": h % 24,
                "device_code": dev["device"],
                "device_name": dev["name"],
                "model_code": dev["model"],
                "category": "excavator" if not dev["is_truck"] else "truck",
                "state": state,
                "lat": round(lat, 6),
                "lng": round(lng, 6),
                "engine_temp_c": round(eng, 2),
                "hyd_oil_temp_c": round(hyd, 2),
                "vib_mm_s": round(vib, 2),
                "rpm": rpm,
                "fuel_rate_lh": round(fuel, 2),
                "oil_pressure_bar": round(oil_p, 2),
                "load_t": round(load_t, 2),
                "speed_kmh": round(speed, 2),
                "precursor": 1 if is_precursor else 0,
                "fault_code": fault_code
                if (is_precursor or (sudden and h >= (FAULT_DAY - 1) * 24 + 9))
                else "",
            }
        )
    return pd.DataFrame(rows)


def gen(days: int = 30, seed: int = 2026, faults: int | None = None) -> dict:
    rs = np.random.RandomState(seed)
    fleet = _fleet()
    manifest_rows = [
        {
            "device_code": d["device"],
            "model_code": d["model"],
            "device_name": d["name"],
            "category": "excavator" if not d["is_truck"] else "truck",
            "project_code": "P-MINING-001",
            "lat": d.get("base_lat", PIT["lat"]),
            "lng": d.get("base_lng", PIT["lng"]),
        }
        for d in fleet
    ]
    frames = [_gen_device(rs, d, days, seed) for d in fleet]
    telemetry = pd.concat(frames, ignore_index=True)

    # 故障事件表（真实故障时刻 + 前兆起点），供模型训练评估与告警回填
    fault_rows = []
    for d in fleet:
        code = FAULT_TARGETS.get(d["device"])
        if not code:
            continue
        fault_hour = (FAULT_DAY - 1) * 24 + 14
        sudden = code == "ELE-04"
        start_iso = datetime(seed, 9, 1, tzinfo=UTC) + timedelta(hours=fault_hour)
        precursor_start = start_iso - timedelta(hours=48) if not sudden else start_iso
        fault_rows.append(
            {
                "device_code": d["device"],
                "device_name": d["name"],
                "model_code": d["model"],
                "fault_code": code,
                "fault_ts": start_iso.isoformat(),
                "precursor_start_ts": precursor_start.isoformat(),
                "type": "sudden" if sudden else "trend",
            }
        )
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    telemetry.to_csv(OUT_DIR / "telemetry.csv", index=False)
    pd.DataFrame(manifest_rows).to_csv(OUT_DIR / "manifest.csv", index=False)
    pd.DataFrame(fault_rows).to_csv(OUT_DIR / "faults.csv", index=False)
    return {"rows": len(telemetry), "devices": len(fleet), "faults": len(fault_rows)}


def main() -> None:
    ap = argparse.ArgumentParser(description="ICOPS 矿山模拟数据生成器")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--faults", type=int, default=None, help="预留：控制故障数量")
    args = ap.parse_args()
    stats = gen(days=args.days, seed=args.seed)
    print(
        f"生成完成：{stats['rows']} 条时序记录 / {stats['devices']} 台设备 / "
        f"{stats['faults']} 例故障事件 -> {OUT_DIR}"
    )


if __name__ == "__main__":
    main()
