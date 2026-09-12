"""语料模型在线推理服务（data/models/corpus，机理分组多检测器）。

与训练脚本 scripts/train_models_corpus.py 的特征定义严格一致：
  设备稳健基线（中位数/MAD）→ z 分数 → 1h/6h 均值 · 6h 漂移 · 1h 波动
判定：逐部件检测器概率 ≥ 该检测器标定阈值（阈值 = 健康设备误报预算 2% 对应值）
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache

import numpy as np
import pandas as pd

from backend.app.core.config import settings

logger = logging.getLogger("icops.predictive_corpus")

CORPUS_DIR = settings.repo_root / "data" / "models" / "corpus"
SIM_DIR = settings.repo_root / "data" / "simulated"
SHORT_W, MID_W = 12, 72
EPS = 1e-6
CLASS_CN = {
    "HYD": "液压系统",
    "ENG": "发动机",
    "ELE": "电气系统",
    "DRV": "传动/行走",
    "STR": "结构件",
    "BRK": "制动系统",
    "BAT": "动力电池",
}


def artifacts_ready() -> bool:
    return (CORPUS_DIR / "detectors.joblib").exists() and (CORPUS_DIR / "meta.json").exists()


@lru_cache(maxsize=1)
def _meta() -> dict:
    f = CORPUS_DIR / "meta.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


@lru_cache(maxsize=1)
def _detectors():
    import joblib

    return joblib.load(CORPUS_DIR / "detectors.joblib")


def corpus_devices() -> list[dict]:
    """语料站点设备清单（来自 corpus_meta.json）。"""
    f = SIM_DIR / "corpus_meta.json"
    if not f.exists():
        return []
    meta = json.loads(f.read_text(encoding="utf-8"))
    out = []
    for d in meta.get("devices", []):
        out.append(
            {
                "device_id": d.get("device_id"),
                "model": d.get("model"),
                "category": d.get("category"),
                "role": d.get("role"),
                "power_kw": d.get("power_kw"),
                "mass_kg": d.get("mass_kg"),
                "bucket_m3": d.get("bucket_m3"),
            }
        )
    return out


def corpus_faults() -> list[dict]:
    f = SIM_DIR / "corpus_faults.csv"
    if not f.exists():
        return []
    return pd.read_csv(f).to_dict(orient="records")


def _build_features(dev_df: pd.DataFrame, baselines: dict) -> pd.DataFrame:
    """按训练同口径构造特征（z / 1h·6h 均值 / 6h 漂移 / 1h 波动）。"""
    g = dev_df.sort_values("ts").reset_index(drop=True)
    feat = pd.DataFrame(index=g.index)
    for ch, base in baselines.items():
        if ch not in g.columns:
            continue
        med, mad = float(base["median"]), float(base["mad"]) + EPS
        z = (g[ch] - med) / mad
        feat[f"{ch}_z"] = z
        feat[f"{ch}_z1h"] = z.rolling(SHORT_W, min_periods=1).mean()
        feat[f"{ch}_z6h"] = z.rolling(MID_W, min_periods=1).mean()
        feat[f"{ch}_drift6h"] = z - z.shift(MID_W)
        feat[f"{ch}_std1h"] = z.rolling(SHORT_W, min_periods=2).std().fillna(0.0)
    return feat.fillna(0.0)


def predict_corpus_device(device_id: str, at_ts: str | None = None) -> dict:
    """返回该语料设备在指定时刻（默认序列末尾）的部件风险评分。"""
    if not artifacts_ready():
        return {
            "device_id": device_id,
            "note": "语料模型未训练：请执行 uv run python -m scripts.train_models_corpus",
            "risky": False,
            "classes": [],
        }
    tele_path = SIM_DIR / "corpus_telemetry.csv"
    if not tele_path.exists():
        return {
            "device_id": device_id,
            "note": "缺少语料遥测数据（corpus_telemetry.csv）",
            "risky": False,
            "classes": [],
        }
    meta = _meta()
    baselines = (meta.get("device_baselines") or {}).get(device_id)
    if not baselines:
        return {
            "device_id": device_id,
            "note": f"未找到设备 {device_id} 的基线（可选设备见 /api/maintenance/corpus/devices）",
            "risky": False,
            "classes": [],
        }

    df = pd.read_csv(tele_path, usecols=None)
    df = df[df["device_id"] == device_id]
    if df.empty:
        return {"device_id": device_id, "note": "该设备无遥测记录", "risky": False, "classes": []}
    df["ts"] = pd.to_datetime(df["ts"])
    if at_ts:
        df = df[df["ts"] <= pd.to_datetime(at_ts)]
    if df.empty:
        return {"device_id": device_id, "note": "指定时刻之前无数据", "risky": False, "classes": []}

    feats = _build_features(df, baselines)
    row_feat = feats.iloc[[-1]]
    detectors = _detectors()
    thresholds: dict = meta.get("operating_thresholds", {})
    feature_cols: dict = meta.get("class_feature_cols", {})

    classes: list[dict] = []
    for cls, model in detectors.items():
        cols = [c for c in feature_cols.get(cls, []) if c in row_feat.columns]
        if not cols:
            continue
        x = row_feat[cols].to_numpy(dtype=np.float32)
        proba = float(model.predict_proba(x)[0, 1])
        thr = float(thresholds.get(cls, 0.5))
        classes.append(
            {
                "class": cls,
                "class_cn": CLASS_CN.get(cls, cls),
                "proba": round(proba, 6),
                "threshold": round(thr, 6),
                "exceed": proba >= thr,
                "score": round(proba / max(thr, EPS), 3),
            }
        )
    classes.sort(key=lambda c: -c["score"])
    risky = any(c["exceed"] for c in classes)
    top = classes[0] if classes else None
    return {
        "device_id": device_id,
        "at": str(df["ts"].iloc[-1]),
        "risky": risky,
        "top_class": top["class"] if top else "",
        "top_class_cn": top["class_cn"] if top else "",
        "top_conf": top["proba"] if top else 0.0,
        "classes": classes,
        "remaining_hours": None,
        "note": (
            "检测到部件异常征兆；剩余可用时间需结合工况与维修计划人工确认（模型输出为风险等级，非精确 RUL）。"
            if risky
            else "各部件检测器均未超阈值（健康）"
        ),
        "model_note": "语料模型（仿真数据）：P1 时序留出检出 10/10、平均提前量 24.3h、部件 Top1 90%、误报 2%",
    }
