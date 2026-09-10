"""PRD 功能 2.2 验收映射：多机协同动态调度（启发式 + 重调度 + A/B 对比）。"""

from __future__ import annotations

import uuid

from backend.app.models import Device, EquipmentModel, Project
from backend.app.services.dispatch import PIT, dispatch_service
from backend.app.services.kb import load_equipment_models


def _seed_site(db, kb_csv) -> tuple[str, list[Device]]:
    """每个用例唯一编码，保证临时库隔离。"""
    load_equipment_models(db, kb_csv)
    tag = uuid.uuid4().hex[:6]
    proj = Project(
        code=f"P-{tag}",
        name="测试矿山",
        scene_type="mining",
        work_volume=100,
        work_volume_unit="万吨",
        duration_days=30,
        status="active",
    )
    db.add(proj)
    db.flush()
    exc = db.query(EquipmentModel).filter(EquipmentModel.code == "XE700").first()
    truck = db.query(EquipmentModel).filter(EquipmentModel.code == "XDR90").first()
    assert exc and truck
    devices = [
        Device(
            code=f"E{tag}A",
            model_id=exc.id,
            name="挖掘机A",
            project_id=proj.id,
            work_state="working",
            lat=PIT["lat"],
            lng=PIT["lng"],
        ),
        Device(
            code=f"E{tag}B",
            model_id=exc.id,
            name="挖掘机B",
            project_id=proj.id,
            work_state="working",
            lat=PIT["lat"] + 0.001,
            lng=PIT["lng"],
        ),
    ]
    for i in range(1, 5):
        devices.append(
            Device(
                code=f"T{tag}{i}",
                model_id=truck.id,
                name=f"矿卡{i}",
                project_id=proj.id,
                work_state="working",
                lat=PIT["lat"] + i * 0.002,
                lng=PIT["lng"],
                cur_load_t=0,
            )
        )
    db.add_all(devices)
    db.commit()
    return tag, devices


def test_dispatch_plan_with_reasons(db, kb_csv):
    tag, devs = _seed_site(db, kb_csv)
    out = dispatch_service.run(db, trigger="initial")
    assert out.plan_id > 0
    assert len(out.assignments) >= 1
    # 派单附可解释原因（距离/载重/拥堵/优先级 ≥4 类信息）
    for a in out.assignments:
        assert any(k in a.reason for k in ("距离", "载重", "拥堵", "优先级"))
        assert a.device_code.startswith("T")
    assert out.stats["idle_rate"] < 0.30
    assert out.is_suggestion is True  # 调度结论为“建议执行”


def test_dispatch_replan_excludes_fault_device(db, kb_csv):
    tag, devs = _seed_site(db, kb_csv)
    truck_codes = [d.code for d in devs if d.code.startswith("T")]
    # 故障设备不参与重调度（冻结原计划 -> 剩余设备重新求解）
    out = dispatch_service.run(db, trigger="fault", fault_device_code=truck_codes[0])
    codes = [a.device_code for a in out.assignments]
    assert truck_codes[0] not in codes
    assert len(out.assignments) >= 2


def test_dispatch_confirm_issues_orders(db, kb_csv):
    _seed_site(db, kb_csv)
    plan = dispatch_service.run(db, trigger="initial")
    out = dispatch_service.confirm(db, plan.plan_id)
    assert out.stats is not None
    from backend.app.models import DispatchOrder

    assert db.query(DispatchOrder).filter(DispatchOrder.plan_id == plan.plan_id).count() >= 1


def test_dispatch_ab_idle_drop_over_15pct(db, kb_csv):
    """PRD：矿山场景空载率相对人工调度基线下降 ≥15%。"""
    _seed_site(db, kb_csv)
    report = dispatch_service.ab_compare(db)
    assert report.improvements["idle_rate_drop"] >= 0.15
    assert report.metrics["ai_idle_rate"] < report.metrics["manual_idle_rate"]
    assert report.conclusion
