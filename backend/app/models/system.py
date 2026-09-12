"""系统域：用户 / 会话 / 对话消息 / 大模型调用日志（LLM 成本与配额追踪）。"""

from __future__ import annotations

from datetime import datetime, UTC

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class User(Base):
    """内部用户（MVP 演示账号 + RBAC 角色）。"""

    __tablename__ = "sys_user"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))  # 演示环境明文前缀 sha256:
    display_name: Mapped[str] = mapped_column(String(64), default="")
    role: Mapped[str] = mapped_column(String(32), default="engineer")  # admin|engineer|operator|customer
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("cust_customer.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ChatSession(Base):
    """对话会话：集中存放编排状态（当前智能体、中间结果、引用来源）。

    归档：status=active|archived（归档不删除，可恢复、可重命名、可加标签）。
    """

    __tablename__ = "sys_chat_session"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(128), default="新对话")
    state: Mapped[dict] = mapped_column(JSON, default=dict)  # 编排状态快照（含 slots 需求槽位）
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)  # active|archived
    tags: Mapped[str] = mapped_column(String(128), default="")  # 逗号分隔标签（可按项目/客户）
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ChatMessage(Base):
    """对话消息：多轮上下文保留 ≥10 轮。"""

    __tablename__ = "sys_chat_message"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sys_chat_session.id"), index=True)
    role: Mapped[str] = mapped_column(String(16))  # user|assistant|system|human
    content: Mapped[str] = mapped_column(Text, default="")
    meta: Mapped[dict] = mapped_column(JSON, default=dict)  # route/citations/agent
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LLMCallLog(Base):
    """大模型调用日志：智能体/场景维度词元与费用，支撑成本配额告警（指导书 6.1）。"""

    __tablename__ = "sys_llm_call_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), default="")
    model: Mapped[str] = mapped_column(String(64), default="")
    scene: Mapped[str] = mapped_column(String(64), default="general")  # 智能体/场景
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_cny: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    ok: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DailyCost(Base):
    """按日的费用汇总（预算告警依据）。"""

    __tablename__ = "sys_daily_cost"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    day: Mapped[str] = mapped_column(String(10), index=True)  # YYYY-MM-DD
    cost_cny: Mapped[float] = mapped_column(Float, default=0.0)
