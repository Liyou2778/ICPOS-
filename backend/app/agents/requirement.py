"""需求分析智能体（PRD 表12）：结构化解析 + 关键参数追问。"""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend.app.agents.base import AgentArtifact, BaseAgent
from backend.app.services.requirements import MISSING_CN, parse_requirement


class RequirementAgent(BaseAgent):
    name = "requirement"

    def handle(self, db: Session, user_text: str, parsed=None) -> AgentArtifact:
        parsed = parsed or parse_requirement(user_text)
        facts: list[str] = []
        if parsed.missing:
            facts.append(
                f"需求信息不完整，缺失关键参数：{'、'.join(MISSING_CN.get(m, m) for m in parsed.missing)}"
            )
            facts.append(f"追问话术：{'；'.join(parsed.followup_questions)}")
        else:
            facts.append(
                f"识别场景：{parsed.scene_cn}；年产能 {parsed.annual_t:,.0f} 吨（约日产能 "
                f"{parsed.daily_t:,.0f} 吨）；工期 {parsed.duration_years:.1f} 年；"
                f"预算 {parsed.budget_cny:,.0f} 元"
            )
            if parsed.constraints:
                facts.append(f"识别约束：{'、'.join(parsed.constraints)}")
        return self.artifact(
            facts=facts,
            payload={"parsed": parsed.model_dump()},
            message="需求已结构化解析" if not parsed.missing else "关键参数缺失，已生成追问话术",
        )


requirement_agent = RequirementAgent()
