"""多智能体编排引擎（指导书 6.3 / PRD R4 应对）。

采用 MVP 简化编排：客户交互智能体是唯一入口，意图路由至需求分析/方案生成/
施工调度/设备运维专业智能体；专业智能体不横向互调；复杂任务按固定次序串行
汇总。会话状态（当前智能体、中间结果、引用来源）集中于 ChatSession.state。
"""

from __future__ import annotations

import logging
import re

from sqlalchemy.orm import Session

from backend.app.agents.base import AgentArtifact
from backend.app.agents.dispatch import dispatch_agent
from backend.app.agents.maintenance import maintenance_agent
from backend.app.agents.solution import solution_agent
from backend.app.models import ChatSession, Device, KnowledgeEntry, Project, Warning
from backend.app.services import rag
from backend.app.services import requirement_slots as slot_svc

logger = logging.getLogger("icops.orchestrator")

HUMAN_WORDS = ("转人工", "人工客服", "找真人", "人工服务", "电话")
SOLUTION_WORDS = (
    "方案",
    "选型",
    "配置",
    "报价",
    "投标",
    "招标",
    "采购",
    "买",
    "购置",
    "TCO",
    "测算",
    "施工组织",
    "施工方案",
    "设备推荐",
    "多少钱",
    "价格",
)
DISPATCH_WORDS = ("调度", "派单", "空载", "车等铲", "铲等车", "重调度", "运输", "派车")
MAINTENANCE_WORDS = (
    "故障",
    "诊断",
    "维修",
    "工单",
    "报修",
    "预警",
    "预测",
    "保养",
    "油温",
    "水温",
    "发动机",
    "液压",
    "会不会坏",
    "怎么修",
    "如何修",
    "怎么处理",
)
STATUS_WORDS = ("在哪", "位置", "数量", "状态", "进度", "产量", "干了多少", "今天", "作业")
REFERENCE_WORDS = (
    "上面",
    "刚才",
    "那套",
    "上一步",
    "前一轮",
    "上一轮",
    "之前生成",
    "前面",
    "第一套",
    "第二套",
    "第三套",
    "方案一",
    "方案二",
    "方案三",
    "刚才说的",
)
PLAN_TOPIC_WORDS = ("方案", "TCO", "tco", "配置", "选型", "报价", "成本")
FRESH_REQ_WORDS = ("年产", "预算", "工期", "产量", "帮我生成", "请生成", "重新", "矿区")

_CODE_RE = re.compile(r"\b[A-Z]{2,4}-\d{2,3}\b")


def _has(text: str, words: tuple[str, ...]) -> bool:
    return any(w in text for w in words)


def classify_intent(user_text: str) -> str:
    if _has(user_text, HUMAN_WORDS):
        return "human"
    if _CODE_RE.search(user_text.upper()):
        return "maintenance"  # 故障码查询/诊断优先路由运维智能体
    if _has(user_text, DISPATCH_WORDS) and _has(
        user_text, ("调度", "重调度", "派单", "确认", "车等铲", "空载")
    ):
        return "dispatch"
    if _has(user_text, MAINTENANCE_WORDS):
        return "maintenance"
    if _has(user_text, SOLUTION_WORDS):
        return "solution"
    if _has(user_text, STATUS_WORDS):
        return "status"
    return "kb_qa"


def _status_facts(db: Session) -> list[str]:
    total = db.query(Device).count()
    working = db.query(Device).filter(Device.work_state == "working").count()
    fault = db.query(Device).filter(Device.work_state == "fault").count()
    warnings_open = db.query(Warning).filter(Warning.status == "open").count()
    proj = db.query(Project).order_by(Project.id).first()
    lines = [f"当前在役设备 {total} 台：作业中 {working} 台、故障停机 {fault} 台、其余待命/维保"]
    if proj:
        lines.append(
            f"项目 {proj.name} 进度 {proj.progress_pct * 100:.1f}%"
            + (f"，工程量 {proj.work_volume:,.0f} {proj.work_volume_unit}" if proj.work_volume else "")
        )
    lines.append(f"今日开放预警 {warnings_open} 条（高等级可一键生成维修工单）")
    return lines


def _kb_qa(db: Session, user_text: str) -> AgentArtifact:
    hits = rag.hybrid_search(db, user_text, top_k=3)
    kw_hits = rag._keyword_candidates(db, user_text)  # 关键词是否命中（防幻觉门控）
    strong = bool(kw_hits) or (bool(hits) and hits[0].score >= 0.5)
    if not hits or not strong:
        return AgentArtifact(
            agent="kb_qa",
            transfer=True,
            facts=["知识库暂无直接覆盖该问题的资料（已记录用于知识库优化），建议转人工进一步确认。"],
            message="知识库未覆盖，转人工",
        )
    facts = [f"检索到 {len(hits)} 条相关资料："]
    for h in hits:
        entry = db.get(KnowledgeEntry, h.entry_id)
        content = entry.content if entry else h.excerpt
        snip = rag.sentence_snippet(content, user_text)
        facts.append(f"·【{h.kb_type}】{h.title}：{snip}")
    if len(kw_hits) <= 2:
        # 问句关键词几乎未命中知识条目：不编造答案，明确说明并给出最接近参考（防幻觉第二道防线）
        facts.append(
            "未找到与该问题直接对应的知识条目，以上仅为最接近的参考；可回复“转人工”进一步核实（问题已记录）。"
        )
    return AgentArtifact(
        agent="kb_qa",
        facts=facts,
        citations=rag.citations_of(hits),
        payload={"hits": [h.as_dict() for h in hits]},
        message="知识库问答（RAG 混合检索 + 句级摘要）",
    )


def _solution_followup(db: Session, session: ChatSession | None, user_text: str) -> AgentArtifact | None:
    """多轮上下文：对"本会话此前生成的方案"做追问（如：第二套方案 TCO）。"""
    if session is None or not session.state:
        return None
    state = session.state
    plans = state.get("plans")
    if not plans:
        return None
    if _has(user_text, FRESH_REQ_WORDS):
        return None  # 出现新需求要素 -> 走新的方案生成
    # 必须是"方案类话题 + 引用词"同时出现才视为追问，避免误伤转人工/普通问题
    if not (_has(user_text, PLAN_TOPIC_WORDS) and _has(user_text, REFERENCE_WORDS)):
        return None
    idx = 1  # 默认引用上一轮推荐方案
    if any(w in user_text for w in ("第一套", "方案一", "1 套", "1套")):
        idx = 0
    elif any(w in user_text for w in ("第三套", "方案三", "3 套", "3套")):
        idx = 2
    elif any(w in user_text for w in ("第二套", "方案二", "2 套", "2套")):
        idx = 1
    else:
        idx = int(state.get("best_index", 1))
    idx = max(0, min(idx, len(plans) - 1))
    p = plans[idx]
    return AgentArtifact(
        agent="solution_followup",
        facts=[
            f"上一轮生成的方案共 {len(plans)} 套；您询问的是第 {idx + 1} 套【{p['name']}】",
            f"三年 TCO 合计 {p['tco_3y_total_cny']:,.0f} 元（含购置/能耗/维保/残值四类）",
            f"日设计产能 {p['daily_capacity_t']:,.0f} 吨；配置说明：{p['summary']}",
            "以上数值来自上一轮方案生成时数据库直读结果，可逐项溯源",
        ],
        message=f"方案 {idx + 1} 追问已回答",
    )


class Orchestrator:
    """单入口意图路由编排。"""

    def route(self, db: Session, session: ChatSession | None, user_text: str) -> AgentArtifact:
        # ---------- 会话需求槽位：跨轮抽取/合并（数字来自规则引擎，不经过大模型） ----------
        state = dict(session.state or {}) if session is not None else {}
        prev_slots = dict(state.get("slots") or {})
        extracted = slot_svc.slots_from_text(user_text)
        slots = slot_svc.merge_slots(prev_slots, extracted)
        pending = state.get("pending_intent")
        if pending and not extracted:
            miss = slot_svc.missing_slots(slots)
            if len(miss) == 1:
                v = slot_svc.interpret_bare_value(user_text, miss[0])
                if v is not None:
                    slots = slot_svc.merge_slots(slots, {miss[0]: v})
        slots_changed = slots != prev_slots  # 本次消息是否真的补充了槽位
        patch: dict = {"slots": slots}

        def out(art: AgentArtifact) -> AgentArtifact:
            art.payload.setdefault("session_patch", {}).update(patch)
            return art

        # 1) 多轮追问优先（引用上一轮方案）
        fb = _solution_followup(db, session, user_text)
        if fb is not None:
            return out(fb)
        intent = classify_intent(user_text)
        logger.info(
            "chat intent=%s session=%s slots=%s", intent, session.id if session else None, list(slots)
        )
        if intent == "human":
            return out(
                AgentArtifact(
                    agent="human",
                    transfer=True,
                    facts=["已为您转接人工客服（记录本次会话用于知识库优化）"],
                    message="转人工",
                )
            )
        if intent == "dispatch":
            return out(dispatch_agent.handle(db, user_text))
        if intent == "maintenance":
            return out(maintenance_agent.handle(db, user_text))
        # 待续任务（缺参阻塞后）仅在“本次消息确实是补充”时续跑，避免劫持无关提问
        supplement_words = ("继续", "生成方案", "开始生成", "可以了", "补全", "接着")
        is_supplement = slots_changed or _has(user_text, supplement_words)
        if intent == "solution" or (pending and pending.get("agent") == "solution" and is_supplement):
            doc_type = (pending or {}).get("doc_type") or "construction"
            if _has(user_text, ("投标", "招标", "竞标")):
                doc_type = "bid"
            elif _has(user_text, ("选型", "采购", "报价", "价格", "多少钱")):
                doc_type = "selection"
            # 关键参数缺失 -> 阻塞生成，返回清单式追问（避免无效方案）
            miss = slot_svc.missing_slots(slots)
            if miss:
                patch["pending_intent"] = {"agent": "solution", "doc_type": doc_type, "brief": user_text[:80]}
                facts = [f"还缺少 {len(miss)} 项关键参数，暂不生成方案（避免产出无效方案）："]
                facts += [
                    f"· {slot_svc.SLOT_META[k]['label']}：{slot_svc.SLOT_META[k]['question']}"
                    for k in miss
                    if k in slot_svc.SLOT_META
                ]
                facts.append("可在对话下方表单一次性补录，补录后我会自动继续生成方案，无需重述需求。")
                return out(
                    AgentArtifact(
                        agent="requirement",
                        facts=facts,
                        payload={
                            "missing_slots": miss,
                            "slot_form": slot_svc.form_spec(slots, miss),
                            "slots": slots,
                            "slot_summary": slot_svc.slot_summary(slots),
                            "pending_intent": patch["pending_intent"],
                        },
                        message=f"关键参数缺失（{len(miss)} 项），已生成追问清单",
                    )
                )
            # 参数齐备 -> 续跑原任务
            patch["pending_intent"] = None
            art = solution_agent.handle(db, slot_svc.requirement_text(slots), doc_type=doc_type)
            art.payload["slots"] = slots
            art.payload["slot_summary"] = slot_svc.slot_summary(slots)
            return out(art)
        if intent == "status":
            facts = _status_facts(db)
            # 并行补充领域知识检索（可解释）
            hits = rag.hybrid_search(db, user_text, top_k=1)
            if hits:
                facts.append(f"延伸资料：{hits[0].title}（{hits[0].source}）")
                return out(
                    AgentArtifact(
                        agent="status", facts=facts, citations=rag.citations_of(hits), message="运营状态查询"
                    )
                )
            return out(AgentArtifact(agent="status", facts=facts, message="运营状态查询"))
        return out(_kb_qa(db, user_text))


orchestrator = Orchestrator()
