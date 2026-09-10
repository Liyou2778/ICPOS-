"""PRD 功能 1.1 验收映射：设备选型与 TCO 测算。"""

from __future__ import annotations

from backend.app.models import EquipmentModel
from backend.app.services.kb import load_equipment_models
from backend.app.services.requirements import parse_requirement
from backend.app.services.selection import generate_selection

STANDARD_TEXT = (
    "我是某露天煤矿生产主管，需要制定矿山开采施工方案并配套设备："
    "年产 200 万吨矿石，工期 3 年，预算 1.5 亿元，岩石较硬。请生成方案。"
)


def _seed_models(db, kb_csv) -> None:
    if db.query(EquipmentModel).count() == 0:
        load_equipment_models(db, kb_csv)


def test_requirement_parse_complete():
    req = parse_requirement(STANDARD_TEXT)
    assert req.scene_type == "mining"
    assert req.annual_t == 2_000_000.0  # 200 万吨 -> 吨
    assert req.duration_years == 3.0
    assert req.budget_cny == 150_000_000.0  # 1.5 亿
    assert not req.missing


def test_requirement_parse_asks_followup():
    req = parse_requirement("我想买一台挖掘机")
    assert "scene_type" in req.missing or "annual" in req.missing or "budget" in req.missing
    assert req.followup_questions


def test_selection_outputs_three_bundles_with_tco(db, kb_csv):
    _seed_models(db, kb_csv)
    req = parse_requirement(STANDARD_TEXT)
    result = generate_selection(db, req)
    # 验收：30 秒内输出 ≥3 套（时间由性能门禁验证），结构断言如下
    assert len(result.bundles) >= 3
    assert 0 <= result.best_index < len(result.bundles)
    for bundle in result.bundles:
        assert bundle.fleet, "每套方案含设备配置"
        for opt in bundle.fleet:
            assert opt.model_code and opt.count >= 1 and opt.total_price_cny > 0
        # 每套方案 TCO 覆盖：购置/能耗/维保/残值 四类（合并金额口径）
        for tco in bundle.tco:
            assert tco.purchase_cny > 0 and tco.maintenance_3y_cny > 0
            assert tco.energy_3y_cny > 0 and tco.total_3y_cny > 0
            assert tco.purchase_cny - tco.residual_cny >= 0
        assert bundle.daily_capacity_t > 0
        assert bundle.tco_3y_total_cny > 0
    # 预算约束：最优方案应在预算内（fleet 购置总价 <= 预算）
    best = result.bundles[result.best_index]
    assert best.fleet_total_cny <= req.budget_cny + 1


def test_selection_equipment_params_from_db_only(db, kb_csv):
    """防幻觉第一道防线：方案数值由设备参数库直读（不得由模型生成）。"""
    _seed_models(db, kb_csv)
    req = parse_requirement(STANDARD_TEXT)
    result = generate_selection(db, req)
    db_codes = {m.code for m in db.query(EquipmentModel).all()}
    for b in result.bundles:
        for opt in b.fleet:
            assert opt.model_code in db_codes
