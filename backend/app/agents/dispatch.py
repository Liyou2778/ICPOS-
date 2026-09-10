"""施工调度智能体（PRD 表12 / 指导书 5.4、6.5）。"""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from backend.app.agents.base import AgentArtifact, BaseAgent
from backend.app.services.dispatch import dispatch_service


def _extract_fault_device(text: str) -> str | None:
    m = re.search(r"(?:设备|矿卡|挖掘机)?\s*([A-Za-z]?\d{2,4})\s*(?:故障|报修|出问题|重调度|不可用)", text)
    if m:
        return m.group(1).upper()
    return None


class DispatchAgent(BaseAgent):
    name = "dispatch"

    def handle(self, db: Session, user_text: str) -> AgentArtifact:
        confirm = any(k in user_text for k in ("确认", "下发")) and "调度" in user_text
        if confirm:
            plan = db_query_latest(db)
            if plan is None:
                return self.artifact(facts=["暂无待确认的调度方案，请先生成调度计划"], message="无待确认方案")
            out = dispatch_service.confirm(db, plan.id)
            return self.artifact(
                facts=[
                    f"调度方案 #{out.plan_id} 已确认并下发 {len(out.assignments)} 条派单（建议执行→正式执行）"
                ],
                payload=out.model_dump(),
                message="调度方案已下发",
            )

        replan = any(k in user_text for k in ("重调度", "故障", "延迟", "设备坏了", "重新调度"))
        fault_code = _extract_fault_device(user_text)
        trigger = "fault" if (replan and fault_code) else "delay" if replan else "initial"
        out = dispatch_service.run(db, trigger=trigger, fault_device_code=fault_code)
        s = out.stats
        trigger_cn = {
            "fault": "故障触发·动态重调度",
            "delay": "延迟触发·动态重调度",
            "initial": "初始调度",
        }.get(trigger, trigger)
        facts = [
            f"{trigger_cn}完成（故障设备：{fault_code or '无'}），生成 {len(out.assignments)} 条派单",
            f"空载率 {s['idle_rate'] * 100:.1f}%；设备利用率 {s['utilization'] * 100:.1f}%；"
            f"平均等待 {s['avg_wait_min']} 分钟",
            f"在役矿卡 {s['active_trucks']} 台、铲装设备 {s['excavators']} 台、故障停机 {s['faulted']} 台",
            "派单综合考虑距离/载重料仓匹配/道路拥堵/任务优先级四项约束，全部附可解释原因",
            "调度结论为建议执行，须经调度员确认后下发（回复“确认调度方案”）",
        ]
        return self.artifact(
            facts=facts, payload=out.model_dump(), message=f"{trigger} 调度完成，方案 #{out.plan_id}"
        )


def db_query_latest(db: Session):
    from backend.app.models import DispatchPlan

    return (
        db.query(DispatchPlan)
        .filter(DispatchPlan.confirmed.is_(False))
        .order_by(DispatchPlan.id.desc())
        .first()
    )


dispatch_agent = DispatchAgent()
