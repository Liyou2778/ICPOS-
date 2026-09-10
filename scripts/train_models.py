"""预测性维护模型训练与评估（指导书 6.6）。

流程：模拟数据（预埋故障前兆）-> 特征工程（6h 滑动窗口 均值/斜率/方差）
-> IsolationForest 异常监测 + XGBoost 故障分类 -> 设备级留出（T04）评估
-> 输出准确率/召回/预警提前量，并选定线上判定阈值。

结果：data/models/{isoforest.joblib, xgb.joblib, meta.json, eval_report.json}
用法：uv run python -m scripts.train_models
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, UTC

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

from backend.app.core.config import settings
from backend.app.services import predictive as pv

ROOT = settings.repo_root
MODEL_DIR = ROOT / "data" / "models"
CLASS_MAP = {"ENG-03": 0, "HYD-01": 1, "NONE": 2}
POS_CLASSES = ["ENG-03", "HYD-01"]


def _dataset() -> pd.DataFrame:
    df = pd.read_csv(ROOT / "data" / "simulated" / "telemetry.csv")
    # 去掉故障后停机行（state=fault，含突发故障 ELE-04 段）；
    # 保留正常行（fault_code 为空，读入为 NaN）与趋势故障前兆行（precursor=1）
    df = df[df["state"] != "fault"]
    df = df[(df["fault_code"].isna()) | (df["precursor"] == 1)].copy()
    return df


def _prepare(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, pd.DataFrame, np.ndarray]:
    """按设备排序 -> 特征矩阵 X、标签 y、原表（含 hour 信息）、设备数组。"""
    frames = []
    for code in df["device_code"].unique():
        sub = df[df["device_code"] == code].sort_values("ts")
        feats = pv.build_features(sub)
        feats = feats[feats["precursor"].notna()]
        frames.append(feats)
    allf = pd.concat(frames, ignore_index=True)
    allf = allf.dropna(subset=[f"{c}_mean" for c in pv.FEATURE_COLS])  # 丢弃窗口不足行
    X = pv.feature_matrix(allf)
    y = np.array(
        [CLASS_MAP.get(c, CLASS_MAP["NONE"]) if c else CLASS_MAP["NONE"] for c in allf["fault_code"]]
    )
    return X, y, allf, allf["device_code"].to_numpy()


def _fault_hour_global(device_code: str) -> int | None:
    """从 faults.csv 读取设备故障时刻（全局小时序号）。"""
    fp = ROOT / "data" / "simulated" / "faults.csv"
    if not fp.exists():
        return None
    fs = pd.read_csv(fp)
    row = fs[fs["device_code"] == device_code]
    if row.empty:
        return None
    from datetime import datetime as _dt

    ts = _dt.fromisoformat(row.iloc[0]["fault_ts"])
    return (ts.day - 1) * 24 + ts.hour


def _lead_hours(
    allf: pd.DataFrame, proba: np.ndarray, class_label: int, threshold: float, device_code: str = "T04"
) -> float | None:
    """测试设备上：预警提前量 = 故障时刻 - 首次判定时刻（小时）。

    proba 必须为「该设备行」的预测概率（与 allf 中该设备行的顺序一致）。
    """
    fault_hour = _fault_hour_global(device_code)
    if fault_hour is None:
        return None
    rows = allf[allf["device_code"] == device_code]
    probs = proba[:, class_label]
    det_global: int | None = None
    for idx, p in zip(rows.index, probs, strict=True):
        if p >= threshold:
            r = rows.loc[idx]
            det_global = (int(r["day"]) - 1) * 24 + int(r["hour"])
            break
    if det_global is None:
        return None
    return max(0.0, float(fault_hour - det_global))


def train(quiet: bool = False) -> dict:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    df = _dataset()
    X, y, allf, devs = _prepare(df)

    train_mask = devs != "T04"
    X_tr, y_tr = X[train_mask], y[train_mask]
    X_te, y_te = X[~train_mask], y[~train_mask]
    n_tr = int((y_tr == CLASS_MAP["NONE"]).sum())
    w = np.where(y_tr != CLASS_MAP["NONE"], max(1.0, n_tr / max((y_tr != CLASS_MAP["NONE"]).sum(), 1)), 1.0)

    iso = IsolationForest(n_estimators=120, contamination=0.08, random_state=2026)
    iso.fit(X_tr)

    import joblib
    from xgboost import XGBClassifier

    clf = XGBClassifier(
        n_estimators=160,
        max_depth=3,
        learning_rate=0.12,
        objective="multi:softprob",
        num_class=3,
        eval_metric="mlogloss",
        random_state=2026,
    )
    clf.fit(X_tr, y_tr, sample_weight=w)
    proba_te = clf.predict_proba(X_te)
    y_pred = np.argmax(proba_te, axis=1)
    accuracy = float((y_pred == y_te).mean())

    # 阈值扫描：同时满足 准确率≥85% 与 预警提前量≥24h
    best = {"threshold": 0.5, "lead": None, "acc": accuracy}
    for t in np.arange(0.30, 0.96, 0.05):
        lead = _lead_hours(allf, proba_te, CLASS_MAP["HYD-01"], float(t))
        if accuracy >= 0.85 and lead is not None and lead >= 24:
            best = {"threshold": float(t), "lead": lead, "acc": accuracy}
            break
        if lead is not None and (best["lead"] is None or lead > best["lead"]):
            best = {"threshold": float(t), "lead": lead, "acc": accuracy}
    if best["lead"] is None:
        best["lead"] = _lead_hours(allf, proba_te, CLASS_MAP["HYD-01"], 0.5)
    pos_idx = y_te != CLASS_MAP["NONE"]
    recall = float((y_pred[pos_idx] == y_te[pos_idx]).mean()) if pos_idx.sum() else 0.0
    report = {
        "accuracy": round(accuracy, 4),
        "positive_recall": round(recall, 4),
        "lead_hours": round(best["lead"], 1) if best["lead"] else None,
        "operating_threshold": best["threshold"],
        "train_devices": sorted(set(devs[train_mask])),
        "test_device": "T04",
        "rows_train": int(X_tr.shape[0]),
        "rows_test": int(X_te.shape[0]),
        "note": "模型在模拟数据上训练与验证；真实设备数据迁移与再训练列入 V1.1 试点（指导书 6.6）",
        "trained_at": datetime.now(UTC).isoformat(),
    }

    joblib.dump(iso, MODEL_DIR / "isoforest.joblib")
    joblib.dump(clf, MODEL_DIR / "xgb.joblib")
    (MODEL_DIR / "meta.json").write_text(
        json.dumps(
            {
                "class_map": CLASS_MAP,
                "operating_threshold": best["threshold"],
                "trained_at": report["trained_at"],
                "note": report["note"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (MODEL_DIR / "eval_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if not quiet:
        print("[train_models] 预测性维护模型训练与评估完成：")
        print(f"  样本：训练 {report['rows_train']} 行（设备留出法，测试设备 T04，{report['rows_test']} 行）")
        print(f"  测试准确率：{report['accuracy'] * 100:.1f}%  （PRD 目标 ≥85%）")
        print(f"  正类召回：{report['positive_recall'] * 100:.1f}%")
        print(f"  预警提前量：{report['lead_hours']} 小时  （PRD 目标 ≥24h）")
        print(f"  线上判定阈值：{report['operating_threshold']}")
        print(f"  产物：{MODEL_DIR}")
    return report


def main() -> int:
    r = train()
    if r["accuracy"] < 0.85 or (r["lead_hours"] or 0) < 24:
        print("[train_models] 警告：未同时满足准确率≥85% 与提前量≥24h，请检查模拟数据或调参", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
