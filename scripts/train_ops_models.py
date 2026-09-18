"""项目运营模型重训（新语料 2500 条 mining_project_operation）。

任务：
  A. 工期偏差回归：schedule_deviation_days（天）
  B. 成本偏差率回归：actual_cost_cny / budget_cny - 1
  C. 延期二分类：schedule_deviation_days > 0
  D. 超支二分类：actual_cost_cny > budget_cny

协议（沿用企业级纪律）：
  * 防泄漏：标签来源字段 actual_duration_days / actual_cost_cny / schedule_deviation_days / task_status
    一律不入特征；仅用计划侧与工况字段（工序/地形/区域/工程量/预算/设备数/计划工期/约束）。
  * 划分：沿用语料自带的 agent_train / project_test（各 1250 条，项目级零交叉），
    训练集内 5 折交叉验证，独立测试集只在最后评估一次；不调参、不按测试集选模型。
  * 基线对照：回归=训练集中位数；分类=训练集多数类。ML 需在独立测试集与交叉验证上同时优于基线才上线。

产物：data/models/ops/{delay_reg.joblib,cost_ratio_reg.joblib,delay_clf.joblib,overrun_clf.joblib,
      meta.json,eval_report.json,baseline_model.json}
用法：python -m scripts.train_ops_models
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier, GradientBoostingRegressor
from sklearn.metrics import (
    accuracy_score,
    f1_score,
    mean_absolute_error,
    mean_squared_error,
    r2_score,
    roc_auc_score,
)

from backend.app.core.config import settings
from backend.app.core.db import SessionLocal

OUT = settings.repo_root / "data" / "models" / "ops"
CAT_COLS = ["construction_task", "terrain", "region", "scenario_type", "quality_standard"]
NUM_COLS = ["planned_duration_days", "engineering_quantity_t", "budget_cny", "equipment_count",
            "daily_operation_hours", "max_road_grade_pct", "safety_req_count", "deadline_days",
            "budget_limit_cny", "start_month"]
LEAKY = ("actual_duration_days", "actual_cost_cny", "schedule_deviation_days", "task_status")


def load_frame() -> pd.DataFrame:
    db = SessionLocal()
    try:
        df = pd.read_sql(
            "SELECT record_key, split, project_id, construction_task, terrain, region, scenario_type, "
            "quality_standard, planned_start, planned_duration_days, actual_duration_days, "
            "schedule_deviation_days, engineering_quantity_t, budget_cny, actual_cost_cny, "
            "task_status, equipment_count, constraints FROM corpus_proj_operation",
            db.bind,
        )
    finally:
        db.close()
    # SQLAlchemy JSON 列在 SQLite 中以 TEXT 存储，read_sql 取回的是字符串，需还原为 dict
    df["constraints"] = df["constraints"].apply(
        lambda v: json.loads(v) if isinstance(v, str) and v.strip() else (v if isinstance(v, dict) else {})
    )
    return df


def build_features(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    d = df.copy()
    cons = pd.json_normalize(d["constraints"]).reindex(d.index)
    d["daily_operation_hours"] = pd.to_numeric(cons.get("daily_operation_hours"), errors="coerce")
    d["max_road_grade_pct"] = pd.to_numeric(cons.get("max_road_grade_pct"), errors="coerce")
    d["deadline_days"] = pd.to_numeric(cons.get("deadline_days"), errors="coerce")
    d["budget_limit_cny"] = pd.to_numeric(cons.get("budget_limit_cny"), errors="coerce")
    d["safety_req_count"] = cons.get("safety_requirements").apply(
        lambda v: len(v) if isinstance(v, list) else 0)
    d["start_month"] = pd.to_datetime(d["planned_start"], errors="coerce").dt.month
    for c in CAT_COLS:
        d[c] = d[c].fillna("").astype(str)
    X = pd.get_dummies(d[CAT_COLS + NUM_COLS], columns=CAT_COLS, dummy_na=True)
    X = X.astype(float).fillna(0.0)
    return X, list(X.columns)


def _reg_metrics(y_true, y_pred, base_value) -> dict:
    mae = float(mean_absolute_error(y_true, y_pred))
    base_mae = float(mean_absolute_error(y_true, np.full(len(y_true), base_value)))
    return {
        "mae": round(mae, 4),
        "rmse": round(float(np.sqrt(mean_squared_error(y_true, y_pred))), 4),
        "r2": round(float(r2_score(y_true, y_pred)), 4) if len(y_true) > 2 else None,
        "baseline_mae": round(base_mae, 4),
        "beats_baseline": bool(mae < base_mae),
        "n": int(len(y_true)),
    }


def _clf_metrics(y_true, proba, pred, base_label) -> dict:
    acc = float(accuracy_score(y_true, pred))
    base_acc = float((np.asarray(y_true) == base_label).mean())
    auc = None
    if len(set(np.asarray(y_true).tolist())) > 1:
        auc = round(float(roc_auc_score(y_true, proba)), 4)
    return {
        "accuracy": round(acc, 4),
        "macro_f1": round(float(f1_score(y_true, pred, average="macro", zero_division=0)), 4),
        "auc": auc,
        "baseline_accuracy": round(base_acc, 4),
        "beats_baseline": bool(acc > base_acc),
        "positive_rate": round(float(np.mean(np.asarray(y_true) == 1)), 4),
        "n": int(len(y_true)),
    }


def _cv_reg(X, y, make_model, folds: int = 5) -> dict:
    from sklearn.model_selection import KFold

    maes = []
    kf = KFold(n_splits=folds, shuffle=True, random_state=20260918)
    for tr, te in kf.split(X):
        m = make_model()
        m.fit(X.iloc[tr], y.iloc[tr])
        maes.append(mean_absolute_error(y.iloc[te], m.predict(X.iloc[te])))
    arr = np.asarray(maes)
    half = 1.96 * arr.std(ddof=1) / np.sqrt(len(arr)) if len(arr) > 1 else float("nan")
    return {"cv_mae_mean": round(float(arr.mean()), 4),
            "cv_mae_ci95": [round(float(arr.mean() - half), 4), round(float(arr.mean() + half), 4)]}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    df = load_frame()
    print(f"[train_ops_models] 项目运营记录 {len(df)} 条；"
          f"split={df['split'].value_counts().to_dict()}")

    X, feat_cols = build_features(df)
    leaked = [c for c in feat_cols if any(lk in c for lk in LEAKY)]
    assert not leaked, f"特征中含标签来源字段：{leaked}"

    tr = (df["split"] == "agent_train").to_numpy()
    te = (df["split"] == "project_test").to_numpy()
    assert not (set(df.loc[tr, "project_id"]) & set(df.loc[te, "project_id"])), "项目级交叉，禁止评估"

    y_delay = df["schedule_deviation_days"].astype(float)
    cost_ratio = (df["actual_cost_cny"] / df["budget_cny"].replace(0, np.nan) - 1.0).fillna(0.0)
    y_delay_flag = (y_delay > 0).astype(int)
    y_overrun = (df["actual_cost_cny"] > df["budget_cny"]).astype(int)

    # ---------------- EDA（先看数据里到底有没有信号） ----------------
    print("\n=== EDA ===")
    print(f"  工期偏差(天)：均值 {y_delay.mean():.2f}，中位数 {y_delay.median():.1f}，"
          f"P90 {y_delay.quantile(0.9):.1f}，范围 [{y_delay.min():.0f}, {y_delay.max():.0f}]")
    print(f"  延期比例 {y_delay_flag.mean():.3f}；超支比例 {y_overrun.mean():.3f}；"
          f"成本偏差率 均值 {cost_ratio.mean():.4f} 中位数 {cost_ratio.median():.4f}")
    varying = X.loc[:, X.std(numeric_only=True) > 0]
    corr = varying.corrwith(y_delay).dropna().abs().sort_values(ascending=False)
    print(f"  与工期偏差相关性 Top5：{[(k, round(v, 3)) for k, v in corr.head(5).items()]}")
    corr_cost = varying.corrwith(cost_ratio).dropna().abs().sort_values(ascending=False)
    print(f"  与成本偏差率相关性 Top5：{[(k, round(v, 3)) for k, v in corr_cost.head(5).items()]}")

    report: dict = {
        "trained_at": datetime.now(UTC).isoformat(),
        "dataset": {"records": int(len(df)), "train": int(tr.sum()), "test": int(te.sum()),
                    "feature_count": len(feat_cols), "features": feat_cols,
                    "leakage_guard": "actual_duration_days/actual_cost_cny/schedule_deviation_days/"
                                     "task_status 均不入特征"},
        "eda": {
            "schedule_deviation_days": {"mean": round(float(y_delay.mean()), 3),
                                        "median": round(float(y_delay.median()), 3),
                                        "p90": round(float(y_delay.quantile(0.9)), 3),
                                        "delay_rate": round(float(y_delay_flag.mean()), 4)},
            "cost_ratio": {"mean": round(float(cost_ratio.mean()), 4),
                           "median": round(float(cost_ratio.median()), 4),
                           "overrun_rate": round(float(y_overrun.mean()), 4)},
            "top_corr_delay": {k: round(float(v), 4) for k, v in corr.head(8).items()},
            "top_corr_cost": {k: round(float(v), 4) for k, v in corr_cost.head(8).items()},
        },
    }

    def gbr():
        return GradientBoostingRegressor(n_estimators=300, max_depth=3, learning_rate=0.05,
                                         subsample=0.9, random_state=20260918)

    def gbc():
        return GradientBoostingClassifier(n_estimators=300, max_depth=3, learning_rate=0.05,
                                          subsample=0.9, random_state=20260918)

    results: dict = {}
    Xtr, Xte = X[tr], X[te]

    # ---------------- A. 工期偏差回归 ----------------
    model = gbr().fit(Xtr, y_delay[tr])
    pred = model.predict(Xte)
    med = float(y_delay[tr].median())
    res_a = _reg_metrics(y_delay[te], pred, med)
    res_a.update(_cv_reg(Xtr, y_delay[tr], gbr))
    res_a["baseline_median"] = med
    results["delay_regression"] = res_a
    print(f"\nA 工期偏差回归：测试 MAE {res_a['mae']} 天（基线 {res_a['baseline_mae']}）R² {res_a['r2']}；"
          f"CV MAE {res_a['cv_mae_mean']} {res_a['cv_mae_ci95']} → 优于基线={res_a['beats_baseline']}")

    # ---------------- B. 成本偏差率回归 ----------------
    model_b = gbr().fit(Xtr, cost_ratio[tr])
    pred_b = model_b.predict(Xte)
    med_b = float(cost_ratio[tr].median())
    res_b = _reg_metrics(cost_ratio[te], pred_b, med_b)
    res_b.update(_cv_reg(Xtr, cost_ratio[tr], gbr))
    res_b["baseline_median"] = med_b
    results["cost_ratio_regression"] = res_b
    print(f"B 成本偏差率回归：测试 MAE {res_b['mae']}（基线 {res_b['baseline_mae']}）R² {res_b['r2']}；"
          f"CV MAE {res_b['cv_mae_mean']} → 优于基线={res_b['beats_baseline']}")

    # ---------------- C. 延期二分类 ----------------
    clf_c = gbc().fit(Xtr, y_delay_flag[tr])
    proba_c = clf_c.predict_proba(Xte)[:, 1]
    base_label = int(y_delay_flag[tr].mode().iloc[0])
    res_c = _clf_metrics(y_delay_flag[te], proba_c, (proba_c >= 0.5).astype(int), base_label)
    results["delay_classification"] = res_c
    print(f"C 延期二分类：acc {res_c['accuracy']}（基线 {res_c['baseline_accuracy']}）AUC {res_c['auc']} "
          f"F1 {res_c['macro_f1']} → 优于基线={res_c['beats_baseline']}")

    # ---------------- D. 超支二分类 ----------------
    clf_d = gbc().fit(Xtr, y_overrun[tr])
    proba_d = clf_d.predict_proba(Xte)[:, 1]
    base_label_d = int(y_overrun[tr].mode().iloc[0])
    res_d = _clf_metrics(y_overrun[te], proba_d, (proba_d >= 0.5).astype(int), base_label_d)
    results["overrun_classification"] = res_d
    print(f"D 超支二分类：acc {res_d['accuracy']}（基线 {res_d['baseline_accuracy']}）AUC {res_d['auc']} "
          f"F1 {res_d['macro_f1']} → 优于基线={res_d['beats_baseline']}")

    # ---------------- 上线门控 ----------------
    gate = {
        "delay_regression": bool(res_a["beats_baseline"] and res_a["cv_mae_ci95"][1] < res_a["baseline_mae"]),
        "cost_ratio_regression": bool(res_b["beats_baseline"] and res_b["cv_mae_ci95"][1] < res_b["baseline_mae"]),
        "delay_classification": bool(res_c["beats_baseline"] and (res_c["auc"] or 0) > 0.55),
        "overrun_classification": bool(res_d["beats_baseline"] and (res_d["auc"] or 0) > 0.55),
    }
    deployed = sorted(k for k, v in gate.items() if v)
    decision = {
        "gates": gate,
        "deployed_models": deployed,
        "production_method": ("ml_model" if deployed else "calibrated_statistical_baseline"),
        "gate_rule": "需同时满足：独立测试集优于朴素基线 且 交叉验证置信区间上界仍优于基线"
                     "（分类另需 AUC > 0.55）",
    }
    report["results"] = results
    report["deploy_decision"] = decision

    import joblib

    joblib.dump({"model": model, "feature_cols": feat_cols, "target": "schedule_deviation_days",
                 "deployed": gate["delay_regression"]}, OUT / "delay_reg.joblib")
    joblib.dump({"model": model_b, "feature_cols": feat_cols, "target": "cost_ratio",
                 "deployed": gate["cost_ratio_regression"]}, OUT / "cost_ratio_reg.joblib")
    joblib.dump({"model": clf_c, "feature_cols": feat_cols, "target": "delay_flag",
                 "deployed": gate["delay_classification"]}, OUT / "delay_clf.joblib")
    joblib.dump({"model": clf_d, "feature_cols": feat_cols, "target": "overrun_flag",
                 "deployed": gate["overrun_classification"]}, OUT / "overrun_clf.joblib")

    # 生产基准（无论是否上线 ML 都要给出，供运营参考）
    base_model = {
        "schedule_deviation_days": {
            "n_calibration": int(tr.sum()),
            **{f"p{int(q * 100)}": round(float(y_delay[tr].quantile(q)), 2)
               for q in (0.5, 0.75, 0.9, 0.95)},
            "mean": round(float(y_delay[tr].mean()), 2),
        },
        "cost_ratio": {
            "n_calibration": int(tr.sum()),
            **{f"p{int(q * 100)}": round(float(cost_ratio[tr].quantile(q)), 4)
               for q in (0.25, 0.5, 0.75, 0.9)},
            "mean": round(float(cost_ratio[tr].mean()), 4),
        },
        "by_construction_task": {
            str(task): {"n": int(len(g)),
                        "delay_mean": round(float(g["schedule_deviation_days"].mean()), 2),
                        "delay_p90": round(float(g["schedule_deviation_days"].quantile(0.9)), 2)}
            for task, g in df[tr].groupby("construction_task") if len(g) >= 20
        },
        "meaning": "运营基准：工期偏差与成本偏差分位数（训练集标定），用于排期缓冲与预算预警",
    }
    (OUT / "baseline_model.json").write_text(json.dumps(base_model, ensure_ascii=False, indent=2),
                                             encoding="utf-8")
    report["baseline_model"] = base_model
    (OUT / "meta.json").write_text(json.dumps(
        {"feature_cols": feat_cols, "generated_at": report["trained_at"],
         "leakage_guard": report["dataset"]["leakage_guard"]}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    (OUT / "eval_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                          encoding="utf-8")

    print(f"\n上线门控：{gate}")
    print(f"生产方法：{decision['production_method']}（上线模型 {deployed or '无'}）")
    print(f"运营基准：工期偏差 P50 {base_model['schedule_deviation_days']['p50']} 天 / "
          f"P90 {base_model['schedule_deviation_days']['p90']} 天；"
          f"成本偏差率 P50 {base_model['cost_ratio']['p50']} / P90 {base_model['cost_ratio']['p90']}")
    print(f"产物：{OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
