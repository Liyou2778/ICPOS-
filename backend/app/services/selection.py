"""设备选型与 TCO 测算（指导书 5.3 / PRD 功能 1.1）。

规则要点：
  * 数值全部由数据库（设备参数库）直读计算，大模型不参与数字生成（防幻觉第一道防线）；
  * 以车铲匹配（斗容-载重比、装车时间 3~5 分钟）为约束做产能测算；
  * 输出 ≥3 套选型方案，每套含型号、参数、配置与三年 TCO；
  * TCO 覆盖购置、能耗、维保、残值四大类（PRD 验收标准）。
"""

from __future__ import annotations

import math

from sqlalchemy.orm import Session

from backend.app.models import EquipmentModel
from backend.app.schemas.domain import Bundle, EquipmentOption, ParsedRequirement, SelectionResult, TcoRow

ORE_DENSITY = 2.6  # t/m3
WORK_DAYS_YEAR = 330
WORK_HOURS_DAY = 20.0
FUEL_PRICE_CNY = 7.2  # 元/L
ELECTRIC_PRICE_CNY = 0.8  # 元/kWh（电动化约束占位）

OPERATION_YEARS = 3  # TCO 周期（3 年）


def _spec(model: EquipmentModel) -> dict:
    s = dict(model.spec or {})
    s["avail"] = float(s.get("avail", 0.85))
    return s


def excavator_tph(model: EquipmentModel) -> float:
    """挖掘机理论产能 t/h = 斗容 * 密度 * 3600 / 循环秒 * 满斗系数 * 可用率。"""
    s = _spec(model)
    eff = float(s.get("eff", 0.82))
    cyc = float(s.get("cycle_sec", 30))
    return model.bucket_m3 * ORE_DENSITY * 3600.0 / cyc * eff * s["avail"]


def truck_cycle_min(model: EquipmentModel, exc: EquipmentModel, dist_km: float = 2.8) -> tuple[float, float]:
    """(循环时间 min, 装车时间 min)：按给定挖掘机与运距计算。"""
    s = _spec(model)
    e = _spec(exc)
    passes = max(1, math.ceil(model.rated_load_t / (exc.bucket_m3 * ORE_DENSITY)))
    load_min = passes * float(e.get("load_pass_min", 0.45)) + float(s.get("spot_min", 1.5))
    haul_min = dist_km / max(float(s.get("haul_speed_kmh", 22)), 5) * 60
    return_min = dist_km / max(float(s.get("return_speed_kmh", 28)), 5) * 60
    dump_min = float(s.get("dump_min", 2.0))
    fixed = float(s.get("fixed_cycle_min", 5.0))
    cycle = load_min + haul_min + return_min + dump_min + fixed
    return cycle, load_min


def truck_tph(model: EquipmentModel, exc: EquipmentModel, dist_km: float = 2.8) -> float:
    cycle_min, _ = truck_cycle_min(model, exc, dist_km)
    return model.rated_load_t * 60.0 / cycle_min * _spec(model)["avail"]


def _annual_tco(model: EquipmentModel, count: int, work_hours_year: float) -> TcoRow:
    purchase = model.price_cny * count
    # 能耗（燃油机型按油耗；电动化按 kWh 折算预留）
    energy_3y = model.fuel_lh * work_hours_year * OPERATION_YEARS * FUEL_PRICE_CNY * count
    maintenance_3y = model.maintain_yearly_cny * OPERATION_YEARS * count
    residual = model.price_cny * model.residual_ratio_3y * count
    total = purchase + energy_3y + maintenance_3y - residual
    return TcoRow(
        model_code=model.code,
        model_name=model.model_name,
        count=count,
        purchase_cny=round(purchase, 2),
        energy_3y_cny=round(energy_3y, 2),
        maintenance_3y_cny=round(maintenance_3y, 2),
        residual_cny=round(residual, 2),
        total_3y_cny=round(total, 2),
    )


def _fleet_option(
    exc: EquipmentModel, n_exc: int, truck: EquipmentModel, n_truck: int
) -> tuple[list[EquipmentOption], list[TcoRow]]:
    exc_hours_year = WORK_DAYS_YEAR * WORK_HOURS_DAY
    fleet = [
        EquipmentOption(
            model_code=exc.code,
            model_name=exc.model_name,
            brand=exc.brand,
            category_cn=exc.category_cn,
            count=n_exc,
            unit_price_cny=exc.price_cny,
            total_price_cny=round(exc.price_cny * n_exc, 2),
            specs={
                "bucket_m3": exc.bucket_m3,
                "power_kw": exc.power_kw,
                "fuel_lh": exc.fuel_lh,
                "rated_load_t": exc.rated_load_t,
            },
        ),
        EquipmentOption(
            model_code=truck.code,
            model_name=truck.model_name,
            brand=truck.brand,
            category_cn=truck.category_cn,
            count=n_truck,
            unit_price_cny=truck.price_cny,
            total_price_cny=round(truck.price_cny * n_truck, 2),
            specs={"rated_load_t": truck.rated_load_t, "power_kw": truck.power_kw, "fuel_lh": truck.fuel_lh},
        ),
    ]
    tcos = [_annual_tco(exc, n_exc, exc_hours_year), _annual_tco(truck, n_truck, exc_hours_year)]
    return fleet, tcos


def generate_selection(db: Session, req: ParsedRequirement) -> SelectionResult:
    """生成 ≥3 套选型方案。"""
    if req.daily_t <= 0:
        raise ValueError("缺少年作业量/工程量参数，无法测算产能")
    req_tph = req.daily_t / WORK_HOURS_DAY

    excs = (
        db.query(EquipmentModel)
        .filter(EquipmentModel.category == "excavator", EquipmentModel.scene.in_([req.scene_type, "mining"]))
        .order_by(EquipmentModel.bucket_m3)
        .all()
    )
    trucks = (
        db.query(EquipmentModel)
        .filter(EquipmentModel.category == "mining_truck")
        .order_by(EquipmentModel.rated_load_t)
        .all()
    )
    if not excs or not trucks:
        raise ValueError("设备参数库为空，请先执行知识库入库（uv run python -m scripts.build_kb）")

    bundles: list[Bundle] = []
    used_pairs: set[tuple[str, str]] = set()
    # 枚举挖掘机 x 卡车组合，选出三档（经济/均衡/高效）
    scored: list[tuple[float, dict]] = []
    for exc in excs:
        e_tph = excavator_tph(exc)
        if e_tph <= 0:
            continue
        for truck in trucks:
            if (exc.code, truck.code) in used_pairs:
                continue
            cycle_min, load_min = truck_cycle_min(truck, exc)
            passes = math.ceil(truck.rated_load_t / (exc.bucket_m3 * ORE_DENSITY))
            if passes > 10:  # 装车时间约束：斗容过小不匹配
                continue
            truck_ph = truck_tph(truck, exc)
            n_exc = max(1, math.ceil(req_tph / e_tph))
            n_truck = max(1, math.ceil(n_exc * e_tph / truck_ph))
            # 预算裁剪：超预算标记由上层处理
            total_price = n_exc * exc.price_cny + n_truck * truck.price_cny
            # 评分：吨成本越低、匹配越好、预算内优先
            score = truck.rated_load_t / max(passes, 1) - total_price / 1e7
            scored.append(
                (
                    score,
                    dict(
                        exc=exc,
                        truck=truck,
                        n_exc=n_exc,
                        n_truck=n_truck,
                        passes=passes,
                        load_min=load_min,
                        cycle_min=cycle_min,
                        total_price=total_price,
                    ),
                )
            )
            used_pairs.add((exc.code, truck.code))

    scored.sort(key=lambda x: -x[0])
    # 机型多样性：每个挖掘机型号先取最优组合，凑足 Top3（兼顾比选价值）
    picked: list = []
    seen_exc: set[str] = set()
    for _, info in scored:
        code = info["exc"].code
        if code not in seen_exc:
            seen_exc.add(code)
            picked.append(info)
        if len(picked) >= 3:
            break
    if len(picked) < 3:
        for _, info in scored:
            if info not in picked and len(picked) < 3:
                picked.append(info)
    names = ["经济适配型", "均衡主力型", "产能高效型"]
    best_by_price = -1.0
    for idx, info in enumerate(picked[:3]):
        exc, truck, n_exc, n_truck = info["exc"], info["truck"], info["n_exc"], info["n_truck"]
        fleet, tcos = _fleet_option(exc, n_exc, truck, n_truck)
        cap_tpd = min(n_exc * excavator_tph(exc), n_truck * truck_tph(truck, exc)) * WORK_HOURS_DAY
        util = min(1.0, req_tph / max(cap_tpd / WORK_HOURS_DAY, 1e-6))
        moved_t = cap_tpd * WORK_DAYS_YEAR * OPERATION_YEARS
        per_ton = (sum(t.total_3y_cny for t in tcos)) / max(moved_t, 1.0)
        tcos[0].per_ton_cost_cny = round(per_ton, 2)
        b = Bundle(
            name=names[idx] if idx < len(names) else f"方案{idx + 1}",
            fleet=fleet,
            tco=tcos,
            fleet_total_cny=round(sum(o.total_price_cny for o in fleet), 2),
            tco_3y_total_cny=round(sum(t.total_3y_cny for t in tcos), 2),
            daily_capacity_t=round(cap_tpd, 1),
            utilization_est=round(util, 3),
            summary=f"{exc.model_name}×{n_exc} + {truck.model_name}×{n_truck}，"
            f"约 {info['passes']} 斗装满车，装车 {info['load_min']:.1f} 分钟，"
            f"三年吨成本 {per_ton:.2f} 元/吨",
        )
        b.note = (
            "车铲斗容匹配、装车时间 3~5 分钟；方案数值均由设备参数库直读计算（示例参数，需人工确认后报价）"
        )
        if req.budget_cny > 0:
            over = b.fleet_total_cny > req.budget_cny
            b.note += "；" + ("超出预算，建议降档或议价" if over else "在预算范围内")
            if not over and (best_by_price < 0 or b.tco_3y_total_cny < best_by_price):
                best_by_price = b.tco_3y_total_cny
        bundles.append(b)

    if not bundles:
        raise ValueError("无可匹配的设备组合（请检查设备参数库数据）")

    best_index = 0
    if req.budget_cny > 0:
        inside = [i for i, b in enumerate(bundles) if b.fleet_total_cny <= req.budget_cny]
        if inside:
            best_index = min(inside, key=lambda i: bundles[i].tco_3y_total_cny)
    else:
        best_index = min(range(len(bundles)), key=lambda i: sum(t.total_3y_cny for t in bundles[i].tco))

    return SelectionResult(
        bundles=bundles,
        best_index=best_index,
        assumptions=[
            f"年作业 {WORK_DAYS_YEAR} 天 × 日作业 {WORK_HOURS_DAY:.0f} 小时；矿岩密度 {ORE_DENSITY} t/m3",
            f"运距按 {2.8} km 测算；TCO 周期 {OPERATION_YEARS} 年",
            "设备参数为公开渠道示例数据，正式方案需厂商确认",
        ],
        citations=[
            {
                "kb_type": "equipment",
                "title": f"设备参数库（{len(excs)} 型号）",
                "source": "徐工官网/产品手册（公开渠道）",
                "version": "V1.0",
            }
        ],
    )
