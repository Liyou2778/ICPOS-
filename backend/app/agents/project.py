"""项目运营智能体（真实招标锚点 + 成本台账 + 标定基准）。

职责：回答"项目/标段预算/中标金额/成本构成/预算执行/工期缓冲"类问题。
防幻觉第一道防线：所有数字直接来自数据库与 data/models/project/baseline_model.json，
LLM 只负责组织语言；方法口径与"为何不使用 ML 预测"随答复一并给出。
"""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from backend.app.agents.base import AgentArtifact
from backend.app.services import project_analytics as pj

# 招标公告项目编号（如 B1504032026081301 / K1502002025031102007）与内部项目编号（P-MINING-001）
TENDER_CODE_RE = re.compile(r"\b([BEFKPW]\d{12,19})\b")
INNER_CODE_RE = re.compile(r"\b([A-Z]{1,3}-[A-Z]{2,10}-\d{2,4})\b")
BUDGET_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(亿元|万元|万|元)")

PROJECT_WORDS = (
    "标段",
    "中标",
    "招标人",
    "成本构成",
    "成本台账",
    "预算执行",
    "计划投资",
    "资金来源",
    "项目清单",
    "项目编号",
    "造价",
    "结算",
    "项目库",
    "在建项目",
    "项目情况",
    "有哪些项目",
)


def _yuan(text: str) -> float | None:
    m = BUDGET_RE.search(text)
    if not m:
        return None
    v = float(m.group(1))
    unit = m.group(2)
    if unit == "亿元":
        return v * 1e8
    if unit in ("万元", "万"):
        return v * 1e4
    return v


def _fmt_yuan(v: float) -> str:
    if not v:
        return "—"
    if v >= 1e8:
        return f"{v / 1e8:.4f} 亿元"
    if v >= 1e4:
        return f"{v / 1e4:.2f} 万元"
    return f"{v:,.0f} 元"


def handle(db: Session, text: str) -> AgentArtifact:
    tender = TENDER_CODE_RE.search(text.upper())
    inner = INNER_CODE_RE.search(text.upper())
    code = (tender or inner).group(1) if (tender or inner) else None

    # 1) 指定项目 → 招标锚点 + 成本构成 + 预算执行
    if code:
        try:
            anchor = pj.tender_anchor(db, code)
        except ValueError:
            anchor = None
        if anchor:
            facts = [
                f"项目【{anchor['code']}】{anchor['name']}",
                f"招标人：{anchor['tenderer']}；行业：{anchor['industry']}；地区：{anchor['region']}",
                f"计划总投资：{_fmt_yuan(anchor['plan_invest_yuan'])}；"
                f"标段预算合计：{_fmt_yuan(anchor['section_est_total_yuan'])}；"
                f"中标金额：{_fmt_yuan(anchor['win_amount_yuan'])}",
                f"计划工期：{anchor['duration_days']} 天；资金来源：{anchor['funding_source'] or '—'}；"
                f"公告日期：{anchor['publish_date'] or '—'}",
            ]
            citations = [
                {
                    "title": f"招标公告 {anchor['code']}",
                    "source": anchor["source_site"] or "项目语料",
                    "url": anchor["source_url"],
                    "kb_type": "project",
                }
            ]
            payload = {"tender_anchor": anchor}
            if pj.artifacts_ready():
                cs = pj.cost_structure(db, code)
                payload["cost_structure"] = cs
                if cs["total_cost_yuan"]:
                    facts.append(
                        f"成本台账合计：{_fmt_yuan(cs['total_cost_yuan'])}（台账期间 "
                        f"{'、'.join(cs['periods']) or '—'}）"
                    )
                    facts.append(
                        "成本构成："
                        + "；".join(
                            f"{i['cost_type']} {i['share_pct']}%（基准 {i['baseline_mean_pct']}%，{i['verdict']}）"
                            for i in cs["items"]
                        )
                    )
                be = cs.get("budget_execution")
                if be:
                    facts.append(
                        f"预算执行比率：{be['ratio']:.4f}（历史区间 "
                        f"{be['band']['p25']}~{be['band']['p75']}）→ {be['level']}"
                    )
                facts.append(f"结论：{cs['conclusion']}")
                facts.append(f"口径：{cs['basis']}；{cs['disclaimer']}")
            return AgentArtifact(
                agent="project",
                facts=facts,
                payload=payload,
                citations=citations,
                message="项目运营档案（招标锚点 + 成本构成）",
            )

    # 2) 依据预算做成本结构测算
    budget = _yuan(text)
    if budget and pj.artifacts_ready():
        fc = pj.cost_forecast(budget)
        facts = [
            f"按标段预算 {_fmt_yuan(budget)} 测算（标定基准法）：",
            f"预计总成本 {_fmt_yuan(fc['total_cost']['expected_yuan'])}，"
            f"合理区间 {_fmt_yuan(fc['total_cost']['range_yuan'][0])} ~ "
            f"{_fmt_yuan(fc['total_cost']['range_yuan'][1])}，"
            f"P90 不利情形 {_fmt_yuan(fc['total_cost']['worst_case_p90_yuan'])}",
        ]
        facts += [
            f"· {i['cost_type']}：{_fmt_yuan(i['expected_yuan'])}"
            f"（区间 {_fmt_yuan(i['range_yuan'][0])} ~ {_fmt_yuan(i['range_yuan'][1])}）"
            for i in fc["items"]
        ]
        facts += [
            f"方法：{fc['method']}（标定项目数 n={fc['sample']['calibration_projects']}）",
            fc["disclaimer"],
        ]
        return AgentArtifact(
            agent="project",
            facts=facts,
            payload={"cost_forecast": fc},
            message="成本结构测算（标定基准法，非 ML 预测）",
        )

    # 3) 组合看板
    if not pj.artifacts_ready():
        return AgentArtifact(
            agent="project",
            transfer=True,
            facts=["项目分析基准未生成：请先执行 python -m scripts.train_project_models"],
            message="项目分析基准缺失",
        )
    s = pj.summary(db)
    projects = sorted(pj.project_list(db), key=lambda p: -(p["section_est_total_yuan"] or 0))
    facts = [
        f"项目库：{s['projects']['total']} 个项目（含招标锚点 {s['projects']['with_tender_anchor']} 个）",
        f"计划总投资合计：{_fmt_yuan(s['investment']['plan_invest_yuan'])}；"
        f"标段预算合计：{_fmt_yuan(s['investment']['section_est_total_yuan'])}；"
        f"中标金额合计：{_fmt_yuan(s['investment']['win_amount_yuan'])}",
        f"成本台账合计：{_fmt_yuan(s['cost']['total_yuan'])}；构成："
        + "；".join(f"{c['cost_type']} {c['share_pct']}%" for c in s["cost"]["by_type"]),
        f"施工任务 {s['tasks']['total']} 项（已完成 {s['tasks']['completed']}、延期 {s['tasks']['delayed']}）",
    ]
    top = [p for p in projects if p["section_est_total_yuan"]][:5]
    if top:
        facts.append("标段预算前 5 项目：")
        facts += [
            f"· {p['code']} {p['name'][:28]}｜预算 {_fmt_yuan(p['section_est_total_yuan'])}"
            f"｜成本 {_fmt_yuan(p['total_cost_yuan'])}"
            + (f"｜执行 {p['cost_to_budget_ratio']:.3f}" if p["cost_to_budget_ratio"] else "")
            for p in top
        ]
    d = s["deploy"]
    facts.append(
        f"分析方法：{d['production_method']}（ML 上线={d['ml_deployed']}）；原因：{d['reason'][:80]}"
    )
    return AgentArtifact(
        agent="project",
        facts=facts,
        payload={"summary": s, "projects": top},
        message="项目组合看板（招标锚点 + 成本台账 + 标定基准）",
    )


def is_project_question(text: str) -> bool:
    """项目类问题判定：命中项目术语，或"项目"且不含方案生成意图。"""
    if TENDER_CODE_RE.search(text.upper()) or INNER_CODE_RE.search(text.upper()):
        return True
    return any(w in text for w in PROJECT_WORDS)
