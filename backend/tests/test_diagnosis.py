"""PRD 功能 3.2 验收映射：故障智能诊断与维修工单自动生成。"""

from __future__ import annotations

from backend.app.models import Device, EquipmentModel, FaultCode
from backend.app.services.diagnosis import create_workorder, diagnose_code, diagnose_text
from backend.app.services.kb import load_fault_codes


def _seed(db, fault_csv):
    load_fault_codes(db, fault_csv)
    if db.query(FaultCode).count() > 0:
        return
    db.flush()


def _device(db) -> Device:
    model = db.query(EquipmentModel).filter(EquipmentModel.code == "XE700").first()
    if model is None:
        model = EquipmentModel(
            code="XE700",
            model_name="测试型号",
            category="excavator",
            price_cny=1,
            bucket_m3=1,
            fuel_lh=1,
            maintain_yearly_cny=1,
        )
        db.add(model)
        db.flush()
    d = Device(code="T-TEST", model_id=model.id, name="测试矿卡", work_state="working")
    db.add(d)
    db.commit()
    db.refresh(d)
    return d


def test_diagnose_code_top3(db, fault_csv):
    _seed(db, fault_csv)
    r = diagnose_code(db, "HYD-01")
    assert r.code == "HYD-01"
    assert len(r.top3) >= 3  # 验收：输出 ≥3 个诊断结果
    assert r.top3[0].confidence > r.top3[1].confidence > r.top3[2].confidence
    assert r.fix_plan and r.engineer


def test_diagnose_text_hydraulic(db, fault_csv):
    _seed(db, fault_csv)
    r = diagnose_text(db, "挖掘机液压油温高，动作没劲，怀疑漏油")
    assert len(r.top3) >= 1
    assert r.top3[0].category == "HYD" or r.top3[0].code == "HYD-01"


def test_diagnose_unknown_answers_honestly(db, fault_csv):
    """防幻觉：知识库外问题明确拒答而非编造。"""
    _seed(db, fault_csv)
    r = diagnose_text(db, "量子纠缠导致挖掘机时空穿越怎么办")
    assert r.top3[0].code == "UNKNOWN" or r.top3[0].confidence < 0.1
    assert "人工" in r.engineer or "转人工" in r.fix_plan


def test_workorder_six_fields(db, fault_csv):
    _seed(db, fault_csv)
    dev = _device(db)
    r = diagnose_code(db, "HYD-01")
    wo = create_workorder(db, dev.code, r)
    assert wo.code.startswith("WO-")
    # 六项信息：故障描述/诊断/方案/备件/预计时长/推荐工程师
    assert wo.fault_desc and wo.diagnosis and wo.fix_plan
    assert wo.parts and wo.est_hours > 0 and wo.engineer
    assert any("采购" in (p.get("action") or "") for p in wo.parts)  # 缺货自动采购建议
