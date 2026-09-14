"""用真实企业案例验证方案智能体：需求解析 · 配置合理性 · 报价量级 · 诚实性边界。

设计原则（企业级验证，不做自我美化的对齐）：
  1. 真实结果只作为**参照区间**，不当作必须命中的"标准答案"——案例与本项目参数库的
     口径差异（品类/规模/统计口径）逐条写明，不可比的地方不下结论。
  2. 分维度分别判定：解析准确性（可硬判）、配置合理性（与真实锚点比区间）、
     报价量级（品类不同只做偏差披露）、诚实性（超范围是否声明）、防幻觉（数字能否溯源）。
  3. 失败即失败：门控判据写死在脚本里，不因"演示需要"放宽。

用法：python -m scripts.validate_agent_with_real_cases
产物：data/validation/agent_validation_report.json（逐案例逐维度结果）
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import select

from backend.app.core.config import settings
from backend.app.core.db import SessionLocal
from backend.app.models import EquipmentModel
from backend.app.schemas.domain import ParsedRequirement
from backend.app.services.requirements import parse_requirement
from backend.app.services.selection import (
    OPERATION_YEARS,
    WORK_DAYS_YEAR,
    WORK_HOURS_DAY,
    generate_selection,
    truck_tph,
)

CASES = settings.repo_root / "data" / "validation" / "real_cases.json"
OUT = settings.repo_root / "data" / "validation" / "agent_validation_report.json"
DENSITY = 2.6
TRUCK_EXC_RATIO_BAND = (3.0, 8.0)  # 行业经验车铲比区间（露天矿间断工艺常见 3~6，放宽上界 8）


def _load_cases() -> dict:
    return json.loads(CASES.read_text(encoding="utf-8"))


def _catalog(db) -> dict[str, EquipmentModel]:
    rows = db.scalars(select(EquipmentModel)).all()
    return {m.model_name: m for m in rows}


def check_parse(case: dict) -> dict:
    """C1 需求解析：真实数字能否被规则引擎正确结构化（硬判）。"""
    req = parse_requirement(case["agent_probe_text"])
    exp = case["requirement"]
    got = {
        "scene_type": req.scene_type,
        "annual_t": req.annual_t,
        "annual_m3": round(req.annual_t / DENSITY, 1) if req.annual_t else 0.0,
        "duration_years": req.duration_years,
        "budget_cny": req.budget_cny,
        "constraints": req.constraints,
        "missing": req.missing,
    }
    checks: list[dict] = []
    if exp.get("annual_t"):
        dev = abs(got["annual_t"] - exp["annual_t"]) / exp["annual_t"]
        checks.append(
            {
                "item": "年工程量解析",
                "expected": exp["annual_t"],
                "got": got["annual_t"],
                "rel_dev": round(dev, 5),
                "pass": dev < 0.01,
            }
        )
    if exp.get("duration_years"):
        dev = abs(got["duration_years"] - exp["duration_years"]) / exp["duration_years"]
        checks.append(
            {
                "item": "工期解析",
                "expected": exp["duration_years"],
                "got": got["duration_years"],
                "rel_dev": round(dev, 5),
                "pass": dev < 0.01,
            }
        )
    if exp.get("budget_cny"):
        dev = abs(got["budget_cny"] - exp["budget_cny"]) / exp["budget_cny"]
        checks.append(
            {
                "item": "预算解析",
                "expected": exp["budget_cny"],
                "got": got["budget_cny"],
                "rel_dev": round(dev, 5),
                "pass": dev < 0.01,
            }
        )
    if exp.get("constraints"):
        hit = [c for c in exp["constraints"] if c in "".join(got["constraints"])]
        # 案例约束（纯电动/无人驾驶/交货期）不要求全部映射为槽位约束，只记录映射情况
        checks.append(
            {
                "item": "约束识别（记录项，不判失败）",
                "expected": exp["constraints"],
                "got": got["constraints"],
                "matched": hit,
                "pass": True,
            }
        )
    return {"parsed": got, "checks": checks, "pass": all(c["pass"] for c in checks) if checks else None}


def check_configuration(db, case: dict, catalog: dict) -> dict:
    """C2/C3 配置合理性：机型是否来自参数库、车铲比、单机产能与真实锚点对比。"""
    exp = case["requirement"]
    if not exp.get("annual_t"):
        return {
            "applicable": False,
            "checks": [],
            "note": "该真实案例为设备采购类（公告未披露年工程量），无法做产能配置校验；"
            "仅做需求解析、阻塞追问与单价对照。",
        }
    req = ParsedRequirement(
        scene_type="mining",
        scene_cn="露天矿山开采",
        annual_t=exp.get("annual_t") or 0.0,
        duration_years=exp.get("duration_years") or 1.0,
        budget_cny=exp.get("budget_cny") or 0.0,
        raw=case["agent_probe_text"],
    )
    req.daily_t = req.annual_t / WORK_DAYS_YEAR if req.annual_t else 0.0
    req.total_t = req.annual_t * req.duration_years
    res = generate_selection(db, req)
    best = res.bundles[res.best_index]

    exc = next((f for f in best.fleet if "挖掘机" in f.model_name), None)
    trucks = [f for f in best.fleet if "自卸车" in f.model_name]
    truck = trucks[0] if trucks else None

    out: dict = {
        "required_daily_t": round(req.daily_t, 1),
        "best_bundle": {
            "name": best.name,
            "fleet": [
                {
                    "model": f.model_name,
                    "count": f.count,
                    "unit_price_cny": f.unit_price_cny,
                    "total_price_cny": f.total_price_cny,
                }
                for f in best.fleet
            ],
            "daily_capacity_t": best.daily_capacity_t,
            "utilization_est": best.utilization_est,
            "fleet_total_cny": best.fleet_total_cny,
            "tco_3y_total_cny": best.tco_3y_total_cny,
            "coverage_ratio": round(best.daily_capacity_t / req.daily_t, 3) if req.daily_t else None,
            "note": best.note,
        },
        "assumptions": res.assumptions,
        "checks": [],
    }

    # C2 防幻觉：机型必须全部来自参数库
    names = [f.model_name for b in res.bundles for f in b.fleet]
    unknown = [n for n in names if n not in catalog]
    out["checks"].append(
        {"item": "机型全部来自参数库（防幻觉）", "unknown_models": unknown, "pass": not unknown}
    )

    # C2b 数字溯源：购置总额必须等于 单价×台数 之和
    recomputed = sum(f.unit_price_cny * f.count for f in best.fleet)
    out["checks"].append(
        {
            "item": "购置总额可复算（单价×台数）",
            "reported": best.fleet_total_cny,
            "recomputed": round(recomputed, 2),
            "pass": abs(recomputed - best.fleet_total_cny) < 1.0,
        }
    )

    # C3 车铲比
    if exc and truck:
        ratio = truck.count / max(exc.count, 1)
        lo, hi = TRUCK_EXC_RATIO_BAND
        out["truck_to_excavator_ratio"] = round(ratio, 2)
        out["checks"].append(
            {"item": f"车铲比落在行业区间 {lo}~{hi}", "got": round(ratio, 2), "pass": lo <= ratio <= hi}
        )

    # C3b 单车日产能 vs 真实锚点（仅白音华 2025 案例有该锚点）
    bench = case.get("derived_benchmarks", {})
    if truck and bench.get("per_truck_daily_t_scopeA"):
        per_truck_t = truck_tph(catalog[truck.model_name], catalog[exc.model_name]) * WORK_HOURS_DAY
        lo_t, hi_t = bench["per_truck_daily_t_scopeB"], bench["per_truck_daily_t_scopeA"]
        within = lo_t * 0.5 <= per_truck_t <= hi_t * 1.5
        out["per_truck_daily_t"] = round(per_truck_t, 1)
        out["checks"].append(
            {
                "item": "单车日产能 vs 真实案例锚点（±50% 容差）",
                "model_value_t": round(per_truck_t, 1),
                "real_band_t": [lo_t, hi_t],
                "ratio_to_band_mid": round(per_truck_t / ((lo_t + hi_t) / 2), 3),
                "pass": within,
            }
        )
    return out


def check_scale_honesty(case: dict, config: dict) -> dict:
    """C4 诚实性：需求规模超出参数库单项目建议配置时，输出是否显式声明适用范围。

    判定口径：以**推荐方案实际需要的台数**是否超出单项目建议上限为准
    （而非"单机产能倍数"这类易误判的粗口径——白音华 806 万 m³/年 = 3 挖 19 车，属正常范围）。
    """
    if not config.get("applicable", True):
        return {
            "out_of_scope": False,
            "applicable": False,
            "pass": None,
            "declared_limit_in_output": False,
            "note": "该案例无工程量，规模适用性判定不适用",
        }
    from backend.app.services.selection import SINGLE_PROJECT_MAX_EXC, SINGLE_PROJECT_MAX_TRUCK

    fleet = config["best_bundle"]["fleet"]
    n_exc = max((f["count"] for f in fleet if "挖掘机" in f["model"]), default=0)
    n_truck = max((f["count"] for f in fleet if "自卸车" in f["model"]), default=0)
    out_of_range = n_exc > SINGLE_PROJECT_MAX_EXC or n_truck > SINGLE_PROJECT_MAX_TRUCK
    text = json.dumps(config, ensure_ascii=False)
    declared = (
        "超出" in text and ("适用范围" in text or "建议配置上限" in text)
    ) or "不得直接用于报价" in text
    return {
        "out_of_scope": out_of_range,
        "recommended_fleet": {"excavator": n_exc, "truck": n_truck},
        "limits": {"excavator": SINGLE_PROJECT_MAX_EXC, "truck": SINGLE_PROJECT_MAX_TRUCK},
        "declared_limit_in_output": declared,
        "pass": (not out_of_range) or declared,
        "note": "规模超限时必须在输出中显式声明适用范围；未声明则记为待改进项（不掩饰）",
    }


def check_price_reference(db, case: dict, catalog: dict) -> dict:
    """C5 报价量级：参数库单价 vs 真实成交单价（品类不同时只披露偏差，不判失败）。"""
    bench = case.get("derived_benchmarks", {})
    real = bench.get("unit_price_cny_range") or (
        [bench["unit_price_cny"]] if bench.get("unit_price_cny") else None
    )
    if not real:
        return {"applicable": False, "note": bench.get("scope_note", "该案例无可用单价锚点")}
    # 取参数库中最接近的吨位机型（75~80 吨级）
    cand = [m for m in catalog.values() if m.rated_load_t and 60 <= m.rated_load_t <= 90]
    if not cand:
        return {"applicable": False, "note": "参数库无 60~90 吨级矿卡"}
    pick = min(cand, key=lambda m: abs(m.rated_load_t - 75))
    real_mid = sum(real) / len(real)
    return {
        "applicable": True,
        "catalog_model": pick.model_name,
        "catalog_rated_load_t": pick.rated_load_t,
        "catalog_unit_price_cny": pick.price_cny,
        "real_unit_price_cny_range": real,
        "ratio_catalog_over_real": round(pick.price_cny / real_mid, 3),
        "pass": None,
        "note": "品类不同（柴油刚性矿卡 vs 纯电宽体）+ 参数库标注'示例数据'，故不做通过/失败判定，"
        "仅作为参数库单价需用真实采购数据校准的证据。",
    }


def check_blocking_behaviour(db, case: dict) -> dict:
    """C6 阻塞/放行行为：参数不全应追问，参数齐备应直接生成方案。

    判定语义（双向）：
      * 真实公告缺年工程量/工期/预算 → 期望 need_more=True（避免产出无效方案）；
      * 参数齐备（如含总工程量+工期+预算的剥离工程） → 期望直接生成方案，而不是无谓追问。
    """
    from backend.app.agents.solution import solution_agent

    exp = case["requirement"]
    complete = bool(exp.get("annual_t") and exp.get("duration_years") and exp.get("budget_cny"))
    art = solution_agent.handle(db, case["agent_probe_text"], doc_type="selection")
    blocked = art.agent == "requirement"
    return {
        "agent": art.agent,
        "params_complete": complete,
        "expected_behaviour": "直接生成方案" if complete else "阻塞并追问",
        "actual_blocked": blocked,
        "pass": blocked != complete,
        "note": "参数不全却硬做方案、或参数齐备却反复追问，都属异常行为",
    }


def main() -> int:
    data = _load_cases()
    cases = data["cases"]
    db = SessionLocal()
    try:
        catalog = _catalog(db)
        results = []
        for case in cases:
            cfg = check_configuration(db, case, catalog)
            r = {
                "id": case["id"],
                "title": case["title"],
                "source_tier": case["source_tier"],
                "source_url": case["source_url"],
                "parse": check_parse(case),
                "configuration": cfg,
                "scale_honesty": check_scale_honesty(case, cfg),
                "blocking": check_blocking_behaviour(db, case),
                "price_reference": check_price_reference(db, case, catalog),
            }
            r["configuration_checks_pass"] = (
                all(c["pass"] for c in cfg["checks"]) if cfg.get("applicable", True) else None
            )
            results.append(r)
    finally:
        db.close()

    summary = {
        "cases": len(results),
        "parse_pass": sum(1 for r in results if r["parse"]["pass"]),
        "config_applicable": sum(1 for r in results if r["configuration"].get("applicable", True)),
        "config_pass": sum(1 for r in results if r["configuration_checks_pass"]),
        "blocking_pass": sum(1 for r in results if r["blocking"]["pass"]),
        "scale_honesty_applicable": sum(1 for r in results if r["scale_honesty"]["pass"] is not None),
        "scale_honesty_pass": sum(1 for r in results if r["scale_honesty"]["pass"]),
        "out_of_scope_cases": [r["id"] for r in results if r["scale_honesty"]["out_of_scope"]],
    }
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset": data["dataset"],
        "constants": {
            "density_t_per_m3": DENSITY,
            "work_days_year": WORK_DAYS_YEAR,
            "work_hours_day": WORK_HOURS_DAY,
            "tco_years": OPERATION_YEARS,
            "truck_excavator_band": TRUCK_EXC_RATIO_BAND,
        },
        "summary": summary,
        "results": results,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("[validate_agent_with_real_cases] 真实案例验证")
    for r in results:
        cfg = r["configuration"]
        print(f"\n— {r['id']} [{r['source_tier']}]")
        print(f"  解析：{'✅' if r['parse']['pass'] else '❌'} {r['parse']['parsed']}")
        if cfg.get("applicable", True):
            print(
                f"  配置：{'✅' if r['configuration_checks_pass'] else '❌'} "
                f"推荐={cfg['best_bundle']['name']} 日产能={cfg['best_bundle']['daily_capacity_t']:,.0f}t"
                f"(需求 {cfg['required_daily_t']:,.0f}t, 覆盖 {cfg['best_bundle']['coverage_ratio']})"
                f" 车铲比={cfg.get('truck_to_excavator_ratio')}"
            )
        else:
            print(f"  配置：不适用（{cfg['note'][:50]}）")
        print(
            f"  阻塞/放行：{'✅' if r['blocking']['pass'] else '❌'} "
            f"参数齐备={r['blocking']['params_complete']} "
            f"实测={'追问' if r['blocking']['actual_blocked'] else '生成方案'} "
            f"（期望 {r['blocking']['expected_behaviour']}）"
        )
        sh = r["scale_honesty"]
        mark = "—" if sh["pass"] is None else ("✅" if sh["pass"] else "❌")
        print(
            f"  规模诚实性：{mark} 超范围={sh['out_of_scope']} "
            f"已声明={sh['declared_limit_in_output']}"
            + (f" 配置={sh.get('recommended_fleet')}" if sh.get("recommended_fleet") else "")
        )
        pr = r["price_reference"]
        if pr.get("applicable"):
            print(
                f"  单价参照：参数库 {pr['catalog_model']} {pr['catalog_unit_price_cny']:,.0f} 元/台 "
                f"vs 真实 {pr['real_unit_price_cny_range']} → 倍数 {pr['ratio_catalog_over_real']}"
            )
        else:
            print(f"  单价参照：不适用（{pr.get('note', '')[:60]}）")
    print("\n汇总：", json.dumps(summary, ensure_ascii=False))
    print(f"报告：{OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
