"""项目运营模型产物验收：泄漏防护 · 指标门槛 · 上线门控一致性 · 基准标定合理性。

企业级要求（对齐目标 (2)(4)）：
  * **泄漏防护**：特征清单不得包含 actual_start / actual_end / status 等标签来源字段；
    报告中 train/test 项目集合必须零交叉。
  * **协议完整**：LOPO 交叉验证、独立测试集、朴素基线对照、置信区间、样本量与限制声明齐备。
  * **门控自洽**：ml_deployed 必须严格等价于"测试集优于基线 且 LOPO 置信区间上界优于基线"，
    防止有人事后手改结论而不改证据。
  * **标定合理**：分位数单调有序、结构容差带包含均值、产出文件齐全可复现。
"""

from __future__ import annotations

import json

import joblib
import pytest

from backend.app.core.config import settings

MODEL_DIR = settings.repo_root / "data" / "models" / "project"
FORBIDDEN_FEATURES = ("actual_start", "actual_end", "status", "delay", "progress")


@pytest.fixture(scope="module")
def report() -> dict:
    f = MODEL_DIR / "eval_report.json"
    if not f.exists():
        pytest.skip("模型产物缺失：请先执行 python -m scripts.train_project_models")
    return json.loads(f.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def baseline() -> dict:
    f = MODEL_DIR / "baseline_model.json"
    if not f.exists():
        pytest.skip("基准文件缺失：请先执行 python -m scripts.train_project_models")
    return json.loads(f.read_text(encoding="utf-8"))


def test_artifacts_exist_and_loadable():
    for name in (
        "task_delay_reg.joblib",
        "task_delay_clf.joblib",
        "cost_structure.joblib",
        "meta.json",
        "eval_report.json",
        "baseline_model.json",
    ):
        assert (MODEL_DIR / name).exists(), f"缺少产物 {name}"
    reg = joblib.load(MODEL_DIR / "task_delay_reg.joblib")
    clf = joblib.load(MODEL_DIR / "task_delay_clf.joblib")
    cost = joblib.load(MODEL_DIR / "cost_structure.joblib")
    assert hasattr(reg, "predict") and hasattr(clf, "predict_proba")
    # 成本模型产物必须自带门控说明，避免被误当作生产模型使用
    assert hasattr(cost["model"], "predict")
    assert cost["cost_types"] == ["人工", "材料", "机械", "其他", "管理"]
    assert "门控关闭" in cost["note"]
    meta = json.loads((MODEL_DIR / "meta.json").read_text(encoding="utf-8"))
    assert meta["classes"] == ["提前", "准时", "延期"]
    assert meta["cost_types"] == ["人工", "材料", "机械", "其他", "管理"]
    assert meta["feature_cols"], "特征清单不得为空"
    assert "cost_structure.joblib" in meta["artifacts"]


def test_no_label_leakage_in_features(report):
    feats = report["dataset"]["features"]
    hit = [f for f in feats if any(bad in f for bad in FORBIDDEN_FEATURES)]
    assert not hit, f"特征中存在标签来源字段（严重泄漏）：{hit}"
    assert "leakage_guard" in report["protocol"]
    assert "actual" in report["protocol"]["leakage_guard"]


def test_train_test_projects_disjoint(report, baseline):
    tr = set(report["dataset"].get("train_projects") or baseline.get("calibration_projects") or [])
    te = set(report["task_delay"]["regression"]["test_projects"])
    assert tr and te, "训练/测试项目集合不得为空"
    assert not (tr & te), f"训练与测试项目交叉，评估无效：{sorted(tr & te)}"
    assert report["task_delay"]["regression"]["n_train"] > 0
    assert report["task_delay"]["regression"]["n_test"] > 0


def test_metrics_completeness_and_samples(report):
    reg = report["task_delay"]["regression"]
    for k in (
        "test_mae_days",
        "test_rmse_days",
        "test_r2",
        "test_mape_pct",
        "baseline_median_mae_days",
        "lopo_mae_mean",
        "lopo_mae_ci95",
    ):
        assert k in reg, f"回归指标缺少 {k}"
    lo, hi = reg["lopo_mae_ci95"]
    assert lo <= reg["lopo_mae_mean"] <= hi, "置信区间必须包含点估计"
    cls = report["task_delay"]["classification"]
    assert cls["classes"] == ["提前", "准时", "延期"]
    assert len(cls["confusion_matrix"]) == 3 and all(len(r) == 3 for r in cls["confusion_matrix"])
    assert 0 <= cls["accuracy"] <= 1 and 0 <= cls["macro_f1"] <= 1
    assert report["limitations"], "必须随报告声明数据边界与限制"
    assert any("仿真" in x or "simulated" in x for x in report["limitations"])


def test_deploy_gate_is_consistent_with_evidence(report):
    """门控结论必须由证据推导，禁止结论与证据不一致。"""
    dep = report["deploy_decision"]
    reg = report["task_delay"]["regression"]
    expected = bool(reg["beats_baseline"] and reg["lopo_mae_ci95"][1] < reg["baseline_median_mae_days"])
    assert dep["ml_deployed"] is expected, (
        f"门控结论与证据不一致：ml_deployed={dep['ml_deployed']}, 证据推导={expected}"
    )
    if not expected:
        assert dep["production_method"] == "calibrated_statistical_baseline"
        assert "未通过门控" in dep["reason"]
        assert report["baseline_model"], "未上线 ML 时必须提供标定基准作为生产方案"
    assert dep["gate_rule"]


def test_baseline_calibration_is_monotonic(baseline):
    d = baseline["delay_days"]
    assert d["p50"] <= d["p80"] <= d["p90"] <= d["p95"], f"分位数必须单调：{d}"
    assert d["p50"] >= 0, "工期偏差分位数不应为负（按业务定义截断为 0）"
    for proc, ref in d["by_process"].items():
        assert ref["p50"] <= ref["p80"] <= ref["p90"] <= ref["p95"], f"{proc} 分位数乱序"
        assert ref["n"] >= 8, f"{proc} 样本过少不应单列基准"

    for ctype, s in baseline["cost_structure_pct"].items():
        assert s["p25_pct"] <= s["mean_pct"] <= s["p75_pct"], f"{ctype} 容差带未包含均值"
        assert s["std_pct"] >= 0 and s["cv_pct"] >= 0
    r = baseline["cost_to_budget_ratio"]
    assert r["p25"] <= r["p50"] <= r["p75"] <= r["p90"], f"预算执行分位数乱序：{r}"


def test_cost_model_reported_honestly(report):
    cs = report["cost_structure"]
    assert cs["cost_type_order"] == ["人工", "材料", "机械", "其他", "管理"]
    assert set(cs["global_share_baseline"]) == set(cs["cost_type_order"])
    total = sum(cs["global_share_baseline"].values())
    assert abs(total - 1.0) < 0.02, f"基准占比合计应≈100%：{total}"
    for k in ("lopo_mape_pct_mean", "baseline_mape_pct_mean", "beats_baseline"):
        assert k in cs, f"成本模型缺少 {k}"
    if not cs["beats_baseline"]:
        # 成本构成模型未跑赢基线时，生产侧必须回退标定基准（不得直接使用该模型输出）
        assert report["deploy_decision"]["production_method"] == "calibrated_statistical_baseline"
        ht = cs.get("holdout_test")
        if ht:
            assert ht["model_mape_pct"] > ht["baseline_mape_pct"] or ht["n_test_projects"] < 5, (
                "LOPO 未优于基线但独立测试集显著优于基线时，需重新评估并更新门控结论"
            )
