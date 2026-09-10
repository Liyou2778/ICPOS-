"""设备运维智能体（PRD 表12 / 指导书 5.4）：预测、诊断、工单。"""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from backend.app.agents.base import AgentArtifact, BaseAgent
from backend.app.models import Device, FaultCode, Warning
from backend.app.services.diagnosis import create_workorder, diagnose_code, diagnose_text
from backend.app.services.predictive import models_ready, predict_device


def _extract_device(text: str) -> str | None:
    m = re.search(r"(?:设备|矿卡|挖掘机|车)\s*([A-Za-z]\d{1,4})", text, re.IGNORECASE)
    return m.group(1).upper() if m else None


def _extract_code(text: str) -> str | None:
    m = re.search(r"\b([A-Z]{2,4}-\d{2})\b", text.upper())
    return m.group(1) if m else None


class MaintenanceAgent(BaseAgent):
    name = "maintenance"

    def handle(self, db: Session, user_text: str) -> AgentArtifact:
        # 1) 预测/预警请求
        if any(k in user_text for k in ("预测", "预警", "提前", "会不会坏", "剩余可用", "保养")):
            return self._predict(db, user_text)
        # 2) 工单/报修
        if any(k in user_text for k in ("工单", "报修", "维修", "派人修")):
            return self._workorder(db, user_text)
        # 3) 诊断（自然语言或故障码）
        return self._diagnose(db, user_text)

    def _diagnose(self, db: Session, text: str) -> AgentArtifact:
        code = _extract_code(text)
        if code and db.query(FaultCode).filter(FaultCode.code == code).first():
            result = diagnose_code(db, code)
        else:
            result = diagnose_text(db, text)
        facts = [
            f"诊断结果 Top{len(result.top3)}："
            + "；".join(f"{d.code} {d.name}（置信度 {d.confidence * 100:.0f}%）" for d in result.top3),
            f"推荐维修方案：{result.fix_plan}",
            f"预计维修时长 {result.est_hours} 小时；备件：{('、'.join(p['name'] for p in result.parts)) or '无'}",
            "如描述未被知识库覆盖，系统已明确拒答并转人工（陈述必有出处）",
        ]
        if result.top3 and result.top3[0].code == "UNKNOWN":
            facts[-1] = "该描述未被维保知识库覆盖（已拒答并记录，用于知识库优化）；建议回复“转人工”"
        return self.artifact(
            facts=facts,
            payload=result.model_dump(),
            citations=[
                {
                    "kb_type": "maintenance",
                    "title": f"故障码 {d.code} {d.name}",
                    "source": "维保知识库",
                    "version": "V1.0",
                }
                for d in result.top3[:1]
            ],
            message="故障诊断完成",
        )

    def _workorder(self, db: Session, text: str) -> AgentArtifact:
        """工单必须有故障依据：优先设备开放预警 -> 文本携带故障码 -> 自然语言；否则询问补充。"""
        device = None
        code = _extract_code(text)
        device_code = _extract_device(text)
        if device_code:
            device = db.query(Device).filter(Device.code == device_code).first()
        if device is None:
            device = db.query(Device).filter(Device.work_state == "fault").first()
        if device is None:
            return self.artifact(
                facts=[
                    "请先说明是哪台设备需要开单（例如：给矿卡 T02 生成维修工单），"
                    "并提供故障现象或故障代码（如 HYD-01），我再为您生成工单。"
                ],
                message="缺少设备信息",
            )
        # 故障依据：代码优先；其次该设备最新开放预警的故障码；最后自然语言诊断
        result = None
        base = None
        if code and db.query(FaultCode).filter(FaultCode.code == code).first():
            result = diagnose_code(db, code)
            base = code
        else:
            warn = (
                db.query(Warning)
                .filter(Warning.device_id == device.id, Warning.status == "open")
                .order_by(Warning.id.desc())
                .first()
            )
            if warn is not None:
                result = diagnose_code(db, warn.fault_code)
                base = warn.fault_code
        if result is None:
            result = diagnose_text(db, text if text.strip() else "")
        if result.top3 and result.top3[0].code == "UNKNOWN":
            return self.artifact(
                facts=[
                    f"设备 {device.code} 尚未有故障依据：无开放预警、也未提供故障码或可识别现象。",
                    "请补充故障代码（如 HYD-01）或故障现象描述，我将为您生成维修工单。",
                ],
                message="缺少故障依据，未开单",
            )
        wo = create_workorder(db, device.code, result)
        facts = [
            f"维修工单 {wo.code} 已自动生成（设备 {wo.device_code}，故障依据：{base or '自然语言诊断'}）",
            f"工单信息：诊断 {len(wo.diagnosis)} 项；方案：{wo.fix_plan[:48]}…；"
            f"备件 {len(wo.parts)} 项（缺货自动生成采购建议）；预计 {wo.est_hours} 小时",
            f"推荐工程师：{wo.engineer}（按位置与技能匹配）",
        ]
        return self.artifact(
            facts=facts,
            payload=wo.model_dump(),
            citations=[
                {
                    "kb_type": "maintenance",
                    "title": f"故障码 {base or result.code}",
                    "source": "维保知识库",
                    "version": "V1.0",
                }
            ],
            message=f"工单已生成 {wo.code}",
        )

    def _predict(self, db: Session, text: str) -> AgentArtifact:
        device_code = _extract_device(text)
        if not models_ready():
            return self.artifact(
                facts=["预测模型尚未训练，请先执行训练脚本（uv run python -m scripts.train_models）"],
                message="模型未就绪",
            )
        device_codes = (
            [device_code] if device_code else [d.code for d in db.query(Device).order_by(Device.id).limit(4)]
        )
        lines: list[str] = []
        risky_lines: list[str] = []
        for code in device_codes:
            r = predict_device(db, code)
            if not r.get("risky"):
                lines.append(f"设备 {code}：运行正常（异常分 {r.get('anomaly_score', 0):.3f}）")
            else:
                rul = r.get("remaining_hours")
                rul_txt = f"{rul:.1f} 小时" if rul is not None else "未知"
                risky_lines.append(
                    f"设备 {code}：风险预警 —— 最可能故障 {r['top_code']}（置信度 {r['top_conf'] * 100:.0f}%），"
                    f"预计剩余可用 {rul_txt}，异常分 {r.get('anomaly_score', 0):.3f}"
                )
        facts = risky_lines + lines
        facts.append(
            "预警五要素完整输出（异常部件/可能原因/严重等级/建议措施/剩余可用时间），高等级预警可一键生成工单"
        )
        return self.artifact(
            facts=facts,
            message="预测性巡检完成",
            payload={"device_results": [predict_device(db, c) for c in device_codes]},
            citations=[
                {
                    "kb_type": "maintenance",
                    "title": "预测性维护模型",
                    "source": "模拟数据训练（V1.1 真实试点）",
                    "version": "V1.0",
                }
            ],
        )


maintenance_agent = MaintenanceAgent()
