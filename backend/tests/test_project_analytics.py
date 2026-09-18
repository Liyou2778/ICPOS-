"""项目运营验收：招标锚点 / 成本构成与容差判定 / 预算执行预警 / 成本测算 / 工期缓冲 / 上线门控。

覆盖点（企业级要求）：
  * 数据口径与边界：锚点为 real、台账为 simulated，二者在响应中可区分；
  * 契约稳定性：字段齐全、类型正确、错误码正确（404/400/409）；
  * 方法诚实性：ML 未通过门控时接口必须显式声明使用标定基准，不得谎称预测；
  * 数值一致性：占比合计≈100%、金额与数据库台账一致、区间上下界有序。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from backend.app.main import app
from backend.app.models.project import Project, ProjectCost
from backend.app.core.config import settings

from .conftest import requires_demo

pytestmark = requires_demo

# 项目运营标定基准（data/models/project/baseline_model.json）已按"以新全域语料重建训练"的计划清除
# 并备份到 data/_backup_*/project/；标定基准缺失时接口按设计返回 409（拒绝给无依据的结论），
# 因此本文件的接口用例在基准缺失时显式跳过，而不是放宽断言。
BASELINE = settings.repo_root / "data" / "models" / "project" / "baseline_model.json"
RETIRED_REASON = (
    "项目运营标定基准已清除（备份于 data/_backup_*/project/）：接口按设计返回 409；"
    "新链路为 scripts.ingest_unified_corpus + scripts.train_ops_models"
)


@pytest.fixture(scope="module", autouse=True)
def _require_baseline():
    if not BASELINE.exists():
        pytest.skip(RETIRED_REASON)


@pytest.fixture(scope="module")
def c():
    with TestClient(app) as client:
        yield client


@pytest.fixture(scope="module")
def a_project_code() -> str:
    """取一个同时具备招标锚点与成本台账的真实项目编号。"""
    from backend.app.core.db import SessionLocal

    s = SessionLocal()
    try:
        code = s.scalar(
            select(Project.code)
            .join(ProjectCost, ProjectCost.project_id == Project.id)
            .where(Project.section_est_total_yuan > 0)
            .limit(1)
        )
    finally:
        s.close()
    if not code:
        pytest.skip("数据库无项目语料：请先执行 ingest_project_corpus + train_project_models")
    return code


def test_model_report_has_deploy_gate_evidence(c):
    rep = c.get("/api/projects/analytics/model-report")
    assert rep.status_code == 200
    body = rep.json()
    for key in ("dataset", "task_delay", "cost_structure", "protocol", "deploy_decision", "limitations"):
        assert key in body, f"评估报告缺少 {key}"
    dep = body["deploy_decision"]
    assert dep["ml_deployed"] in (True, False)
    # 门控必须给出证据与理由，不允许空口结论
    assert dep["evidence"]["baseline_median_mae_days"] is not None
    assert len(dep["reason"]) > 10
    # 未通过门控时生产方法必须是标定基准
    if not dep["ml_deployed"]:
        assert dep["production_method"] == "calibrated_statistical_baseline"
        assert body["task_delay"]["regression"]["beats_baseline"] is False


def test_summary_contract_and_internal_consistency(c):
    s = c.get("/api/projects/analytics/summary")
    assert s.status_code == 200
    b = s.json()
    assert b["projects"]["total"] >= 1
    # 成本构成占比合计应在 100% 附近（四舍五入误差 0.5pp 内）
    shares = [x["share_pct"] for x in b["cost"]["by_type"]]
    if shares:
        assert abs(sum(shares) - 100) < 0.5
        assert all(0 <= x <= 100 for x in shares)
        assert len({x["cost_type"] for x in b["cost"]["by_type"]}) == len(b["cost"]["by_type"])
    # 预算执行区间必须有序，且与上线决策一致
    band = b["budget_execution_band"]
    if band:
        assert band["p25"] <= band["p50"] <= band["p75"] <= band["p90"]
    assert b["deploy"]["production_method"] in ("ml_model", "calibrated_statistical_baseline")
    assert b["data_boundary"], "必须随看板返回数据边界声明"


def test_tender_anchor_is_real_data_and_404(c, a_project_code):
    r = c.get(f"/api/projects/analytics/tender-anchor/{a_project_code}")
    assert r.status_code == 200
    a = r.json()
    assert a["code"] == a_project_code
    assert a["data_type"] == "real", "招标锚点必须标记为真实公开数据"
    assert a["plan_invest_yuan"] > 0 and a["section_est_total_yuan"] > 0
    assert a["source_site"], "必须带数据来源（可追溯）"
    assert c.get("/api/projects/analytics/tender-anchor/NOT-A-CODE").status_code == 404


def test_cost_structure_matches_ledger_and_band_logic(c, a_project_code):
    from backend.app.core.db import SessionLocal

    r = c.get(f"/api/projects/analytics/cost-structure/{a_project_code}")
    assert r.status_code == 200
    cs = r.json()
    assert len(cs["items"]) == 5
    assert abs(sum(i["share_pct"] for i in cs["items"]) - 100) < 0.5

    s = SessionLocal()
    try:
        pid = s.scalar(select(Project.id).where(Project.code == a_project_code))
        total = s.scalar(select(func.sum(ProjectCost.amount)).where(ProjectCost.project_id == pid))
    finally:
        s.close()
    assert abs(cs["total_cost_yuan"] - float(total)) < 0.01, "接口金额必须与台账一致（防幻觉第一道防线）"

    for item in cs["items"]:
        lo, hi = item["band_pct"]
        assert lo <= item["baseline_mean_pct"] <= hi
        if item["verdict"] == "正常区间":
            assert lo <= item["share_pct"] <= hi
        else:
            assert item["share_pct"] < lo or item["share_pct"] > hi
            assert item["cost_type"] in cs["out_of_band"]
    assert cs["disclaimer"] and cs["basis"]


def test_cost_forecast_bounds_and_validation(c):
    r = c.post(
        "/api/projects/analytics/cost-forecast",
        json={"section_est_total_yuan": 50_000_000, "duration_days": 200},
    )
    assert r.status_code == 200
    f = r.json()
    tc = f["total_cost"]
    assert tc["range_yuan"][0] <= tc["expected_yuan"] <= tc["range_yuan"][1] <= tc["worst_case_p90_yuan"]
    assert sum(i["expected_yuan"] for i in f["items"]) == pytest.approx(tc["expected_yuan"], rel=0.01)
    assert "非 ML 预测" in f["method"] or "非 ML" in f["method"]
    assert f["disclaimer"]
    assert (
        c.post("/api/projects/analytics/cost-forecast", json={"section_est_total_yuan": 0}).status_code == 422
    )
    assert (
        c.post("/api/projects/analytics/cost-forecast", json={"section_est_total_yuan": -1}).status_code
        == 422
    )


def test_task_delay_risk_reports_distribution_not_prediction(c):
    r = c.post(
        "/api/projects/analytics/task-delay-risk",
        json={"process": "hauling", "plan_days": 12, "workload": 800},
    )
    assert r.status_code == 200
    b = r.json()
    ref = b["reference"]
    assert ref["p50_days"] <= ref["p80_days"] <= ref["p90_days"] <= ref["p95_days"]
    assert b["suggestion"]["recommended_finish_days"] == pytest.approx(
        12 + b["suggestion"]["buffer_days_p80"], abs=0.2
    )
    assert "分位数" in b["method"]
    assert b["why_not_ml"], "必须说明为何不用 ML（可审计）"
    # 未知名工序回退到全工序基准，而不是报错或编造
    r2 = c.post("/api/projects/analytics/task-delay-risk", json={"process": "not-a-process", "plan_days": 10})
    assert r2.status_code == 200
    assert r2.json()["reference"]["scope"] == "overall"


def test_project_list_matches_db_counts(c):
    r = c.get("/api/projects/analytics/projects")
    assert r.status_code == 200
    rows = r.json()["projects"]
    from backend.app.core.db import SessionLocal

    s = SessionLocal()
    try:
        n = s.scalar(select(func.count(Project.id)))
    finally:
        s.close()
    assert len(rows) == n
    for p in rows:
        if p["cost_to_budget_ratio"] is not None:
            assert p["section_est_total_yuan"] > 0
            # 接口按 4 位小数返回比率（展示口径），按同精度比较，避免浮点尾差误判
            assert p["cost_to_budget_ratio"] == pytest.approx(
                round(p["total_cost_yuan"] / p["section_est_total_yuan"], 4), abs=1e-9
            )


def test_project_intent_routing_and_db_only_numbers(c, a_project_code):
    """项目类问题必须路由到 project 智能体，且答复中的数字来自数据库直读。"""
    from backend.app.core.db import SessionLocal

    sid = c.post("/api/chat/sessions", json={"title": "项目问答验收"}).json()["session_id"]
    r = c.post(
        f"/api/chat/sessions/{sid}/messages",
        json={"message": f"{a_project_code} 这个项目的招标人和标段预算是多少？"},
    ).json()
    assert r["agent"] == "project", f"项目类问题应路由 project，实际 {r['agent']}"

    s = SessionLocal()
    try:
        p = s.scalar(select(Project).where(Project.code == a_project_code))
        est = p.section_est_total_yuan
        tenderer = p.tenderer
    finally:
        s.close()
    assert tenderer in r["content"]
    # 标段预算必须原样出现（元 或 万元/亿元 换算之一），不得由大模型改写数值
    assert (
        f"{est / 1e4:.2f}" in r["content"]
        or f"{est / 1e8:.4f}" in r["content"]
        or f"{est:.0f}" in r["content"]
    )


def test_project_question_without_anchor_returns_portfolio(c):
    sid = c.post("/api/chat/sessions", json={"title": "项目组合问答"}).json()["session_id"]
    r = c.post(
        f"/api/chat/sessions/{sid}/messages", json={"message": "项目库里有哪些项目？标段预算合计多少？"}
    ).json()
    assert r["agent"] == "project"
    assert "项目" in r["content"]
