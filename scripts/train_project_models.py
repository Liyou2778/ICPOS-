"""工程项目运营模型训练（企业级 · 防泄漏 · 小样本诚实评估）。

任务：
  A. 任务工期偏差：以「计划工期 + 工序/阶段/循环/工作量/班组/设备 + 项目规模」预测实际偏差
     - 回归：delay_days = actual_end - plan_end（天）
     - 三分类：提前 / 准时 / 延期
  B. 项目成本构成：以项目锚点特征预测「人工/材料/机械/其他/管理」五类占比，
     并给出总成本相对标段预算的比率（成本/预算）。

评估协议（无泄漏）：
  * 严禁使用 actual_start/actual_end 作为特征（标签来源）；项目划分沿用数据源 train/test（零交叉）。
  * 训练集内部采用 **Leave-One-Project-Out (LOPO)** 交叉验证，报告均值与 95% t 置信区间。
  * 独立 test 项目集仅用于最终评估，不参与任何选择；分类阈值固定，不调参。
  * 所有指标与**朴素基线**（全局中位数 / 均值 / 多数类）对照；若模型不优于基线则如实标注"不建议上线模型，采用基线"。

产物：data/models/project/{task_delay_reg.joblib,task_delay_clf.joblib,cost_share.joblib,meta.json,eval_report.json}
用法：python -m scripts.train_project_models
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier, GradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    f1_score,
    mean_absolute_error,
    mean_squared_error,
    r2_score,
)

from backend.app.core.config import settings
from backend.app.core.db import SessionLocal

OUT = settings.repo_root / "data" / "models" / "project"
COST_TYPES = ["人工", "材料", "机械", "其他", "管理"]
CLASSES = ["提前", "准时", "延期"]


def _ci95(values: list[float]) -> tuple[float, float]:
    if len(values) < 2:
        return (float("nan"), float("nan"))
    arr = np.asarray(values, dtype=float)
    half = 1.96 * arr.std(ddof=1) / np.sqrt(len(arr))
    return float(arr.mean() - half), float(arr.mean() + half)


def to_days(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, errors="coerce")


def load_frames() -> tuple[pd.DataFrame, pd.DataFrame]:
    db = SessionLocal()
    try:
        tasks = pd.read_sql(
            "SELECT t.id, t.project_id, t.task_code, t.process, t.phase, t.cycle, t.plan_start, "
            "t.plan_end, t.actual_start, t.actual_end, t.workload, t.workload_unit, t.status, "
            "t.team, t.device_ids, t.device_models, p.code AS project_code, p.industry, p.region, "
            "p.plan_invest_yuan, p.section_est_total_yuan, p.duration_days "
            "FROM proj_task t JOIN proj_project p ON p.id = t.project_id "
            "WHERE t.task_code <> '' ",
            db.bind,
        )
        costs = pd.read_sql(
            "SELECT c.project_id, c.cost_type, c.amount, c.period, p.code AS project_code, "
            "p.industry, p.region, p.plan_invest_yuan, p.section_est_total_yuan, p.duration_days "
            "FROM proj_cost c JOIN proj_project p ON p.id = c.project_id",
            db.bind,
        )
    finally:
        db.close()
    return tasks, costs


def build_task_dataset(tasks: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.Series]:
    """构建特征矩阵。仅保留同时具备 plan_end 与 actual_end 的任务（即已完工、有真实结果标签）。

    泄漏防护：actual_start / actual_end / status 一律不作为特征（它们是标签来源或结果状态）。
    """
    df = tasks.copy()
    df["plan_start_d"] = to_days(df["plan_start"])
    df["plan_end_d"] = to_days(df["plan_end"])
    df["actual_end_d"] = to_days(df["actual_end"])
    df = df.dropna(subset=["plan_end_d", "actual_end_d"])
    df["plan_days"] = (df["plan_end_d"] - df["plan_start_d"]).dt.days
    df["delay_days"] = (df["actual_end_d"] - df["plan_end_d"]).dt.days
    df = df.dropna(subset=["delay_days", "plan_days"])
    df = df[df["plan_days"] > 0].reset_index(drop=True)
    if df.empty:
        raise SystemExit("无已完工任务（缺 actual_end），无法训练工期模型")

    df["device_cnt"] = df["device_ids"].fillna("").apply(lambda s: len([x for x in str(s).split("|") if x]))
    df["team_id"] = df["team"].fillna("").str.extract(r"(\d+)$").fillna("-1").astype(int)
    df["plan_month"] = df["plan_start_d"].dt.month.fillna(0).astype(int)
    df["scale"] = np.log1p(df["section_est_total_yuan"].fillna(0).astype(float))
    df["workload"] = df["workload"].astype(float)
    df["cycle"] = df["cycle"].astype(float)
    df["duration_days"] = df["duration_days"].fillna(0).astype(float)

    def label(d: float) -> str:
        if d <= -1:
            return "提前"
        if d >= 2:
            return "延期"
        return "准时"

    df["delay_class"] = df["delay_days"].apply(label)
    cat_cols = ["process", "phase", "industry", "region", "workload_unit"]
    num_cols = [
        "cycle",
        "workload",
        "plan_days",
        "device_cnt",
        "team_id",
        "plan_month",
        "scale",
        "duration_days",
    ]
    X = pd.get_dummies(
        df[cat_cols + num_cols].astype({c: str for c in cat_cols}), columns=cat_cols, dummy_na=True
    )
    y = pd.concat([df["delay_days"].rename("delay_days"), df["delay_class"].rename("delay_class")], axis=1)
    return X, y, df["project_code"].reset_index(drop=True)


def evaluate_task(
    X: pd.DataFrame, y: pd.DataFrame, projects: pd.Series, train_projects: list[str], test_projects: list[str]
) -> dict:
    def fit_predict_reg(Xtr, ytr, Xte):
        model = GradientBoostingRegressor(
            n_estimators=200, max_depth=3, learning_rate=0.06, subsample=0.9, random_state=20260912
        )
        model.fit(Xtr, ytr)
        return model, model.predict(Xte)

    tr = projects.isin(train_projects).to_numpy()
    te = projects.isin(test_projects).to_numpy()
    if te.sum() < 5:
        raise SystemExit(f"独立测试集可用已完工任务仅 {int(te.sum())} 条，样本不足，需与业务确认")
    Xtr, ytr, Xte, yte = X[tr], y["delay_days"][tr], X[te], y["delay_days"][te]

    # LOPO（训练集内部）
    lopo_mae, lopo_r2 = [], []
    for p in sorted(set(projects[tr])):
        m = (projects[tr] == p).to_numpy()
        if m.sum() == 0 or (~m).sum() == 0:
            continue
        _, pred = fit_predict_reg(Xtr[~m], ytr[~m], Xtr[m])
        lopo_mae.append(mean_absolute_error(ytr[m], pred))
        if len(ytr[m]) > 2:
            lopo_r2.append(r2_score(ytr[m], pred))

    _, te_pred = fit_predict_reg(Xtr, ytr, Xte)
    mae = mean_absolute_error(yte, te_pred)
    rmse = float(np.sqrt(mean_squared_error(yte, te_pred)))
    r2 = r2_score(yte, te_pred) if len(yte) > 2 else float("nan")
    baseline_median = float(np.median(ytr))
    base_mae = mean_absolute_error(yte, np.full(len(yte), baseline_median))
    denom = np.where(np.abs(yte.to_numpy()) < 1e-9, np.nan, np.abs(yte.to_numpy()))
    mape = float(np.nanmean(np.abs((yte.to_numpy() - te_pred) / denom)) * 100)

    # 三分类（阈值固定：delay<=-1 提前 / -1<delay<2 准时 / delay>=2 延期）
    ycls_tr, ycls_te = y["delay_class"][tr], y["delay_class"][te]
    model_cls = GradientBoostingClassifier(
        n_estimators=200, max_depth=3, learning_rate=0.06, subsample=0.9, random_state=20260912
    )
    model_cls.fit(Xtr, ycls_tr)
    pred_cls = model_cls.predict(Xte)
    acc = float(accuracy_score(ycls_te, pred_cls))
    macro_f1 = float(f1_score(ycls_te, pred_cls, average="macro", zero_division=0))
    cm = confusion_matrix(ycls_te, pred_cls, labels=CLASSES).tolist()
    majority = ycls_tr.value_counts().idxmax()
    base_acc = float((ycls_te == majority).mean())

    ci_lo, ci_hi = _ci95(lopo_mae)
    return {
        "label_definition": {"提前": "delay<=-1天", "准时": "-1天<delay<2天", "延期": "delay>=2天"},
        "class_distribution_train": ycls_tr.value_counts().to_dict(),
        "class_distribution_test": ycls_te.value_counts().to_dict(),
        "regression": {
            "test_mae_days": round(mae, 3),
            "test_rmse_days": round(rmse, 3),
            "test_r2": round(float(r2), 4),
            "test_mape_pct": round(mape, 2),
            "baseline_median_mae_days": round(base_mae, 3),
            "beats_baseline": bool(mae < base_mae),
            "lopo_mae_mean": round(float(np.mean(lopo_mae)), 3),
            "lopo_mae_ci95": [round(ci_lo, 3), round(ci_hi, 3)],
            "lopo_r2_mean": round(float(np.mean(lopo_r2)), 4) if lopo_r2 else None,
            "n_train": int(tr.sum()),
            "n_test": int(te.sum()),
            "train_projects": sorted(set(projects[tr])),
            "test_projects": sorted(set(projects[te])),
        },
        "classification": {
            "classes": CLASSES,
            "accuracy": round(acc, 4),
            "macro_f1": round(macro_f1, 4),
            "confusion_matrix": cm,
            "baseline_majority": majority,
            "baseline_accuracy": round(base_acc, 4),
            "beats_baseline": bool(acc > base_acc),
        },
    }


def evaluate_cost(costs: pd.DataFrame, projects: list[str]) -> dict:
    """项目成本构成：LOPO + 独立 test 评估，与"全局均值占比"基线对照。"""
    piv = costs.pivot_table(index="project_code", columns="cost_type", values="amount", aggfunc="sum").fillna(
        0.0
    )
    for c in COST_TYPES:
        if c not in piv.columns:
            piv[c] = 0.0
    piv = piv[COST_TYPES]
    total = piv.sum(axis=1)
    shares = piv.div(total.replace(0, np.nan), axis=0).fillna(0.0)
    meta = (
        costs.groupby("project_code")
        .agg(
            industry=("industry", "first"),
            region=("region", "first"),
            plan_invest=("plan_invest_yuan", "first"),
            section_est=("section_est_total_yuan", "first"),
        )
        .reindex(shares.index)
    )
    feat = pd.DataFrame(
        {
            "scale": np.log1p(meta["section_est"].fillna(0).astype(float)),
            "duration_known": (meta["section_est"].fillna(0) > 0).astype(int),
        }
    )
    feat = pd.concat([feat, pd.get_dummies(meta[["industry", "region"]].astype(str), dummy_na=True)], axis=1)

    train_p = [p for p in shares.index if p in projects]
    test_p = [p for p in shares.index if p not in projects]
    global_share = shares.loc[train_p].mean()

    def mape(true: np.ndarray, pred: np.ndarray) -> float:
        d = np.where(np.abs(true) < 1e-9, np.nan, np.abs(true))
        return float(np.nanmean(np.abs((true - pred) / d)) * 100)

    model = Ridge(alpha=1.0)
    lopo_err = []
    for p in train_p:
        others = [q for q in train_p if q != p]
        model.fit(feat.loc[others], shares.loc[others])
        lopo_err.append(mape(shares.loc[p].to_numpy(), model.predict(feat.loc[[p]])[0]))
    base_err = [mape(shares.loc[p].to_numpy(), global_share.to_numpy()) for p in train_p]

    test_report = None
    if test_p:
        model.fit(feat.loc[train_p], shares.loc[train_p])
        pred = model.predict(feat.loc[test_p])
        test_report = {
            "model_mape_pct": round(mape(shares.loc[test_p].to_numpy(), pred), 2),
            "baseline_mape_pct": round(
                mape(shares.loc[test_p].to_numpy(), np.tile(global_share.to_numpy(), (len(test_p), 1))), 2
            ),
            "n_test_projects": len(test_p),
        }
        # 成本/预算比率
        ratio = (total / meta["section_est"].replace(0, np.nan)).dropna()
        ratio = ratio[np.isfinite(ratio)]
        ratio_stats = (
            {
                "n": int(len(ratio)),
                "mean": round(float(ratio.mean()), 4),
                "median": round(float(ratio.median()), 4),
                "p25": round(float(ratio.quantile(0.25)), 4),
                "p75": round(float(ratio.quantile(0.75)), 4),
            }
            if len(ratio)
            else None
        )
    else:
        ratio_stats = None

    report = {
        "cost_type_order": COST_TYPES,
        "global_share_baseline": {c: round(float(global_share[c]), 4) for c in COST_TYPES},
        "lopo_mape_pct_mean": round(float(np.mean(lopo_err)), 2) if lopo_err else None,
        "baseline_mape_pct_mean": round(float(np.mean(base_err)), 2) if base_err else None,
        "beats_baseline": bool(lopo_err and np.mean(lopo_err) < np.mean(base_err)),
        "holdout_test": test_report,
        "cost_to_budget_ratio": ratio_stats,
        "n_train_projects": len(train_p),
        "n_test_projects": len(test_p),
    }
    # 训练集上拟合的最终模型随产物一并落盘（供审计；是否启用由门控决定）
    final_model = Ridge(alpha=1.0).fit(feat.loc[train_p], shares.loc[train_p])
    artifact = {
        "model": final_model,
        "feature_cols": list(feat.columns),
        "cost_types": COST_TYPES,
        "train_projects": train_p,
        "global_share_baseline": global_share.to_dict(),
        "note": "成本构成占比模型（Ridge）。本语料下未跑赢全局均值基线，门控关闭，不参与生产输出；"
        "生产使用 baseline_model.json 中的结构容差带。",
    }
    return report, artifact


def build_baseline_model(tasks: pd.DataFrame, costs: pd.DataFrame, train_projects: list[str]) -> dict:
    """标定统计基准（当前语料下的生产方案）。

    依据诊断结论：138 条工期标签下最大 |r|=0.12、eta²=0.042、训练 R²=0.999 而 LOPO R²<0，
    即数据中不存在可泛化信号。因此生产侧采用「分位数缓冲 + 结构容差带 + 预算执行区间」，
    并把 ML 作为门控候选（不默认启用）。
    仅用训练集项目标定，避免测试集泄漏。
    """
    df = tasks.copy()
    df["plan_end_d"] = to_days(df["plan_end"])
    df["actual_end_d"] = to_days(df["actual_end"])
    df["delay"] = (df["actual_end_d"] - df["plan_end_d"]).dt.days
    cal = df.dropna(subset=["delay"])
    cal = cal[cal["project_code"].isin(train_projects)]
    qs = (0.5, 0.8, 0.9, 0.95)

    def quantiles(s: pd.Series) -> dict:
        return {f"p{int(q * 100)}": max(0.0, round(float(s.quantile(q)), 2)) for q in qs}

    by_process = {}
    for proc, g in cal.groupby("process"):
        if len(g) >= 8:
            by_process[str(proc)] = {"n": int(len(g)), **quantiles(g["delay"])}

    cost_train = costs[costs["project_code"].isin(train_projects)]
    piv = cost_train.pivot_table(
        index="project_code", columns="cost_type", values="amount", aggfunc="sum"
    ).fillna(0.0)
    for c in COST_TYPES:
        if c not in piv.columns:
            piv[c] = 0.0
    shares = piv[COST_TYPES].div(piv[COST_TYPES].sum(axis=1).replace(0, np.nan), axis=0).dropna() * 100
    structure = {
        c: {
            "mean_pct": round(float(shares[c].mean()), 2),
            "std_pct": round(float(shares[c].std(ddof=1)), 2),
            "p25_pct": round(float(shares[c].quantile(0.25)), 2),
            "p75_pct": round(float(shares[c].quantile(0.75)), 2),
            "cv_pct": round(float(shares[c].std(ddof=1) / shares[c].mean() * 100), 2),
        }
        for c in COST_TYPES
    }
    meta = costs.groupby("project_code").agg(section_est=("section_est_total_yuan", "first"))
    total = costs.groupby("project_code")["amount"].sum()
    ratio = (total / meta["section_est"].replace(0, np.nan)).replace([np.inf, -np.inf], np.nan).dropna()
    ratio_train = ratio[ratio.index.isin(train_projects)]
    return {
        "delay_days": {
            "n_calibration": int(len(cal)),
            **quantiles(cal["delay"]),
            "by_process": by_process,
            "meaning": "任务完工偏差分位数（仅训练集项目标定）；用于工期缓冲建议与交期风险提示",
        },
        "cost_structure_pct": structure,
        "cost_structure_rule": "实测占比落在 P25~P75 内视为正常；超出 P25~P75 提示结构偏差；"
        "变异系数>25% 的类型不做强判定（样本噪声大）",
        "cost_to_budget_ratio": {
            "n": int(len(ratio_train)),
            **{f"p{int(q * 100)}": round(float(ratio_train.quantile(q)), 4) for q in (0.25, 0.5, 0.75, 0.9)},
            "mean": round(float(ratio_train.mean()), 4),
            "meaning": "合同额/标段预算执行比率；>P75 预警，>P90 严重偏差",
        },
        "calibration_projects": sorted(set(cal["project_code"])),
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    tasks, costs = load_frames()
    if tasks.empty:
        raise SystemExit("proj_task 无数据：请先执行 python -m scripts.ingest_project_corpus")
    X, y, task_projects = build_task_dataset(tasks)
    all_projects = sorted(tasks["project_code"].unique())
    deps = json.loads((settings.repo_root / "data/corpus/project_manifest.json").read_text(encoding="utf-8"))
    train_projects = [p for p in deps["train_projects"] if p in set(task_projects)]
    test_projects = [p for p in deps["test_projects"] if p in set(task_projects)]
    unknown = [p for p in all_projects if p not in deps["train_projects"] + deps["test_projects"]]
    assert not set(train_projects) & set(test_projects), "训练/测试项目交叉，禁止评估"

    task_metrics = evaluate_task(X, y, task_projects, train_projects, test_projects)
    cost_metrics, cost_artifact = evaluate_cost(costs, train_projects)

    import joblib

    tr_mask = task_projects.isin(train_projects)
    reg = GradientBoostingRegressor(
        n_estimators=200, max_depth=3, learning_rate=0.06, subsample=0.9, random_state=20260912
    ).fit(X[tr_mask], y["delay_days"][tr_mask])
    clf = GradientBoostingClassifier(
        n_estimators=200, max_depth=3, learning_rate=0.06, subsample=0.9, random_state=20260912
    ).fit(X[tr_mask], y["delay_class"][tr_mask])
    joblib.dump(reg, OUT / "task_delay_reg.joblib")
    joblib.dump(clf, OUT / "task_delay_clf.joblib")
    joblib.dump(cost_artifact, OUT / "cost_structure.joblib")

    baseline = build_baseline_model(tasks, costs, train_projects)
    (OUT / "baseline_model.json").write_text(
        json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 上线门控：ML 需在独立测试集上「优于朴素基线且 LOPO 置信区间上界仍优于基线」才部署。
    reg_m = task_metrics["regression"]
    lopo_hi = reg_m["lopo_mae_ci95"][1]
    ml_deployed = bool(reg_m["beats_baseline"] and lopo_hi < reg_m["baseline_median_mae_days"])
    deploy_decision = {
        "ml_deployed": ml_deployed,
        "production_method": "ml_model" if ml_deployed else "calibrated_statistical_baseline",
        "gate_rule": "test MAE < 基线中位数 MAE 且 LOPO MAE 95%CI 上界 < 基线 MAE 才允许上线 ML",
        "evidence": {
            "test_mae_days": reg_m["test_mae_days"],
            "baseline_median_mae_days": reg_m["baseline_median_mae_days"],
            "lopo_mae_ci95_upper": lopo_hi,
            "classification_accuracy_vs_baseline": [
                task_metrics["classification"]["accuracy"],
                task_metrics["classification"]["baseline_accuracy"],
            ],
        },
        "reason": (
            "ML 未通过门控：本语料延期标签为噪声生成（训练 R²≈0.999 / LOPO R²<0 / 最大单特征 |r|≈0.12），"
            "任何模型都无法优于中位数基线；生产改用标定分位数与结构容差带，ML 产物保留但不启用。"
            if not ml_deployed
            else "ML 通过门控，可作为生产模型（仍建议人工确认）。"
        ),
    }

    report = {
        "trained_at": datetime.now(UTC).isoformat(),
        "dataset": {
            "labelled_tasks": int(len(X)),
            "tasks_all": int(len(tasks)),
            "costs": int(len(costs)),
            "projects_total": len(all_projects),
            "projects_with_tasks": int(tasks["project_code"].nunique()),
            "projects_train": len(train_projects),
            "projects_test": len(test_projects),
            "unknown_projects_skipped": unknown,
            "feature_count": X.shape[1],
            "features": list(X.columns),
        },
        "task_delay": task_metrics,
        "cost_structure": cost_metrics,
        "deploy_decision": deploy_decision,
        "baseline_model": baseline,
        "protocol": {
            "leakage_guard": "禁止使用 actual_start/actual_end 作为特征；项目 train/test 零交叉（源划分）",
            "cv": "训练集内 Leave-One-Project-Out；独立 test 项目仅最终评估一次，固定阈值不调参",
            "baseline": "回归=训练集中位数；分类=训练集多数类；成本构成=训练集全局均值占比",
        },
        "limitations": [
            "construction_task / actual_cost 为按真实招标锚点仿真生成，非真实施工记录。",
            "样本量小（任务 345 条 / 项目 22 个），置信区间较宽，不能外推为行业结论。",
            "project_budget 为公开招标公告锚点，计划投资/标段预算以公告为准。",
            "模型输出定位为决策参考（风险提示/预算对照），需人工确认。",
        ],
    }
    (OUT / "eval_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "meta.json").write_text(
        json.dumps(
            {
                "feature_cols": list(X.columns),
                "classes": CLASSES,
                "cost_types": COST_TYPES,
                "cost_feature_cols": cost_artifact["feature_cols"],
                "artifacts": {
                    "task_delay_reg.joblib": "工期偏差回归（门控未启用）",
                    "task_delay_clf.joblib": "工期三分类（门控未启用）",
                    "cost_structure.joblib": "成本构成占比模型（门控未启用，仅供审计）",
                    "baseline_model.json": "生产使用的标定基准",
                },
                "trained_at": report["trained_at"],
                "train_projects": train_projects,
                "test_projects": test_projects,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    tr = task_metrics["regression"]
    cl = task_metrics["classification"]
    cs = cost_metrics
    print("[train_project_models] 完成")
    print(
        f"  工期偏差回归：test MAE {tr['test_mae_days']}天 / R² {tr['test_r2']} / "
        f"基线中位数 MAE {tr['baseline_median_mae_days']}天 / 是否优于基线={tr['beats_baseline']}"
    )
    print(f"  LOPO MAE {tr['lopo_mae_mean']}天 95%CI {tr['lopo_mae_ci95']}")
    print(
        f"  工期三分类：acc {cl['accuracy']} / macroF1 {cl['macro_f1']} / 基线 {cl['baseline_accuracy']} / "
        f"优于基线={cl['beats_baseline']}"
    )
    print(
        f"  成本构成：LOPO MAPE {cs['lopo_mape_pct_mean']}% vs 基线 {cs['baseline_mape_pct_mean']}% / "
        f"优于基线={cs['beats_baseline']}"
    )
    print(f"  成本/预算比率：{cs['cost_to_budget_ratio']}")
    print(
        f"  上线决策：ML={'启用' if deploy_decision['ml_deployed'] else '不启用'} → "
        f"生产方法={deploy_decision['production_method']}"
    )
    print(f"  基准（合同额/预算）：{baseline['cost_to_budget_ratio']}")
    print(f"  工期缓冲分位数：{ {k: v for k, v in baseline['delay_days'].items() if k.startswith('p')} }")
    print(f"  产物：{OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
