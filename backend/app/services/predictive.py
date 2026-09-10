"""预测性维护（指导书 6.6）：在线推理服务（读取 data/models 下训练产物）。

训练脚本：uv run python -m scripts.train_models
模型：IsolationForest（无监督异常监测）+ XGBoost（故障分类，基于滑动窗口统计特征）。
产物：data/models/{isoforest.joblib, xgb.joblib, meta.json, eval_report.json}
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache

import numpy as np
import pandas as pd

from backend.app.core.config import settings

logger = logging.getLogger("icops.predictive")

MODEL_DIR = lambda: settings.repo_root / "data" / "models"  # noqa: E731
TELEMETRY_CSV = lambda: settings.repo_root / "data" / "simulated" / "telemetry.csv"  # noqa: E731
WINDOW = 6  # 6h 滑动窗口

FEATURE_COLS = ["engine_temp_c", "hyd_oil_temp_c", "vib_mm_s", "rpm", "fuel_rate_lh", "oil_pressure_bar"]
THRESHOLD_BY_CODE = {"ENG-03": ("engine_temp_c", 102.0), "HYD-01": ("hyd_oil_temp_c", 95.0)}


@lru_cache(maxsize=1)
def _load_meta() -> dict:
    f = MODEL_DIR() / "meta.json"
    if not f.exists():
        return {}
    return json.loads(f.read_text(encoding="utf-8"))


def models_ready() -> bool:
    return (MODEL_DIR() / "isoforest.joblib").exists() and (MODEL_DIR() / "xgb.joblib").exists()


def _load_models():
    import joblib

    iso = joblib.load(MODEL_DIR() / "isoforest.joblib")
    xgb = joblib.load(MODEL_DIR() / "xgb.joblib")
    return iso, xgb


def device_telemetry(device_code: str) -> pd.DataFrame:
    """读取该设备时序（离线演示数据），按时间升序。"""
    csv_path = TELEMETRY_CSV()
    if not csv_path.exists():
        return pd.DataFrame()
    df = pd.read_csv(csv_path)
    df = df[df["device_code"] == device_code].sort_values("ts").reset_index(drop=True)
    return df


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """滑动窗口统计特征：均值/标准差/斜率（指导书 6.6 特征工程）。"""
    if df.empty:
        return df
    base = df.copy()
    for col in FEATURE_COLS:
        base[f"{col}_mean"] = df[col].rolling(WINDOW, min_periods=WINDOW).mean()
        base[f"{col}_std"] = df[col].rolling(WINDOW, min_periods=WINDOW).std().fillna(0)
        base[f"{col}_slope"] = df[col].diff(WINDOW - 1) / max(WINDOW - 1, 1)
    base["load_t_mean"] = df["load_t"].rolling(WINDOW, min_periods=WINDOW).mean()
    return base


def feature_matrix(df: pd.DataFrame) -> np.ndarray:
    cols = []
    for col in FEATURE_COLS:
        cols += [f"{col}_mean", f"{col}_std", f"{col}_slope"]
    cols.append("load_t_mean")
    return df[cols].fillna(0).to_numpy(dtype=np.float32)


def _class_map() -> dict:
    meta = _load_meta()
    return meta.get("class_map", {})


def predict_device(db, device_code: str) -> dict:
    """输出：异常分 / 是否风险 / 最可能故障码与置信度 / 预计剩余可用时间（RUL）。"""
    if not models_ready():
        return {
            "device_code": device_code,
            "note": "预测模型未训练，请先执行 uv run python -m scripts.train_models",
            "risky": False,
            "anomaly_score": 0.0,
        }
    df = device_telemetry(device_code)
    if df.empty:
        return {"device_code": device_code, "note": "无该设备时序数据", "risky": False, "anomaly_score": 0.0}
    feats = build_features(df)
    if len(feats) < WINDOW:
        return {"device_code": device_code, "note": "数据不足一个窗口", "risky": False, "anomaly_score": 0.0}
    X = feature_matrix(feats.iloc[[-1]])
    iso, xgb = _load_models()
    anomaly = float(iso.decision_function(X)[0])  # 越高越正常
    proba = xgb.predict_proba(X)[0]
    inv = {v: k for k, v in _class_map().items()}
    best = int(np.argmax(proba))
    best_code = inv.get(best, "NONE")
    conf = float(proba[best])
    threshold = float(_load_meta().get("operating_threshold", 0.5))
    risky = best_code != "NONE" and conf >= threshold and anomaly < 0.0

    remaining = None
    if risky and best_code in THRESHOLD_BY_CODE:
        ch, thr = THRESHOLD_BY_CODE[best_code]
        cur = float(feats.iloc[-1][f"{ch}_mean"])
        slope = float(feats.iloc[-1].get(f"{ch}_slope", 0.0))
        if cur >= thr:
            remaining = 0.0  # 已超阈值：需立即处置
        elif slope > 0.05:
            remaining = max(0.0, round((thr - cur) / slope, 1))
        else:
            remaining = 24.0
    return {
        "device_code": device_code,
        "anomaly_score": round(anomaly, 4),
        "risky": risky,
        "top_code": best_code if risky else "",
        "top_conf": round(conf, 3) if risky else 0.0,
        "remaining_hours": remaining,
        "note": "模拟数据模型；真实设备迁移列入 V1.1 试点",
    }
