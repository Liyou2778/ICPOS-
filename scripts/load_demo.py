"""演示业务库装载：把模拟数据（设备/时序末态/故障事件）装配进业务库供 API 使用。

幂等语义：每次执行会先清空演示业务表（派单/工单/预警/维保/任务进度），再按
模拟数据重建，保证可重复装载、状态确定（开发与彩排常用）。
注意：会重置演示设备/项目相关的业务记录（不会删除知识库与账号）。

前置：先执行 build_kb（型号/FaultCode 入库）与 data.simulator.gen。
用法（仓库根目录）：uv run python -m scripts.load_demo
"""

from __future__ import annotations

import pandas as pd

from backend.app.core.config import settings
from backend.app.core.db import SessionLocal, init_db
from backend.app.core.security import seed_demo_users
from backend.app.models import (
    Customer,
    Device,
    DispatchLog,
    DispatchOrder,
    DispatchPlan,
    EquipmentModel,
    FaultCode,
    MaintenancePlan,
    Project,
    Task,
    TaskProgress,
    Warning,
    WorkOrder,
)

SIM = settings.repo_root / "data" / "simulated"
PART_BY_CODE = {  # 预警五要素的“异常部件/建议措施”映射（示例）
    "ENG-03": ("冷却系统/水温传感器", "立即检查冷却液并停机降温，避免拉缸"),
    "HYD-01": ("液压油/散热系统", "停机清洗散热器并检查液压油位，防止液压泵烧蚀"),
    "ELE-04": ("控制器/CAN总线", "检查接插件与线束，必要时更换控制器"),
}
SEVERITY = {"ENG-03": "H", "HYD-01": "H", "ELE-04": "H"}


def run() -> dict:
    init_db()
    db = SessionLocal()
    try:
        # 0) 幂等重置：清空演示业务表（依赖顺序：工单/预警/维保 -> 派单 -> 进度）
        db.query(DispatchOrder).delete()
        db.query(DispatchPlan).delete()
        db.query(DispatchLog).delete()
        db.query(WorkOrder).delete()
        db.query(Warning).delete()
        db.query(MaintenancePlan).delete()
        db.query(TaskProgress).delete()
        db.commit()
        # 1) 客户与项目
        cust = db.query(Customer).filter(Customer.code == "C-MINING-001").first()
        if cust is None:
            cust = Customer(
                code="C-MINING-001",
                name="北方矿业集团有限公司",
                industry="mining",
                contact="张矿长",
                service_level="enterprise",
            )
            db.add(cust)
            db.flush()
        proj = db.query(Project).filter(Project.code == "P-MINING-001").first()
        if proj is None:
            proj = Project(
                code="P-MINING-001",
                name="鄂尔多斯某露天煤矿年产200万吨开采项目",
                customer_id=cust.id,
                scene_type="mining",
                work_volume=200,
                work_volume_unit="万吨",
                duration_days=30,
                budget_cny=150_000_000,
                terrain="低山丘陵，岩性砂岩夹泥岩",
                quality_std="块度≤1m，大块率≤5%",
                progress_pct=0.62,
                status="active",
            )
            db.add(proj)
            db.flush()
            for i, (process, cn, days) in enumerate(
                [
                    ("drilling", "穿孔", (1, 28)),
                    ("blasting", "爆破", (1, 28)),
                    ("loading", "铲装", (1, 30)),
                    ("hauling", "运输", (1, 30)),
                    ("dumping", "排土", (2, 30)),
                ]
            ):
                db.add(
                    Task(
                        project_id=proj.id,
                        code=f"T{i + 1}",
                        process=process,
                        process_cn=cn,
                        start_day=days[0],
                        end_day=days[1],
                        quantity=200 / 5 * (i + 1),
                        unit="万吨",
                        progress_pct=min(1.0, 0.62 + i * 0.02),
                    )
                )
        db.commit()

        # 2) 设备（型号 -> 末态/位置取自 telemetry 末行）
        manifest = pd.read_csv(SIM / "manifest.csv")
        tele = pd.read_csv(SIM / "telemetry.csv")
        faults = pd.read_csv(SIM / "faults.csv")
        dev_count = 0
        for r in manifest.itertuples():
            model = db.query(EquipmentModel).filter(EquipmentModel.code == r.model_code).first()
            if model is None:
                continue
            last = tele[tele["device_code"] == r.device_code].iloc[-1]
            dev = db.query(Device).filter(Device.code == r.device_code).first()
            state = "fault" if r.device_code in ("E01", "T05") else "working"
            data = dict(
                model_id=model.id,
                name=r.device_name,
                owner_customer_id=cust.id,
                project_id=proj.id,
                work_state=state,
                lat=float(last["lat"]),
                lng=float(last["lng"]),
                cur_load_t=float(last["load_t"]),
                status_note="模拟数据设备（E01/T05 演示故障态）" if state == "fault" else "",
            )
            if dev is None:
                db.add(Device(code=r.device_code, **data))
            else:
                for k, v in data.items():
                    setattr(dev, k, v)
            dev_count += 1
        db.commit()

        # 3) 预警与工单（对齐 faults.csv；演示 3 条趋势预警 + 1 张已闭环工单）
        warning_ids: dict[str, int] = {}
        for r in faults.itertuples():
            dev = db.query(Device).filter(Device.code == r.device_code).first()
            if dev is None:
                continue
            fc = db.query(FaultCode).filter(FaultCode.code == r.fault_code).first()
            part, advice = PART_BY_CODE.get(r.fault_code, (fc.name if fc else "", "转人工诊断"))
            exists = (
                db.query(Warning)
                .filter(Warning.device_id == dev.id, Warning.fault_code == r.fault_code)
                .first()
            )
            if exists:
                warning_ids[r.device_code] = exists.id
                continue
            w = Warning(
                device_id=dev.id,
                fault_code=r.fault_code,
                predicted_part=part,
                probable_cause=fc.causes.split(";")[0] if fc and fc.causes else "趋势异常",
                severity=SEVERITY.get(r.fault_code, "M"),
                advice=advice,
                remaining_hours=24.0,
                model_conf=0.93,
                status="open",
                source="predictive",
            )
            db.add(w)
            db.flush()
            warning_ids[r.device_code] = w.id
        db.commit()

        # 工单：2 张开放（由预警一键生成）+ 1 张闭环（E01 ELE-04 突发检修完成）
        wo_count = 0
        for dev_code in ("T02", "T04"):
            dev = db.query(Device).filter(Device.code == dev_code).first()
            fc = db.query(FaultCode).filter(FaultCode.code == "HYD-01").first()
            w = db.get(Warning, warning_ids.get(dev_code, 0))
            if dev is None or fc is None:
                continue
            wd = Warning(
                device_id=dev.id,
                fault_code="HYD-01",
                predicted_part="液压油/散热系统",
                probable_cause=fc.causes.split(";")[0],
                severity="H",
                advice=fc.fix_plan,
                remaining_hours=18.0,
                model_conf=0.91,
                status="converted",
                source="diagnose",
            )
            db.add(wd)
            db.flush()
            db.add(
                WorkOrder(
                    code=f"WO-DEMO-{dev_code}",
                    warning_id=wd.id,
                    device_id=dev.id,
                    fault_desc="液压油温过高预警（模拟）",
                    fault_code="HYD-01",
                    diagnosis=[{"code": "HYD-01", "name": fc.name, "confidence": 0.91}],
                    fix_plan=fc.fix_plan,
                    parts=[
                        {"name": p, "qty": 1, "price_cny": 0, "stock": 0, "action": "库存不足，建议采购"}
                        for p in str(fc.parts).split(";")
                        if p
                    ],
                    est_hours=fc.est_hours,
                    engineer="李师傅（液压组）",
                    status="created",
                )
            )
            wo_count += 1
        dev_e01 = db.query(Device).filter(Device.code == "E01").first()
        if dev_e01:
            db.add(
                WorkOrder(
                    code="WO-DEMO-CLOSED",
                    device_id=dev_e01.id,
                    fault_desc="控制器通讯故障（ELE-04，突发）",
                    fault_code="ELE-04",
                    diagnosis=[{"code": "ELE-04", "name": "控制器通讯故障", "confidence": 0.99}],
                    fix_plan="更换控制器并重刷程序，通讯恢复",
                    parts=[{"name": "控制器", "qty": 1}],
                    est_hours=4.0,
                    engineer="赵师傅（电气组）",
                    status="closed",
                )
            )
            wo_count += 1
        # 4) 维保计划（结合施工进度推荐保养窗口，避开关键工期）
        for d in db.query(Device).all():
            db.add(
                MaintenancePlan(
                    device_id=d.id,
                    item="每2000小时发动机大保养",
                    due_hours=2000,
                    window_start_day=24,
                    window_end_day=26,
                    status="planned",
                    note="推荐窗口避开月末关键工期",
                )
            )
        db.commit()
        seed_demo_users(db)
        return {"devices": dev_count, "warnings": db.query(Warning).count(), "workorders": wo_count}
    finally:
        db.close()


def main() -> int:
    r = run()
    print(
        f"[load_demo] 业务演示库装载完成：{r['devices']} 台设备 / {r['warnings']} 条预警 / "
        f"{r['workorders']} 张工单"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
