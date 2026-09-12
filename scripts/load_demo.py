"""演示业务库装载：把模拟数据（设备/时序末态/故障事件）装配进业务库供 API 使用。

幂等语义：每次执行会先清空演示业务表（派单/工单/预警/维保/任务进度），再按
模拟数据重建，保证可重复装载、状态确定（开发与彩排常用）。
注意：会重置演示设备/项目相关的业务记录（不会删除知识库与账号）。

前置：先执行 build_kb（型号/FaultCode 入库）与 data.simulator.gen。
用法（仓库根目录）：uv run python -m scripts.load_demo
"""

from __future__ import annotations

from datetime import datetime, timedelta

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
    SparePart,
    Task,
    TaskProgress,
    Warning,
    WorkOrder,
    WorkOrderEvent,
)

SIM = settings.repo_root / "data" / "simulated"
PART_BY_CODE = {  # 预警五要素的“异常部件/建议措施”映射（示例）
    "ENG-03": ("冷却系统/水温传感器", "立即检查冷却液并停机降温，避免拉缸"),
    "HYD-01": ("液压油/散热系统", "停机清洗散热器并检查液压油位，防止液压泵烧蚀"),
    "ELE-04": ("控制器/CAN总线", "检查接插件与线束，必要时更换控制器"),
}
SEVERITY = {"ENG-03": "H", "HYD-01": "H", "ELE-04": "H"}

# 备件库存种子（部分低库存 -> 触发备件预警）
SPARE_SEED = [
    ("SP-001", "液压油", 1800, 12),
    ("SP-002", "散热器芯", 6200, 3),
    ("SP-003", "溢流阀", 3400, 2),
    ("SP-004", "液压泵修理包", 12800, 1),
    ("SP-005", "柴油滤芯", 320, 20),
    ("SP-006", "机油滤芯", 260, 18),
    ("SP-007", "空气滤芯", 480, 9),
    ("SP-008", "冷却液", 900, 6),
    ("SP-009", "节温器", 760, 4),
    ("SP-010", "风扇皮带", 380, 2),
    ("SP-011", "控制器", 26000, 1),
    ("SP-012", "蓄电池", 4300, 3),
    ("SP-013", "启动马达", 8800, 2),
    ("SP-014", "变速箱油", 1500, 8),
    ("SP-015", "制动蹄片", 2200, 5),
    ("SP-016", "轮胎", 18500, 6),
    ("SP-017", "支重轮油封", 460, 2),
    ("SP-018", "履带张紧油缸", 9800, 1),
]


def run() -> dict:
    init_db()
    db = SessionLocal()
    try:
        # 0) 幂等重置：清空演示业务表（依赖顺序：事件/工单/预警/维保 -> 派单 -> 进度）
        db.query(WorkOrderEvent).delete()
        db.query(DispatchOrder).delete()
        db.query(DispatchPlan).delete()
        db.query(DispatchLog).delete()
        db.query(WorkOrder).delete()
        db.query(Warning).delete()
        db.query(MaintenancePlan).delete()
        db.query(SparePart).delete()
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
        # 3.1) 备件库存（低库存项会在设备运营栏触发采购预警）
        for sku, name, price, stock in SPARE_SEED:
            db.add(SparePart(sku=sku, name=name, price_cny=float(price), stock=int(stock), fit_codes=""))
        db.commit()

        # 3.2) 六状态演示工单（含状态流转时间线），覆盖 已生成→已派工→维修中→待验收→已完成→已归档
        now = datetime.now()

        def seed_order(
            code: str,
            device_code: str,
            fault_code: str,
            desc: str,
            engineer: str,
            steps: list[tuple[str, str, str, int]],
            **extra,
        ) -> bool:
            """steps: [(to_status, note, operator, 距今小时数)]，按顺序生成事件时间线。"""
            dev = db.query(Device).filter(Device.code == device_code).first()
            fc = db.query(FaultCode).filter(FaultCode.code == fault_code).first()
            if dev is None:
                return False
            wo = WorkOrder(
                code=code,
                device_id=dev.id,
                fault_desc=desc,
                fault_code=fault_code,
                diagnosis=[{"code": fault_code, "name": fc.name if fc else desc, "confidence": 0.92}],
                fix_plan=fc.fix_plan if fc else "按维保手册处理",
                parts=[{"name": p, "qty": 1} for p in str(fc.parts).split(";") if p] if fc else [],
                est_hours=fc.est_hours if fc else 4.0,
                engineer=engineer,
                severity=SEVERITY.get(fault_code, "M"),
                created_at=now - timedelta(hours=steps[0][3] + 1),
                **extra,
            )
            db.add(wo)
            db.flush()
            prev = "created"
            db.add(
                WorkOrderEvent(
                    workorder_code=code,
                    from_status="",
                    to_status="created",
                    note="预警自动生成工单",
                    operator="系统",
                    created_at=wo.created_at,
                )
            )
            for to_status, note, operator, hours_ago in steps:
                at = now - timedelta(hours=hours_ago)
                db.add(
                    WorkOrderEvent(
                        workorder_code=code,
                        from_status=prev,
                        to_status=to_status,
                        note=note,
                        operator=operator,
                        created_at=at,
                    )
                )
                prev = to_status
            return True

        made = 0
        made += int(
            seed_order(
                "WO-FLOW-DISPATCH",
                "T03",
                "ENG-03",
                "水温偏高预警，待派工到场检查",
                "王师傅（发动机组）",
                [("dispatched", "已派工至王师傅，预计 4h 到场", "调度员老李", 6)],
                status="dispatched",
                dispatched_at=now - timedelta(hours=6),
            )
        )
        made += int(
            seed_order(
                "WO-FLOW-REPAIR",
                "T01",
                "HYD-01",
                "液压油温高，已进场维修",
                "李师傅（液压组）",
                [
                    ("dispatched", "派工李师傅", "调度员老李", 20),
                    ("repairing", "已停机拆检散热器，清洗并补油", "李师傅", 8),
                ],
                status="repairing",
                dispatched_at=now - timedelta(hours=20),
                repair_started_at=now - timedelta(hours=8),
                repair_notes="散热器翅片堵塞严重，已清洗；液压油位补至标准值。",
            )
        )
        made += int(
            seed_order(
                "WO-FLOW-ACCEPT",
                "T05",
                "ENG-01",
                "启动困难，维修完成待验收",
                "王师傅（发动机组）",
                [
                    ("dispatched", "派工", "调度员老李", 30),
                    ("repairing", "更换蓄电池与启动马达碳刷", "王师傅", 22),
                    ("pending_acceptance", "维修完成，等待设备管理员验收", "王师傅", 4),
                ],
                status="pending_acceptance",
                dispatched_at=now - timedelta(hours=30),
                repair_started_at=now - timedelta(hours=22),
                acceptance_at=now - timedelta(hours=4),
                labor_hours=3.5,
                repair_notes="更换蓄电池 1 只、启动马达碳刷 1 套，试启动正常。",
            )
        )
        made += int(
            seed_order(
                "WO-FLOW-DONE",
                "T02",
                "ENG-03",
                "冷却系统效能下降，已完成维修",
                "王师傅（发动机组）",
                [
                    ("dispatched", "派工", "调度员老李", 56),
                    ("repairing", "清洗散热器并更换节温器", "王师傅", 48),
                    ("pending_acceptance", "维修完成待验收", "王师傅", 30),
                    ("completed", "验收通过，设备已复产", "设备管理员", 26),
                ],
                status="completed",
                dispatched_at=now - timedelta(hours=56),
                repair_started_at=now - timedelta(hours=48),
                acceptance_at=now - timedelta(hours=30),
                completed_at=now - timedelta(hours=26),
                closed_at=now - timedelta(hours=26),
                labor_hours=5.0,
                parts_used=[{"name": "节温器", "qty": 1, "price_cny": 760}],
                repair_notes="更换节温器；水温恢复 88℃ 正常区间。",
            )
        )
        made += int(
            seed_order(
                "WO-FLOW-ARCHIVE",
                "E01",
                "ELE-04",
                "控制器通讯故障（突发），已归档",
                "赵师傅（电气组）",
                [
                    ("dispatched", "紧急派工", "调度员老李", 96),
                    ("repairing", "更换控制器并重刷程序", "赵师傅", 92),
                    ("pending_acceptance", "通讯恢复，待验收", "赵师傅", 90),
                    ("completed", "验收通过", "设备管理员", 89),
                    ("archived", "案例回写知识库（控制器通讯故障处置）", "售后服务经理", 88),
                ],
                status="archived",
                dispatched_at=now - timedelta(hours=96),
                repair_started_at=now - timedelta(hours=92),
                acceptance_at=now - timedelta(hours=90),
                completed_at=now - timedelta(hours=89),
                archived_at=now - timedelta(hours=88),
                closed_at=now - timedelta(hours=89),
                labor_hours=4.0,
                parts_used=[{"name": "控制器", "qty": 1, "price_cny": 26000}],
                repair_notes="更换控制器并重刷程序，CAN 通讯恢复；已纳入案例库。",
            )
        )
        wo_count += made
        db.commit()
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
