"""对话 API：会话管理 + SSE 流式多智能体对话（指导书 4.5：对话采用 SSE，首字节 ≤3s）。"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app.agents.orchestrator import orchestrator
from backend.app.core.db import get_db
from backend.app.core.llm_gateway import gateway
from backend.app.core.security import get_current_user
from backend.app.models import ChatMessage, ChatSession, User

logger = logging.getLogger("icops.chat")
router = APIRouter(prefix="/api/chat")

MAX_CONTEXT = 8  # 与当前轮合计 ≥9 轮上下文（PRD：多轮上下文保留 ≥10 轮由前端/会话累计保证）


class SessionIn(BaseModel):
    title: str = "新对话"


class MessageIn(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _history(db: Session, session_id: int) -> list[dict]:
    rows = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.id.desc())
        .limit(MAX_CONTEXT)
        .all()
    )
    return [{"role": r.role, "content": r.content} for r in reversed(rows)]


def _prompt_messages(history: list[dict], user_text: str, facts: list[str]) -> list[dict]:
    system = (
        "你是智工云枢（ICOPS）矿山全流程智能运营助手。用户消息中的『数据事实:』行由系统"
        "从结构化数据库与知识库直读而来，你的答复必须严格基于这些事实组织语言，禁止虚构任何数字"
        "（如需数值请直接引用事实行）；最后附注『AI 生成初稿，需人工确认』。"
    )
    fact_lines = "\n".join(f"数据事实:{f}" for f in facts) if facts else ""
    content = f"{user_text}\n{fact_lines}".strip()
    msgs = [{"role": "system", "content": system}]
    msgs += history[-MAX_CONTEXT:]
    msgs.append({"role": "user", "content": content})
    return msgs


@router.post("/sessions")
def create_session(body: SessionIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    s = ChatSession(user_id=user.id, title=body.title, state={})
    db.add(s)
    db.commit()
    db.refresh(s)
    return {"session_id": s.id, "title": s.title}


@router.get("/sessions")
def list_sessions(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = (
        db.query(ChatSession)
        .filter(ChatSession.user_id == user.id)
        .order_by(ChatSession.id.desc())
        .limit(50)
        .all()
    )
    return [
        {"session_id": s.id, "title": s.title, "created_at": s.created_at.isoformat() if s.created_at else ""}
        for s in rows
    ]


@router.get("/sessions/{sid}/messages")
def session_messages(sid: int, db: Session = Depends(get_db)):
    s = db.get(ChatSession, sid)
    if s is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    rows = db.query(ChatMessage).filter(ChatMessage.session_id == sid).order_by(ChatMessage.id).all()
    return [
        {
            "id": m.id,
            "role": m.role,
            "content": m.content,
            "meta": m.meta,
            "created_at": m.created_at.isoformat() if m.created_at else "",
        }
        for m in rows
    ]


class _RouteOut:
    """路由执行结果：facts、art、session。"""

    def __init__(self, facts: list[str], art, session: ChatSession) -> None:
        self.facts = facts
        self.art = art
        self.session = session


def _route(db: Session, s: ChatSession, user_text: str) -> _RouteOut:
    art = orchestrator.route(db, s, user_text)
    # 会话状态集中存储：当前智能体、中间结果、引用来源（指导书 6.3）
    # 若本轮生成了方案，额外保存方案索引供"多轮追问"使用（如：第二套方案的 TCO）
    state: dict = dict(s.state or {})
    state.update(
        {
            "agent": art.agent,
            "last_facts": art.facts[:20],
            "citations": art.citations[:10],
            "payload_preview": json.dumps(art.payload, ensure_ascii=False)[:2000],
        }
    )
    if art.agent == "solution" and art.payload.get("bundles"):
        state["plans"] = [
            {
                "name": b.get("name", f"方案{i + 1}"),
                "tco_3y_total_cny": b.get("tco_3y_total_cny", 0),
                "daily_capacity_t": b.get("daily_capacity_t", 0),
                "summary": b.get("summary", ""),
            }
            for i, b in enumerate(art.payload["bundles"])
        ]
        state["best_index"] = art.payload.get("best_index", 0)
    s.state = state
    db.commit()
    return _RouteOut(art.facts, art, s)


def _persist(db: Session, s: ChatSession, user_text: str, assistant_text: str, meta: dict) -> int:
    db.add(ChatMessage(session_id=s.id, role="user", content=user_text, meta={}))
    m = ChatMessage(session_id=s.id, role="assistant", content=assistant_text, meta=meta)
    db.add(m)
    db.commit()
    db.refresh(m)
    return m.id


async def _llm_text(msgs: list[dict]) -> str:
    """非流式（demo 模式同样走网关，确定性模板兜底）。"""
    result = await gateway.complete(msgs, scene="chat")
    return result.text


@router.post("/sessions/{sid}/messages")
async def send_message(
    sid: int, body: MessageIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    s = db.get(ChatSession, sid)
    if s is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    ro = _route(db, s, body.message)
    if ro.art.transfer or ro.art.agent == "human":
        text = "\n".join(ro.facts) if ro.facts else "已为您转人工客服。"
        meta = {"agent": "human", "citations": [], "transfer": True}
    else:
        msgs = _prompt_messages(_history(db, sid), body.message, ro.facts)
        text = await _llm_text(msgs)
        meta = {"agent": ro.art.agent, "citations": ro.art.citations, "transfer": False}
    mid = _persist(db, s, body.message, text, meta)
    return {
        "message_id": mid,
        "agent": meta["agent"],
        "content": text,
        "citations": meta["citations"],
        "transfer": meta["transfer"],
    }


@router.post("/sessions/{sid}/messages/stream")
async def stream_message(
    sid: int, body: MessageIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    s = db.get(ChatSession, sid)
    if s is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    ro = _route(db, s, body.message)
    citations = ro.art.citations

    async def gen():
        yield _sse("route", {"agent": ro.art.agent, "session_id": sid, "message": body.message[:80]})
        if ro.art.transfer or ro.art.agent == "human":
            text = "\n".join(ro.facts) if ro.facts else "已为您转人工客服。"
            mid = _persist(db, s, body.message, text, {"agent": "human", "citations": [], "transfer": True})
            yield _sse("done", {"message_id": mid, "content": text, "transfer": True, "citations": []})
            return
        msgs = _prompt_messages(_history(db, sid), body.message, ro.facts)
        # SSE 流式：逐段增量输出（真实通道首字节 ≤3s；demo 模板即时）
        buf: list[str] = []
        try:
            async for chunk in gateway.stream(msgs, scene="chat"):
                t = chunk.text
                buf.append(t)
                yield _sse("delta", {"text": t})
        except Exception as exc:  # noqa: BLE001
            logger.exception("流式对话异常")
            yield _sse("error", {"detail": f"生成中断：{exc}"})
        text = "".join(buf).strip() or "（未能生成回复，请重试）"
        meta = {"agent": ro.art.agent, "citations": citations, "transfer": False}
        mid = _persist(db, s, body.message, text, meta)
        yield _sse("citations", {"citations": citations})
        yield _sse("done", {"message_id": mid, "content": text, "transfer": False, "citations": citations})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
