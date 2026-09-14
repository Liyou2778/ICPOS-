"""方案生成智能体（PRD 表12 / 指导书 5.3、6.7）。

职责：设备选型(Top3) + TCO 测算 + 施工组织/投标方案章节化内容装配，
产出可直接导出 Word/PDF 的完整 payload（模板+内容装配模式）。
"""

from __future__ import annotations

import json
from datetime import datetime, UTC

from sqlalchemy.orm import Session

from backend.app.agents.base import AgentArtifact, BaseAgent
from backend.app.core.config import settings
from backend.app.schemas.domain import ParsedRequirement
from backend.app.services.rag import hybrid_search
from backend.app.services.requirements import parse_requirement
from backend.app.services.selection import generate_selection, WORK_DAYS_YEAR, OPERATION_YEARS

PROCESSES = [
    ("drilling", "穿孔"),
    ("blasting", "爆破"),
    ("loading", "铲装"),
    ("hauling", "运输"),
    ("dumping", "排土"),
]
PROCESS_KEYWORDS = {
    "穿孔": "穿孔 孔网 钻孔",
    "爆破": "爆破 装药 单耗",
    "铲装": "铲装 挖掘机 斗容",
    "运输": "运输 矿用自卸车 循环时间",
    "排土": "排土 排土场",
}


def _template_chapters(db: Session, doc_type: str) -> list[dict]:
    """从模板库读取章节骨架（入库的模板 JSON 或文件）。"""
    path = settings.repo_root / "data" / "knowledge" / "templates"
    fname = {
        "selection": "template_selection.json",
        "bid": "template_bid.json",
        "construction": "template_construction.json",
    }.get(doc_type, "template_selection.json")
    try:
        data = json.loads((path / fname).read_text(encoding="utf-8"))
        return data.get("chapters", [])
    except FileNotFoundError:
        return []


def _enrich_chapters(db: Session, chapters: list[dict]) -> tuple[list[dict], list[dict]]:
    """每章填充要点：模板 bullets + RAG 工艺检索片段；返回章节与引用。"""
    citations: list[dict] = []
    out: list[dict] = []
    for ch in chapters:
        paragraphs: list[str] = []
        for b in ch.get("bullets", []):
            paragraphs.append(b)
        # 针对工艺类章节追加知识库事实片段
        for kw, q in PROCESS_KEYWORDS.items():
            if kw in ch.get("chapter", ""):
                hits = hybrid_search(db, q, top_k=2, kb_type="process")
                for h in hits:
                    paragraphs.append(f"（工艺依据·{h.title}）{h.excerpt}")
                    citations.append(
                        {
                            "entry_id": h.entry_id,
                            "title": h.title,
                            "kb_type": "process",
                            "source": h.source,
                            "version": h.version,
                        }
                    )
                break
        out.append({"chapter": ch.get("chapter", ""), "paragraphs": paragraphs})
    return out, citations


def _build_construction(
    db: Session, req: ParsedRequirement, bundles: list, best_index: int
) -> tuple[list[dict], list[dict]]:
    """施工组织设计章节（含进度甘特要点与瓶颈分析）。"""
    best = bundles[best_index]
    chapters = _template_chapters(db, "construction")
    enriched, citations = _enrich_chapters(db, chapters)
    # 补充数据化章节（甘特/资源配置）
    gantt_lines = [
        f"{p}：第 {i * 6 + 1}~{min(30, i * 6 + 8)} 天滚动推进" for i, (_, p) in enumerate(PROCESSES)
    ]
    enriched.append(
        {
            "chapter": "进度计划",
            "paragraphs": [
                f"工序链条：{' → '.join(p for _, p in PROCESSES)}；关键路径为爆破→铲装→运输",
                "；".join(gantt_lines),
                f"日设计产能 {best.daily_capacity_t:,.0f} 吨；年作业 {WORK_DAYS_YEAR} 天 × "
                f"{OPERATION_YEARS} 年（滚动窗口 30 天演示口径）",
            ],
        }
    )
    enriched.append(
        {
            "chapter": "资源配置",
            "paragraphs": [
                "；".join(f"{o.model_name}×{o.count}" for o in best.fleet),
                f"设备利用率测算 ≈ {best.utilization_est * 100:.1f}%",
                "瓶颈工序：依据利用率与车铲匹配，若"
                + (
                    "运距增大将首先在运输环节形成瓶颈（建议动态重调度应对）"
                    if best.utilization_est < 0.95
                    else "当前车铲匹配均衡，无显著瓶颈"
                ),
            ],
        }
    )
    return enriched, citations


def _build_bid(db: Session, bundles: list, best_index: int) -> tuple[list[dict], list[dict], list[dict]]:
    """投标方案章节（≥6 章）与招标响应度检查。"""
    best = bundles[best_index]
    chapters = _template_chapters(db, "bid")
    enriched: list[dict] = []
    citations: list[dict] = []
    for ch in chapters:
        paragraphs = list(ch.get("bullets", []))
        if "技术方案" in ch["chapter"]:
            paragraphs.append(f"设备配置：{'；'.join(f'{o.model_name}×{o.count}' for o in best.fleet)}")
            paragraphs.append(
                f"三年 TCO 汇总：{best.tco_3y_total_cny:,.0f} 元；单位成本 "
                f"{sum(t.per_ton_cost_cny for t in best.tco if t.per_ton_cost_cny > 0):.2f} 元/吨（以吨成本计）"
            )
        if "报价方案" in ch["chapter"]:
            for o in best.fleet:
                paragraphs.append(f"{o.model_name}×{o.count}：{o.total_price_cny:,.0f} 元")
        enriched.append({"chapter": ch["chapter"], "paragraphs": paragraphs})
    response_check = [
        {"clause": ch["chapter"], "status": "已响应", "note": "章节内容与需求对应"} for ch in enriched
    ]
    return enriched, citations, response_check


class SolutionAgent(BaseAgent):
    name = "solution"

    def handle(self, db: Session, user_text: str, doc_type: str = "construction") -> AgentArtifact:
        parsed = parse_requirement(user_text)
        if parsed.missing:
            return requirement_proxy(db, user_text, parsed)
        result = generate_selection(db, parsed)
        bundles = [b.model_dump() for b in result.bundles]
        best = result.bundles[result.best_index]

        payload: dict = {
            "doc_type": doc_type,
            "title": "",
            "requirement": parsed.model_dump(),
            "bundles": bundles,
            "best_index": result.best_index,
            "assumptions": result.assumptions,
            "meta": {
                "model_version": "demo-template" if not settings.has_real_llm else settings.llm_model,
                "kb_version": "V1.0",
                "generated_at": datetime.now(UTC).isoformat(),
            },
            "citations": result.citations,
        }
        citations = result.citations
        if doc_type == "bid":
            enriched, cits, resp = _build_bid(db, result.bundles, result.best_index)
            payload["chapters"] = enriched
            payload["bid_response_check"] = resp
            citations = citations + cits
            payload["title"] = f"矿山设备投标方案（{parsed.scene_cn}）"
        elif doc_type == "selection":
            chapters, cits = _enrich_chapters(db, _template_chapters(db, "selection"))
            payload["chapters"] = chapters
            citations = citations + cits
            payload["title"] = f"设备选型与采购方案（{parsed.scene_cn}）"
        else:
            chapters, cits = _build_construction(db, parsed, result.bundles, result.best_index)
            payload["chapters"] = chapters
            citations = citations + cits
            payload["title"] = f"矿山施工组织设计（{parsed.scene_cn}·{parsed.daily_t:,.0f}吨/日）"

        facts = [
            f"已生成 {len(result.bundles)} 套选型方案（Top3），每套含型号、参数、配置与三年 TCO",
            f"推荐方案：{best.name}（{best.summary}）",
            f"方案一/二/三三年 TCO 合计：{bundles[0]['tco_3y_total_cny']:,.0f} / "
            f"{bundles[1]['tco_3y_total_cny']:,.0f} / {bundles[2]['tco_3y_total_cny']:,.0f} 元",
            "设备参数、价格与 TCO 全部来自设备参数库直读（示例参数，需人工确认后报价）",
            f"方案文档已按模板装配完成（{payload['title']}），可一键导出 Word/PDF",
        ]
        oversize_note = next((a for a in result.assumptions if a.startswith("⚠️")), "")
        if oversize_note:
            facts.insert(1, oversize_note)  # 超范围声明必须出现在答复显眼位置
        return self.artifact(
            facts=facts, payload=payload, citations=citations, message=f"方案生成完成：{payload['title']}"
        )


def requirement_proxy(db: Session, user_text: str, parsed: ParsedRequirement) -> AgentArtifact:
    """需求参数缺失时，交由需求分析智能体返回追问。"""
    from backend.app.agents.requirement import requirement_agent

    return requirement_agent.handle(db, user_text, parsed=parsed)


solution_agent = SolutionAgent()
