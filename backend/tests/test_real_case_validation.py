"""真实企业案例验证（回归门禁）：需求解析缺陷 · 规模适用性声明 · 验证集完整性。

这些用例锁定的缺陷**全部由真实案例验证发现**，不是凭空设想的边界：
  1. 平煤神马"20 台 75 吨级纯电矿卡"曾被误解析为年产 75 吨（吨级 = 设备规格，非工程量）；
  2. 白音华"总工程量 8.88 亿立方米"曾被当作年产量（总量口径需按工期折算）；
  3. 国家能源集团/平煤神马采购公告用"最高限价"而非"预算"，原先无法识别；
  4. 4.6 亿吨/年这类远超参数库规模的需求，原先会静默输出 58 挖 + 366 车的"巨型配置"而不作提示。
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from backend.app.core.config import settings
from backend.app.core.db import SessionLocal
from backend.app.models import EquipmentModel
from backend.app.schemas.domain import ParsedRequirement
from backend.app.services.requirements import parse_requirement
from backend.app.services.selection import (
    SINGLE_PROJECT_MAX_EXC,
    SINGLE_PROJECT_MAX_TRUCK,
    generate_selection,
)

from .conftest import requires_demo

CASES = settings.repo_root / "data" / "validation" / "real_cases.json"
REPORT = settings.repo_root / "data" / "validation" / "agent_validation_report.json"


# ---------------------------------------------------------------- 解析缺陷回归


def test_tonnage_spec_is_not_volume():
    """'75 吨级' 是设备规格而不是工程量，不得被解析为年产量。"""
    req = parse_requirement(
        "我是平煤神马建工集团的设备采购负责人，需要采购 20 台 75 吨级非公路纯电动矿用自卸车"
        "用于露天矿剥离运输，最高限价 2800 万元，要求合同签订后 30 天内交货"
    )
    assert req.annual_t == 0.0, f"吨级规格被误当作工程量：{req.annual_t}"
    assert "annual" in req.missing
    assert req.scene_type == "mining"
    assert "电动化优先" in req.constraints


def test_total_volume_divided_by_duration():
    """'总工程量' 是全周期总量，必须按工期折算年产量与总量。"""
    req = parse_requirement(
        "我们是国家电投白音华矿区剥离工程，工期 5 年（2026—2030 年），"
        "总工程量 8.88 亿立方米露天剥离，预算 78 亿元，要求无人驾驶设备不低于 240 台"
    )
    total_expected = 8.88e8 * 2.6
    assert req.total_t == pytest.approx(total_expected, rel=1e-6)
    assert req.annual_t == pytest.approx(total_expected / 5, rel=1e-6)
    assert req.daily_t == pytest.approx(req.annual_t / 330.0, rel=1e-6)
    assert req.duration_years == 5.0
    assert req.budget_cny == 7.8e9


def test_tender_total_volume_phrasing():
    """招标原件写法'剥离总量 17239.2 万立方米 + 工期 5 年'同样必须按总量折算。

    该措辞来自白音华一标段资格预审公告原文（真实招标文件用词），
    此前解析器只认'总工程量/工程量'，会把 5 年总量当成一年产量（差 5 倍）。
    """
    req = parse_requirement(
        "白音华露天矿 2026—2030 年剥离工程一标段（5 年），剥离总量 17239.2 万立方米，"
        "露天煤矿土岩剥离的采装、运输、排卸，工期 5 年，加权运距 4.37 千米"
    )
    total_m3 = 17239.2 * 1e4
    assert req.total_t == pytest.approx(total_m3 * 2.6, rel=1e-6)
    assert req.annual_t == pytest.approx(total_m3 * 2.6 / 5, rel=1e-6)
    assert req.duration_years == 5.0


def test_annual_volume_still_scales_to_total():
    """年产量口径行为不变：总量 = 年产量 × 工期。"""
    req = parse_requirement("年产 200 万吨矿石，工期 3 年，预算 1.5 亿元，露天煤矿")
    assert req.annual_t == 2_000_000.0
    assert req.total_t == 6_000_000.0
    assert not req.missing


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("最高限价 2800 万元", 28_000_000.0),
        ("预算 1.5 亿元", 150_000_000.0),
        ("计划投资 3.2 亿元", 320_000_000.0),
        ("合同额 78 亿元", 7_800_000_000.0),
    ],
)
def test_budget_synonyms(text, expected):
    """采购/招标公告常用'最高限价/计划投资/合同额'，必须与'预算'等价识别。"""
    req = parse_requirement(f"露天矿剥离工程，年产 300 万吨，工期 3 年，{text}")
    assert req.budget_cny == pytest.approx(expected, rel=1e-6)
    assert "budget" not in req.missing


# ---------------------------------------------------------------- 规模适用性声明


@requires_demo
def test_oversize_demand_declares_scope_limit():
    """需求规模超参数库单项目上限时，必须显式声明适用范围（不得静默给出巨型配置）。"""
    db = SessionLocal()
    try:
        catalog_n = db.scalar(select(EquipmentModel.id).limit(1))
        if catalog_n is None:
            pytest.skip("设备参数库为空：请先执行 scripts.build_kb")
        req = ParsedRequirement(
            scene_type="mining",
            scene_cn="露天矿山开采",
            annual_t=8.88e8 * 2.6 / 5,  # 年剥离量约 4.6 亿吨（白音华 2026—2030 案例）
            duration_years=5.0,
            budget_cny=7.8e9,
            raw="白音华剥离工程",
        )
        req.daily_t = req.annual_t / 330
        req.total_t = req.annual_t * req.duration_years
        res = generate_selection(db, req)
    finally:
        db.close()

    warning = [a for a in res.assumptions if a.startswith("⚠️")]
    assert warning, f"超范围需求未声明适用范围：{res.assumptions}"
    assert "适用范围" in warning[0]

    best = res.bundles[res.best_index]
    n_exc = max((f.count for f in best.fleet if "挖掘机" in f.model_name), default=0)
    n_truck = max((f.count for f in best.fleet if "自卸车" in f.model_name), default=0)
    assert n_exc > SINGLE_PROJECT_MAX_EXC or n_truck > SINGLE_PROJECT_MAX_TRUCK, (
        f"该案例应触发超范围：{n_exc} 挖 / {n_truck} 车"
    )
    assert "超出适用范围" in best.note
    assert "不得直接用于报价" in best.note


@requires_demo
def test_normal_demand_has_no_scope_warning():
    """正常规模需求不应出现超范围警告（避免噪声式免责声明）。"""
    db = SessionLocal()
    try:
        if db.scalar(select(EquipmentModel.id).limit(1)) is None:
            pytest.skip("设备参数库为空")
        req = ParsedRequirement(
            scene_type="mining",
            scene_cn="露天矿山开采",
            annual_t=2_000_000.0,
            duration_years=3.0,
            budget_cny=150_000_000.0,
            raw="年产200万吨",
        )
        req.daily_t = req.annual_t / 330
        req.total_t = req.annual_t * req.duration_years
        res = generate_selection(db, req)
    finally:
        db.close()
    assert not [a for a in res.assumptions if a.startswith("⚠️")]
    assert "超出适用范围" not in res.bundles[res.best_index].note


# ---------------------------------------------------------------- 验证集与报告门禁


def test_real_case_dataset_integrity():
    """验证集本身必须可溯源、口径已声明、数字与探针文本一致。"""
    data = json.loads(CASES.read_text(encoding="utf-8"))
    assert len(data["cases"]) >= 3, "真实案例验证集至少需 3 个案例"
    allowed_tiers = {
        "official_tender",
        "official_media",
        "industry_media",
        "secondary",
        "industry_media + secondary",
        # 招标原件全文转载（权威原文位于需注册/会员的平台，转载页内容详实且内部自洽）
        "tender_aggregator_fulltext",
    }
    for case in data["cases"]:
        for field in (
            "id",
            "title",
            "customer",
            "source_name",
            "source_url",
            "source_tier",
            "requirement",
            "result",
            "agent_probe_text",
            "comparability",
        ):
            assert case.get(field), f"{case.get('id')} 缺少 {field}"
        assert case["source_tier"] in allowed_tiers
        assert case["source_url"].startswith("http")
        # 三个关键槽位（年作业量/工期/预算）必须标注来源：published / derived / not_published
        prov = case["requirement"].get("provenance")
        assert prov, f"{case['id']} 缺少 provenance（无法区分公告原文、派生值与测试值）"
        for key in ("annual", "duration", "budget"):
            assert key in prov, f"{case['id']} provenance 缺少 {key}"
            assert prov[key].split("：")[0] in {"published", "derived", "not_published"}, (
                f"{case['id']}.{key} 来源类型非法：{prov[key]}"
            )
        if prov["budget"].startswith("not_published"):
            assert case["requirement"].get("budget_cny") is None, (
                f"{case['id']} 预算未披露却填了具体值（禁止用测试值充当公告字段）"
            )
        # 探针文本不得篡改公告数字：**仅对公告披露（published）的槽位**做文本强校验；
        # 派生值（derived，如总量 ÷ 工期）与未披露值不做文本比对——由 provenance 显式声明口径，
        # 避免把"派生数字"当成"公告原文数字"对外表述。
        text = case["agent_probe_text"]

        def _cands(value: float, unit: str) -> list[str]:
            if unit == "m3":
                return [f"{value:,.0f}", f"{value:.0f}", f"{value / 1e4:g}", f"{value / 1e8:g}"]
            if unit == "cny":
                return [f"{value / 1e8:g}", f"{value / 1e4:g}", f"{value:.0f}"]
            return [f"{value:g}"]

        for slot, unit in (("annual_m3", "m3"), ("duration_years", "year"), ("budget_cny", "cny")):
            key = {"annual_m3": "annual", "duration_years": "duration", "budget_cny": "budget"}[slot]
            if prov[key].startswith("not_published"):
                assert case["requirement"].get(slot) is None, f"{case['id']}.{slot} 未披露却填了值"
                continue
            if prov[key].startswith("published"):
                value = case["requirement"].get(slot)
                assert value is not None, f"{case['id']}.{slot} 标注已披露但值为空"
                cands = _cands(float(value), unit)
                assert any(c in text for c in cands), (
                    f"{case['id']} 探针文本未包含公告披露的 {slot}={value}（候选 {cands}）"
                )


def test_validation_report_gates_all_pass():
    """报告存在时，各维度门禁必须全绿（防止改代码后不重跑验证）。"""
    if not REPORT.exists():
        pytest.skip("验证报告不存在：请先执行 scripts.validate_agent_with_real_cases")
    rep = json.loads(REPORT.read_text(encoding="utf-8"))
    s = rep["summary"]
    assert s["parse_pass"] == s["cases"], f"需求解析未全通过：{s}"
    assert s["config_pass"] == s["config_applicable"], f"配置校验未全通过：{s}"
    assert s["blocking_pass"] == s["cases"], f"阻塞/放行行为未全通过：{s}"
    assert s["scale_honesty_pass"] == s["scale_honesty_applicable"], f"规模诚实性未全通过：{s}"
    for r in rep["results"]:
        assert r["source_url"].startswith("http"), f"{r['id']} 缺少可溯源链接"


def test_mandatory_spec_check_is_wired_and_gap_disclosed():
    """业主硬性门槛校验必须已接入，且当前的能力缺口必须在报告中公开披露。

    说明：招标文件门槛不满足即不响应（废标）。当前参数库最大挖掘机 6 m³ < 真实标段要求的 7 m³，
    产品尚未实现"硬性门槛校验与无适配机型提示"——本用例的作用是**锁定这个缺口已被公开记录**，
    避免文档说"已满足招标要求"而系统实际会推荐不合规机型。修复该功能后本用例需同步更新。
    """
    data = json.loads(CASES.read_text(encoding="utf-8"))
    tender_cases = [c for c in data["cases"] if c["requirement"].get("owner_mandatory_specs")]
    assert tender_cases, "验证集必须包含带业主强制设备门槛的招标原件案例"
    specs = tender_cases[0]["requirement"]["owner_mandatory_specs"]
    assert specs["excavator"]["bucket_m3_min"] >= 7, "该案例门槛记录有误"

    if not REPORT.exists():
        pytest.skip("验证报告不存在")
    rep = json.loads(REPORT.read_text(encoding="utf-8"))
    s = rep["summary"]
    assert "mandatory_specs_applicable" in s, "报告缺少硬性门槛校验维度"
    assert s["mandatory_specs_applicable"] >= 1, "硬性门槛校验未对任何案例生效"
    if s["mandatory_specs_pass"] < s["mandatory_specs_applicable"]:
        # 缺口存在 → 必须在交付文档中公开，且违规明细可查
        assert s["mandatory_specs_violations"], "存在门槛违规却未记录明细"
        doc = (settings.repo_root / "docs" / "validation" / "agent_real_case_validation.md").read_text(
            encoding="utf-8"
        )
        assert "硬性门槛" in doc, "能力缺口未在验证报告中公开"
        assert "7 m³" in doc or "7 m3" in doc, "报告中未写明具体门槛数值"
