"""智能体基类与产物结构（指导书 5.3/5.4）。

产物约定：智能体只产出"结构化 payload + 数据事实行(facts) + 引用(citations)"，
最终自然语言由 LLM（或离线模板）基于事实行组织——数字全部来自数据库直读，
LLM 仅负责组织语言（防幻觉第一道防线）。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AgentArtifact:
    agent: str
    facts: list[str] = field(default_factory=list)  # 数据事实行（数字直读）
    payload: dict = field(default_factory=dict)  # 结构化结果（供导出/状态保存）
    citations: list[dict] = field(default_factory=list)  # 引用来源
    transfer: bool = False  # 转人工
    message: str = ""  # 会话内状态说明


class BaseAgent:
    name = "base"

    def artifact(self, facts=None, payload=None, citations=None, transfer=False, message=""):
        return AgentArtifact(
            agent=self.name,
            facts=facts or [],
            payload=payload or {},
            citations=citations or [],
            transfer=transfer,
            message=message,
        )
