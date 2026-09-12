"""语料驱动的预测性维护训练（企业级最终版）。

数据现实：7 类故障仅分布于 9 台设备，其中 制动/结构/电池/电气 各只有 1 个实例，
因此"设备级留出"在数学上无法同时训练与测试这些类。故采用双口径评估：

  P1 · 实例时序留出（可部署结论）
     每个故障的"最后 N 小时"（N = min(48, max(8, 0.4×真实提前量))）作为测试窗口，
     完全不参与训练；训练使用 其余全部正常工况 + 各故障的早期前兆段。
     逐检测器阈值由"正常工况误报率 ≤2%"标定（工程上等价于误报预算控制）。

  P2 · 跨设备泛化参考（GroupKFold(3)）
     同设备不跨折；仅统计训练折中存在该类的故障，用于说明跨机泛化现状与数据缺口。

模型：机理分组多检测器（液压/发动机/电气/传动/结构/制动/电池各一个 XGB）
      + 设备稳健基线（中位数/MAD）归一化 + 多尺度特征（1h/6h 均值·漂移·波动）
产出：data/models/corpus/{detectors.joblib,isoforest.joblib,meta.json,eval_report.json}
用法：python -m scripts.train_models_corpus
"""

from __future__ import annotations

import json
import time

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.model_selection import GroupKFold

from backend.app.core.config import settings

ROOT = settings.repo_root
SIM = ROOT / "data" / "simulated"
OUT = ROOT / "data" / "models" / "corpus"

SHORT_W, MID_W = 12, 72
EPS = 1e-6
FAR_BUDGET = 0.02  # 正常工况误报预算（阈值标定目标）

CLASS_CHANNELS: dict[str, list[str]] = {
    "HYD": ["hyd_oil_temp", "hyd_pressure", "vibration"],
    "ENG": ["water_temp", "fuel_lph", "rpm"],
    "ELE": ["voltage", "battery_soc"],
    "DRV": ["trans_oil_temp", "drive_resistance", "vibration"],
    "STR": ["vibration", "hyd_pressure"],
    "BRK": ["brake_pressure", "drive_resistance"],
    "BAT": ["battery_soc", "voltage"],
}
ALL_CHANNELS = [
    "water_temp",
    "hyd_oil_temp",
    "trans_oil_temp",
    "hyd_pressure",
    "vibration",
    "rpm",
    "fuel_lph",
    "brake_pressure",
    "voltage",
    "battery_soc",
    "drive_resistance",
]
PREFIX_CLASS = {
    "HYD": "HYD",
    "ENG": "ENG",
    "ELE": "ELE",
    "DRV": "DRV",
    "STR": "STR",
    "BRK": "BRK",
    "BAT": "BAT",
}


def fault_class(code: str) -> str:
    parts = str(code).split("-")
    return PREFIX_CLASS.get(parts[1] if len(parts) > 1 else "", "OTH")


def build_dataset() -> tuple[pd.DataFrame, pd.DataFrame, dict[str, list[str]], dict, dict[str, float]]:
    tele = pd.read_csv(SIM / "corpus_telemetry.csv")
    faults = pd.read_csv(SIM / "corpus_faults.csv")
    tele["ts"] = pd.to_datetime(tele["ts"])

    frames: list[pd.DataFrame] = []
    baselines: dict[str, dict] = {}
    for dev, g in tele.groupby("device_id", sort=False):
        g = g.sort_values("ts").reset_index(drop=True)
        feat = g[["device_id", "ts"]].copy()
        bases: dict[str, dict] = {}
        for ch in ALL_CHANNELS:
            if ch not in g.columns:
                continue
            med = float(g[ch].median())
            mad = float((g[ch] - med).abs().median()) * 1.4826 + EPS
            bases[ch] = {"median": round(med, 4), "mad": round(mad, 4)}
            z = (g[ch] - med) / mad
            feat[f"{ch}_z"] = z
            feat[f"{ch}_z1h"] = z.rolling(SHORT_W, min_periods=3).mean()
            feat[f"{ch}_z6h"] = z.rolling(MID_W, min_periods=6).mean()
            feat[f"{ch}_drift6h"] = z - z.shift(MID_W)
            feat[f"{ch}_std1h"] = z.rolling(SHORT_W, min_periods=3).std().fillna(0.0)
        baselines[dev] = bases
        frames.append(feat)
    data = pd.concat(frames, ignore_index=True)

    # 标签：早期前兆段 = 训练正样本；最后 N 小时 = 测试窗口（不参与训练）；故障后剔除
    data["label"] = "NONE"
    data["is_test"] = False
    test_window_h: dict[str, float] = {}
    for r in faults.itertuples():
        detect, onset = pd.to_datetime(r.detect_ts), pd.to_datetime(r.onset_ts)
        n_h = min(48.0, max(12.0, 0.5 * float(r.lead_hours)))
        test_start = onset - pd.Timedelta(hours=n_h)
        test_window_h[r.fault_id if hasattr(r, "fault_id") else r.code] = n_h
        dev = data["device_id"] == r.device_id
        cls = fault_class(r.code)
        data.loc[dev & (data["ts"] >= detect) & (data["ts"] < test_start), "label"] = cls
        data.loc[dev & (data["ts"] >= test_start) & (data["ts"] < onset), "label"] = cls
        data.loc[dev & (data["ts"] >= test_start) & (data["ts"] < onset), "is_test"] = True
        data.loc[dev & (data["ts"] >= onset), "label"] = "DROP"
    data = data[data["label"] != "DROP"].reset_index(drop=True)

    class_feats: dict[str, list[str]] = {}
    for cls, chans in CLASS_CHANNELS.items():
        class_feats[cls] = [c for c in data.columns if any(c.startswith(f"{ch}_") for ch in chans)]
    num_cols = [c for c in data.columns if c not in ("device_id", "ts", "label", "is_test")]
    data[num_cols] = data[num_cols].fillna(0.0)
    return data, faults, class_feats, baselines, test_window_h


def _fit_detector(X: np.ndarray, y: np.ndarray):
    from xgboost import XGBClassifier

    pos_w = max(1.0, (y == 0).sum() / max((y == 1).sum(), 1))
    clf = XGBClassifier(
        n_estimators=300,
        max_depth=4,
        learning_rate=0.08,
        objective="binary:logistic",
        eval_metric="logloss",
        scale_pos_weight=pos_w,
        random_state=2026,
        n_jobs=4,
    )
    clf.fit(X, y)
    return clf


def _threshold_by_far(none_proba: np.ndarray, far: float = FAR_BUDGET) -> float:
    """选满足误报预算的最小可用阈值（避免分位数在大量并列 0 值上失效）。"""
    if none_proba.size == 0:
        return 0.5
    for t in np.unique(none_proba):
        if float((none_proba >= t).mean()) <= far:
            return float(t)
    return float(np.max(none_proba))


def evaluate_p1(df: pd.DataFrame, faults: pd.DataFrame, class_feats: dict[str, list[str]]) -> dict:
    """P1 实例时序留出：测试窗口完全未参与训练；阈值用“设备级切分”独立标定。"""
    classes = list(CLASS_CHANNELS)
    lab = df["label"].to_numpy()
    is_test = df["is_test"].to_numpy()
    devices = sorted(df["device_id"].unique())
    # 标定集优先使用“无故障设备”（工程上等价：在健康设备上标定误报预算），
    # 避免按序号切分把某一故障类的全部正样本切出训练集
    fault_devices = set(faults["device_id"])
    healthy_devices = [d for d in devices if d not in fault_devices]
    calib_devices = set(healthy_devices if len(healthy_devices) >= 2 else devices[::3])
    calib_val_mask = df["device_id"].isin(calib_devices).to_numpy()
    proba_all = np.zeros((len(df), len(classes)))
    thresholds: dict[str, float] = {}
    far_measured: list[float] = []

    for ci, cls in enumerate(classes):
        X = df[class_feats[cls]].to_numpy(dtype=np.float32)
        train_all = np.where(((lab == cls) & ~is_test) | (lab == "NONE"))[0]
        # 1) 标定模型：仅用非标定设备；在标定设备的正常工况上取 98% 分位（误报预算 2%）
        fit_rows = train_all[~calib_val_mask[train_all]]
        val_none = np.where((lab == "NONE") & calib_val_mask)[0]
        thr = 0.5
        if (lab[fit_rows] == cls).sum() >= 10 and val_none.size:
            cal = _fit_detector(X[fit_rows], (lab[fit_rows] == cls).astype(int))
            p_val = cal.predict_proba(X[val_none])[:, 1]
            thr = float(_threshold_by_far(p_val))  # 保留精度（切勿 round 到 0）
            far_measured.append(float((p_val >= thr).mean()))
        thresholds[cls] = round(thr, 6)
        # 2) 部署模型：使用全部训练行，作用于全量样本
        if (lab[train_all] == cls).sum() >= 10:
            clf = _fit_detector(X[train_all], (lab[train_all] == cls).astype(int))
            proba_all[:, ci] = clf.predict_proba(X)[:, 1]
    thr_vec = np.array([thresholds[c] for c in classes])

    leads: list[float] = []
    detected, detectable = 0, 0
    comp_hits, comp_total = 0, 0
    per_fault: list[dict] = []
    for r in faults.itertuples():
        cls = fault_class(r.code)
        if cls not in classes:
            continue
        own = classes.index(cls)
        m = (df["device_id"] == r.device_id) & df["is_test"] & (df["label"] == cls)
        rows = np.where(m.to_numpy())[0]
        if rows.size == 0:
            continue
        detectable += 1
        hit = rows[proba_all[rows, own] >= thresholds[cls]]
        onset = pd.to_datetime(r.onset_ts)
        if hit.size:
            detected += 1
            first = int(hit[0])
            lead = round((onset - df.loc[first, "ts"]).total_seconds() / 3600, 1)
            leads.append(lead)
            comp_total += 1
            score = proba_all[first] / np.maximum(thr_vec, EPS)
            top = int(np.argmax(score))
            ok = top == own
            comp_hits += int(ok)
            per_fault.append(
                {
                    "fault": r.code,
                    "device": r.device_id,
                    "class": cls,
                    "detected": True,
                    "lead_hours": lead,
                    "predicted_class": classes[top],
                    "component_ok": ok,
                }
            )
        else:
            per_fault.append(
                {
                    "fault": r.code,
                    "device": r.device_id,
                    "class": cls,
                    "detected": False,
                    "lead_hours": None,
                    "predicted_class": None,
                    "component_ok": False,
                }
            )

    far = float(np.mean(far_measured)) if far_measured else None
    lead_ok = sum(1 for x in leads if x >= 24)
    return {
        "protocol": "P1 实例时序留出（测试窗口未参与训练；阈值按设备级切分独立标定）",
        "detection_rate": round(detected / detectable, 4) if detectable else None,
        "detected": detected,
        "detectable_faults": detectable,
        "lead_hours_avg": round(float(np.mean(leads)), 1) if leads else None,
        "lead_hours_min": round(float(np.min(leads)), 1) if leads else None,
        "lead_ge_24h_count": lead_ok,
        "component_top1_acc": round(comp_hits / comp_total, 4) if comp_total else None,
        "healthy_false_alarm_rate": round(far, 4) if far is not None else None,
        "far_budget": FAR_BUDGET,
        "thresholds": thresholds,
        "per_fault": per_fault,
    }


def evaluate_p2(df: pd.DataFrame, faults: pd.DataFrame, class_feats: dict[str, list[str]]) -> dict:
    """P2 跨设备泛化参考：GroupKFold(3)，仅统计训练折具备该类的故障。"""
    classes = list(CLASS_CHANNELS)
    groups = df["device_id"].to_numpy()
    lab = df["label"].to_numpy()
    is_test = df["is_test"].to_numpy()
    folds: list[dict] = []
    leads: list[float] = []
    for k, (tr, te) in enumerate(GroupKFold(n_splits=3).split(df, groups=groups)):
        proba = np.zeros((len(te), len(classes)))
        thr: dict[str, float] = {}
        for ci, cls in enumerate(classes):
            X = df[class_feats[cls]].to_numpy(dtype=np.float32)
            rows = np.where((((lab == cls) & ~is_test) | (lab == "NONE")) & np.isin(np.arange(len(df)), tr))[
                0
            ]
            y = (lab[rows] == cls).astype(int)
            if y.sum() < 10:
                thr[cls] = 0.5
                continue
            clf = _fit_detector(X[rows], y)
            proba[:, ci] = clf.predict_proba(X[te])[:, 1]
            none_tr = rows[lab[rows] == "NONE"]
            thr[cls] = round(_threshold_by_far(clf.predict_proba(X[none_tr])[:, 1]), 3)
        df_te = df.iloc[te]
        te_devices = set(groups[te])
        fold_detect, fold_cases = 0, 0
        for r in faults.itertuples():
            cls = fault_class(r.code)
            if r.device_id not in te_devices or cls not in classes:
                continue
            train_has_class = ((lab == cls) & ~is_test & np.isin(np.arange(len(df)), tr)).sum() >= 10
            if not train_has_class:
                continue  # 训练折无该类正样本 —— 数据缺口，不计入分母
            m = ((df_te["device_id"] == r.device_id) & df_te["is_test"] & (df_te["label"] == cls)).to_numpy()
            rows_te = np.where(m)[0]
            if rows_te.size == 0:
                continue
            fold_cases += 1
            own = classes.index(cls)
            hit = rows_te[proba[rows_te, own] >= thr[cls]]
            if hit.size:
                fold_detect += 1
                onset = pd.to_datetime(r.onset_ts)
                leads.append(round((onset - df_te.iloc[int(hit[0])]["ts"]).total_seconds() / 3600, 1))
        folds.append(
            {
                "fold": k,
                "detectable_faults": fold_cases,
                "detection_rate": round(fold_detect / fold_cases, 4) if fold_cases else None,
                "test_devices": sorted(te_devices),
                "thresholds": thr,
            }
        )
        print(
            f"  P2 fold{k}: 可测故障 {fold_cases} 例，检出率 "
            f"{round(fold_detect / fold_cases, 4) if fold_cases else None} 设备={sorted(te_devices)}"
        )
    valid = [f["detection_rate"] for f in folds if f["detection_rate"] is not None]
    return {
        "protocol": "P2 跨设备泛化参考（GroupKFold，仅统计训练含该类的故障）",
        "detection_rate": round(float(np.mean(valid)), 4) if valid else None,
        "lead_hours_avg": round(float(np.mean(leads)), 1) if leads else None,
        "lead_samples": len(leads),
        "folds": folds,
        "note": "单实例故障类（BRK/STR/BAT/ELE）无法做跨设备验证，数据缺口需 V1.1 真实数据补充",
    }


def train_final(
    df: pd.DataFrame, class_feats: dict[str, list[str]], baselines: dict, p1_thresholds: dict[str, float]
) -> dict:
    import joblib

    lab = df["label"].to_numpy()
    is_test = df["is_test"].to_numpy()
    detectors: dict[str, object] = {}
    for cls, cols in class_feats.items():
        X = df[cols].to_numpy(dtype=np.float32)
        rows = np.where(((lab == cls) & ~is_test) | (lab == "NONE"))[0]
        y = (lab[rows] == cls).astype(int)
        if y.sum() < 10:
            continue
        detectors[cls] = _fit_detector(X[rows], y)

    all_cols = [c for c in df.columns if c not in ("device_id", "ts", "label", "is_test")]
    iso = IsolationForest(n_estimators=150, contamination=0.05, random_state=2026)
    iso.fit(df[all_cols].to_numpy(dtype=np.float32))

    OUT.mkdir(parents=True, exist_ok=True)
    joblib.dump(detectors, OUT / "detectors.joblib")
    joblib.dump(iso, OUT / "isoforest.joblib")
    (OUT / "meta.json").write_text(
        json.dumps(
            {
                "class_channels": CLASS_CHANNELS,
                "class_feature_cols": class_feats,
                "windows": {"short": SHORT_W, "mid": MID_W},
                "far_budget": FAR_BUDGET,
                "device_baselines": baselines,
                "operating_thresholds": p1_thresholds,
                "trained_at": pd.Timestamp.now(tz="UTC").isoformat(),
                "n_rows": int(len(df)),
                "class_dist": df["label"].value_counts().to_dict(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {"detectors": sorted(detectors), "thresholds": p1_thresholds, "n_rows": int(len(df))}


def main() -> int:
    t0 = time.perf_counter()
    print("[1/4] 稳健基线 + 机理分组特征 + 时序留出标签 …")
    df, faults, class_feats, baselines, tw = build_dataset()
    print(
        f"      样本 {len(df)} 行 / {df['device_id'].nunique()} 台；训练标签 {df[~df['is_test']]['label'].value_counts().to_dict()}"
    )
    print(f"      测试窗口（每故障最后 N 小时，不参与训练）：{[round(v, 1) for v in tw.values()]}")
    print("[2/4] P1 实例时序留出评估（可部署口径）…")
    p1 = evaluate_p1(df, faults, class_feats)
    print(
        f"      检出 {p1['detected']}/{p1['detectable_faults']} = {p1['detection_rate']}，"
        f"提前量 平均 {p1['lead_hours_avg']}h / 最小 {p1['lead_hours_min']}h，"
        f"部件Top1 {p1['component_top1_acc']}，误报 {p1['healthy_false_alarm_rate']}"
    )
    print("[3/4] P2 跨设备泛化参考评估 …")
    p2 = evaluate_p2(df, faults, class_feats)
    print("[4/4] 全量训练并保存产物 …")
    final = train_final(df, class_feats, baselines, p1["thresholds"])

    v1_path = ROOT / "data" / "models" / "eval_report.json"
    v1 = json.loads(v1_path.read_text(encoding="utf-8")) if v1_path.exists() else {}
    report = {
        "dataset": {
            "rows": int(len(df)),
            "devices": int(df["device_id"].nunique()),
            "faults": int(len(faults)),
            "train_label_dist": df[~df["is_test"]]["label"].value_counts().to_dict(),
            "test_window_hours": tw,
        },
        "P1_temporal_holdout": p1,
        "P2_cross_device": p2,
        "final": final,
        "baseline_v1": {
            "accuracy": v1.get("accuracy"),
            "positive_recall": v1.get("positive_recall"),
            "lead_hours": v1.get("lead_hours"),
            "note": "v1 = 自研模拟数据（7 台设备，单一温漂）",
        },
        "verdict": {
            "p1_detection_ok": (p1["detection_rate"] or 0) >= 0.85,
            "p1_lead_ok": (p1["lead_hours_min"] or 0) >= 8,
            "p2_reference": p2["detection_rate"],
        },
        "elapsed_s": round(time.perf_counter() - t0, 1),
        "note": "语料为仿真数据（虚构场地）；单实例故障类无法跨机验证，真实数据迁移列入 V1.1 试点（指导书 6.6）",
    }
    (OUT / "eval_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("=" * 74)
    print(
        f"P1 检出率 {p1['detection_rate']}（{p1['detected']}/{p1['detectable_faults']}）| "
        f"提前量 平均 {p1['lead_hours_avg']}h / 最小 {p1['lead_hours_min']}h | "
        f"部件Top1 {p1['component_top1_acc']} | 误报 {p1['healthy_false_alarm_rate']}"
    )
    print(f"P2 跨设备参考检出率 {p2['detection_rate']}（单实例类不可跨机验证，已在报告说明）")
    print(
        f"对比 v1（自研模拟）：准确率 {v1.get('accuracy')} / 召回 {v1.get('positive_recall')} / "
        f"提前量 {v1.get('lead_hours')}h"
    )
    print(f"产物：{OUT}   用时 {report['elapsed_s']}s")
    return 0 if (report["verdict"]["p1_detection_ok"] and report["verdict"]["p1_lead_ok"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
