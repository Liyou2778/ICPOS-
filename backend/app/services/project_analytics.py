"""项目运营分析服务（成本构成 / 预算执行 / 工期缓冲 / 招标锚点）。

设计原则（与 scripts/train_project_models.py 的评估结论保持一致）：
  * 生产方法 = 标定统计基准（calibrated_statistical_baseline），而非 ML 预测。
    证据：本语料 138 条工期标签上训练 R²≈0.999、LOPO/5 折 CV R²<0、单特征最大 |r|≈0.12，
    任何模型都无法优于「中位数/多数类」朴素基线；ML 产物保留在
    data/models/project 但门控关闭（见 eval_report.json → deploy_decision）。
  * 所有输出都带口径说明与样本量，禁止把统计分布包装成"预测"。
  * 数字只来自数据库与标定基准文件，不做任何凭空推算。
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.models.project import Project, ProjectCost, Task

logger = logging.getLogger("icops.project_analytics")

MODEL_DIR = settings.repo_root / "data" / "models" / "project"
COST_TYPES = ["人工", "材料", "机械", "其他", "管理"]
PROCESS_CN = {
    "drilling": "穿孔凿岩",
    "blasting": "爆破",
    "loading": "铲装",
    "hauling": "运输",
    "dumping": "排土",
}


def artifacts_ready() -> bool:
    return (MODEL_DIR / "baseline_model.json").exists()


@lru_cache(maxsize=1)
def baseline() -> dict:
    f = MODEL_DIR / "baseline_model.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


@lru_cache(maxsize=1)
def model_report() -> dict:
    f = MODEL_DIR / "eval_report.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def deploy_decision() -> dict:
    rep = model_report()
    return rep.get(
        "deploy_decision",
        {
            "ml_deployed": False,
            "production_method": "calibrated_statistical_baseline",
            "reason": "尚无评估报告：请先执行 python -m scripts.train_project_models",
        },
    )


# ---------------------------------------------------------------- 招标锚点


def tender_anchor(db: Session, code: str) -> dict:
    p = db.scalar(select(Project).where(Project.code == code))
    if not p:
        raise ValueError(f"项目不存在：{code}")
    return {
        "code": p.code,
        "name": p.name,
        "tenderer": p.tenderer,
        "industry": p.industry,
        "region": p.region,
        "platform": p.platform,
        "publish_date": p.publish_date,
        "plan_invest_yuan": p.plan_invest_yuan,
        "section_est_total_yuan": p.section_est_total_yuan,
        "win_amount_yuan": p.win_amount_yuan,
        "duration_days": p.duration_days,
        "funding_source": p.funding_source,
        "approval_authority": p.approval_authority,
        "source_site": p.source_site,
        "source_url": p.source_url,
        "data_type": p.data_type,
        "budget_scope": (p.tender_meta or {}).get("budget_scope", ""),
        "note": (p.tender_meta or {}).get("note", ""),
    }


# ---------------------------------------------------------------- 成本构成


def cost_structure(db: Session, code: str) -> dict:
    p = db.scalar(select(Project).where(Project.code == code))
    if not p:
        raise ValueError(f"项目不存在：{code}")
    rows = db.execute(
        select(ProjectCost.cost_type, func.sum(ProjectCost.amount))
        .where(ProjectCost.project_id == p.id)
        .group_by(ProjectCost.cost_type)
    ).all()
    actual = {str(t): float(a or 0) for t, a in rows}
    total = sum(actual.values())
    base = baseline().get("cost_structure_pct", {})
    items = []
    out_of_band: list[str] = []
    for c in COST_TYPES:
        amt = actual.get(c, 0.0)
        share = round(amt / total * 100, 2) if total else 0.0
        ref = base.get(c, {})
        lo, hi = ref.get("p25_pct"), ref.get("p75_pct")
        dev = round(share - ref["mean_pct"], 2) if ref else None
        if lo is not None and total and (share < lo or share > hi):
            verdict = "偏高" if share > hi else "偏低"
            out_of_band.append(c)
        else:
            verdict = "正常区间" if total else "无数据"
        items.append(
            {
                "cost_type": c,
                "amount_yuan": round(amt, 2),
                "share_pct": share,
                "baseline_mean_pct": ref.get("mean_pct"),
                "band_pct": [lo, hi],
                "deviation_pp": dev,
                "verdict": verdict,
            }
        )
    total_with_budget = None
    if p.section_est_total_yuan:
        ratio = total / p.section_est_total_yuan
        r = baseline().get("cost_to_budget_ratio", {})
        warn = r.get("p75")
        total_with_budget = {
            "section_est_total_yuan": p.section_est_total_yuan,
            "total_cost_yuan": round(total, 2),
            "ratio": round(ratio, 4),
            "band": {"p25": r.get("p25"), "p50": r.get("p50"), "p75": warn, "p90": r.get("p90")},
            "level": (
                "严重偏差"
                if (r.get("p90") and ratio > r["p90"])
                else "预警"
                if (warn and ratio > warn)
                else "正常区间"
            ),
        }
    return {
        "project": {
            "code": p.code,
            "name": p.name,
            "industry": p.industry,
            "region": p.region,
            "data_type": p.data_type,
        },
        "periods": sorted(
            {
                r[0]
                for r in db.execute(
                    select(ProjectCost.period).where(ProjectCost.project_id == p.id).distinct()
                ).all()
                if r[0]
            }
        ),
        "total_cost_yuan": round(total, 2),
        "items": items,
        "out_of_band": out_of_band,
        "budget_execution": total_with_budget,
        "conclusion": _cost_conclusion(out_of_band, total_with_budget),
        "basis": "实测台账来自 proj_cost（仿真生成）；对照基准为训练集项目标定的结构百分位",
        "disclaimer": "结构偏差提示用于复核线索，不构成成本审定结论。",
    }


def _cost_conclusion(out_of_band: list[str], budget: dict | None) -> str:
    if not out_of_band and (not budget or budget["level"] == "正常区间"):
        return "成本结构与预算执行均处于历史正常区间。"
    parts = []
    if out_of_band:
        parts.append(f"成本结构偏差项：{'、'.join(out_of_band)}")
    if budget and budget["level"] != "正常区间":
        parts.append(f"预算执行 {budget['ratio']:.3f} → {budget['level']}")
    return "；".join(parts) + "。建议核查对应科目台账。"


def cost_forecast(budget_yuan: float, duration_days: int = 0) -> dict:
    """由标段预算推五类成本区间（基准结构 × 预算执行比率的 P25/P50/P75）。"""
    if budget_yuan <= 0:
        raise ValueError("budget_yuan 必须大于 0")
    struct = baseline().get("cost_structure_pct", {})
    ratio = baseline().get("cost_to_budget_ratio", {})
    out = []
    for c in COST_TYPES:
        s = struct.get(c, {})
        mean_pct = s.get("mean_pct", 0.0) / 100.0
        lo_pct = s.get("p25_pct", s.get("mean_pct", 0.0)) / 100.0
        hi_pct = s.get("p75_pct", s.get("mean_pct", 0.0)) / 100.0
        out.append(
            {
                "cost_type": c,
                "expected_yuan": round(budget_yuan * ratio.get("p50", 1.0) * mean_pct, 2),
                "range_yuan": [
                    round(budget_yuan * ratio.get("p25", 0.9) * lo_pct, 2),
                    round(budget_yuan * ratio.get("p75", 1.1) * hi_pct, 2),
                ],
                "share_pct": s.get("mean_pct"),
            }
        )
    return {
        "input": {"section_est_total_yuan": budget_yuan, "duration_days": duration_days},
        "total_cost": {
            "expected_yuan": round(budget_yuan * ratio.get("p50", 1.0), 2),
            "range_yuan": [
                round(budget_yuan * ratio.get("p25", 0.9), 2),
                round(budget_yuan * ratio.get("p75", 1.1), 2),
            ],
            "worst_case_p90_yuan": round(budget_yuan * ratio.get("p90", 1.2), 2),
        },
        "items": out,
        "method": "标定基准法：历史结构占比 × 预算执行比率分位数（非 ML 预测）",
        "sample": {
            "calibration_projects": ratio.get("n"),
            "calibration_note": baseline().get("cost_to_budget_ratio", {}).get("meaning"),
        },
        "disclaimer": "区间来自少量项目（n<20）的统计分布，用于预算编制参考，需造价人员复核。",
    }


# ---------------------------------------------------------------- 工期缓冲


def task_delay_risk(
    process: str = "", plan_days: float = 0, workload: float = 0, cycle: int = 0, device_cnt: int = 0
) -> dict:
    d = baseline().get("delay_days", {})
    by_proc = d.get("by_process", {}).get(process) if process else None
    ref = by_proc or d
    p50, p80, p90, p95 = ref.get("p50"), ref.get("p80"), ref.get("p90"), ref.get("p95")
    buffer_days = float(p80 or 0)
    level = "低"
    if p90 is not None and workload and ref.get("n", 0) and workload > 0:
        level = "中"
    if plan_days and p90 is not None and plan_days < 15:
        level = "中" if level == "低" else level
    return {
        "input": {
            "process": process or "(全部工序)",
            "plan_days": plan_days,
            "workload": workload,
            "cycle": cycle,
            "device_cnt": device_cnt,
        },
        "reference": {
            "scope": "by_process" if by_proc else "overall",
            "n": ref.get("n"),
            "p50_days": p50,
            "p80_days": p80,
            "p90_days": p90,
            "p95_days": p95,
        },
        "suggestion": {
            "buffer_days_p80": buffer_days,
            "recommended_finish_days": round(float(plan_days) + buffer_days, 1) if plan_days else None,
            "risk_level": level,
        },
        "method": "历史偏差分位数缓冲（P80 作为缓冲建议值）；非逐任务 ML 预测",
        "why_not_ml": deploy_decision().get("reason"),
        "disclaimer": "该建议基于有限样本的历史偏差分布，用于排期缓冲参考，不构成工期承诺。",
    }


# ---------------------------------------------------------------- 组合看板


def summary(db: Session) -> dict:
    n_projects = db.scalar(select(func.count(Project.id))) or 0
    corpus_projects = db.scalar(select(func.count(Project.id)).where(Project.plan_invest_yuan > 0)) or 0
    agg = db.execute(
        select(
            func.sum(Project.plan_invest_yuan),
            func.sum(Project.section_est_total_yuan),
            func.sum(Project.win_amount_yuan),
        )
    ).one()
    cost_total = db.scalar(select(func.sum(ProjectCost.amount))) or 0.0
    n_tasks = db.scalar(select(func.count(Task.id)).where(Task.task_code != "")) or 0
    done = db.scalar(select(func.count(Task.id)).where(Task.task_code != "", Task.status == "已完成")) or 0
    delayed = db.scalar(select(func.count(Task.id)).where(Task.task_code != "", Task.status == "延期")) or 0
    by_type = db.execute(
        select(ProjectCost.cost_type, func.sum(ProjectCost.amount)).group_by(ProjectCost.cost_type)
    ).all()
    by_process = db.execute(
        select(Task.process_cn, func.count(Task.id), func.sum(Task.workload))
        .where(Task.task_code != "")
        .group_by(Task.process_cn)
    ).all()
    return {
        "projects": {"total": int(n_projects), "with_tender_anchor": int(corpus_projects)},
        "investment": {
            "plan_invest_yuan": round(float(agg[0] or 0), 2),
            "section_est_total_yuan": round(float(agg[1] or 0), 2),
            "win_amount_yuan": round(float(agg[2] or 0), 2),
        },
        "cost": {
            "total_yuan": round(float(cost_total), 2),
            "by_type": [
                {
                    "cost_type": t,
                    "amount_yuan": round(float(a or 0), 2),
                    "share_pct": round(float(a or 0) / cost_total * 100, 2) if cost_total else 0,
                }
                for t, a in sorted(by_type, key=lambda x: -float(x[1] or 0))
            ],
            "baseline_structure_pct": {
                k: v.get("mean_pct") for k, v in baseline().get("cost_structure_pct", {}).items()
            },
        },
        "tasks": {
            "total": int(n_tasks),
            "completed": int(done),
            "delayed": int(delayed),
            "by_process": [
                {"process": p, "count": int(c), "workload": round(float(w or 0), 2)} for p, c, w in by_process
            ],
        },
        "budget_execution_band": baseline().get("cost_to_budget_ratio"),
        "deploy": deploy_decision(),
        "data_boundary": model_report().get("limitations", []),
    }


def project_list(db: Session, limit: int = 100) -> list[dict]:
    rows = db.execute(
        select(
            Project.code,
            Project.name,
            Project.industry,
            Project.region,
            Project.data_type,
            Project.section_est_total_yuan,
            Project.plan_invest_yuan,
            Project.duration_days,
            func.coalesce(func.sum(ProjectCost.amount), 0.0),
        )
        .outerjoin(ProjectCost, ProjectCost.project_id == Project.id)
        .group_by(Project.id)
        .limit(limit)
    ).all()
    out = []
    for code, name, industry, region, dtype, est, invest, dur, cost in rows:
        ratio = round(float(cost) / est, 4) if est and cost else None
        out.append(
            {
                "code": code,
                "name": name,
                "industry": industry,
                "region": region,
                "data_type": dtype,
                "section_est_total_yuan": est,
                "plan_invest_yuan": invest,
                "duration_days": dur,
                "total_cost_yuan": round(float(cost or 0), 2),
                "cost_to_budget_ratio": ratio,
            }
        )
    return out
