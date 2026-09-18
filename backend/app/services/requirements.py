"""需求分析：自然语言需求 -> 结构化参数（指导书 5.3 / PRD 表12）。

解析策略（防幻觉三道防线-数字不出模型）：关键参数一律由规则引擎从文本
结构化提取；真实模式可叠加 LLM 追问话术，但字段校验仍以规则输出为准。
"""

from __future__ import annotations

import re

from backend.app.schemas.domain import ParsedRequirement

SCENE_KEYWORDS = {
    "mining": ["矿山", "露天", "剥离", "开采", "矿石", "穿孔", "爆破", "排土", "矿卡", "选矿"],
    "earthwork": ["土方", "路基", "回填", "场地平整", "推土", "填方", "挖方", "土石方"],
    "agriculture": ["农业", "农田", "农机", "收割", "三夏", "秸秆", "播种", "旋耕"],
}
SCENE_CN = {"mining": "露天矿山开采", "earthwork": "土方工程", "agriculture": "农业作业"}
MISSING_CN = {
    "scene_type": "作业场景（矿山/土方/农业）",
    "annual": "年作业量或工程量（如：年产 200 万吨）",
    "budget": "预算范围（如：预算 1.2 亿元）",
    "duration": "工期（如：工期 3 年）",
}


def _num(unit: str | None) -> float:
    if not unit:
        return 1.0
    if "亿" in unit:
        return 100_000_000
    if "万" in unit:
        return 10_000
    return 1.0


def parse_requirement(text: str) -> ParsedRequirement:
    t = (text or "").strip()
    req = ParsedRequirement(raw=t)
    if not t:
        req.missing = ["scene_type", "annual", "budget", "duration"]
        req.followup_questions = [
            "请问作业场景是矿山开采、土方工程还是农业作业？",
            "请提供年作业量或总工程量（吨/方）？",
            "预算范围是多少？",
            "工期要求多长？",
        ]
        return req

    # 1) 场景
    for scene, kws in SCENE_KEYWORDS.items():
        if any(k in t for k in kws):
            req.scene_type = scene
            req.scene_cn = SCENE_CN[scene]
            break

    # 2) 年产量/工程量（吨/方/万/亿 单位折算）
    #    注意 1：`吨级/方级` 是设备规格（如"75 吨级矿卡"），不是工程量，必须排除——
    #            该缺陷由真实案例验证发现（平煤神马"20 台 75 吨级纯电矿卡"曾被误解析为年产 75 吨）。
    #    注意 2：`总工程量/工程量/剥离总量` 是**全周期总量**（真实招标公告常用口径），需结合工期折算为年产量，
    #            否则会把五年总剥离量当成一年产量（白音华招标原件"剥离总量 17239.2 万立方米"验证发现）。
    total_volume_t = 0.0
    m = re.search(
        r"(年产|年产量|产量|年作业量|总工程量|工程量|剥离总量|总剥离量|剥离工程总量|"
        r"年剥离量|剥离量|剥离任务量)?\s*([\d.]+)\s*(万亿|亿|万)?\s*"
        r"(吨|t|T|方|立方|m3|m³|立方米)(?!级)",
        t,
    )
    if m:
        keyword = m.group(1) or ""
        val = float(m.group(2)) * _num(m.group(3))
        unit_raw = m.group(4)
        is_m3 = unit_raw in ("方", "立方", "m3", "m³", "立方米")
        # 方量按矿岩密度折算吨（2.6 t/m3），后续选型统一按吨位计
        tons = val * 2.6 if is_m3 else val
        if any(k in keyword for k in ("总工程量", "工程量", "总剥离量", "剥离总量", "剥离工程总量")):
            total_volume_t = tons  # 全周期总量，待工期限定后折算
        req.annual_t = tons
    # 兼容 "x万吨/年"
    m2 = re.search(r"([\d.]+)\s*(万|亿)?\s*(吨|方|t|m3|m³)(?!级)\s*/\s*年", t)
    if m2 and req.annual_t <= 0:
        val = float(m2.group(1)) * _num(m2.group(2))
        req.annual_t = val * 2.6 if m2.group(3) in ("方", "m3", "m³") else val

    # 3) 工期
    m = re.search(r"工期\s*([\d.]+)\s*(年|月|天|日)", t)
    if m:
        num = float(m.group(1))
        unit = m.group(2)
        req.duration_years = num if unit == "年" else (num / 12 if unit == "月" else num / 330)
    elif req.annual_t > 0 and "年" in t:
        req.duration_years = 1.0

    # 4) 预算（兼容采购口径：最高限价/限价/计划投资/总投资/合同额/标的额/中标金额）
    m = re.search(
        r"(?:预算|最高限价|限价|计划投资|总投资|合同额|标的额|中标金额)\s*([\d.]+)\s*(亿|万)?\s*元", t
    )
    if m:
        req.budget_cny = float(m.group(1)) * _num(m.group(2))

    # 5) 约束条件
    constraints: list[str] = []
    if any(k in t for k in ("纯电", "电动化", "新能源", "电动")):
        constraints.append("电动化优先")
    if any(k in t for k in ("高原", "海拔", "高海拔")):
        constraints.append("高海拔工况")
    if "冬季" in t or "严寒" in t:
        constraints.append("严寒工况")
    req.constraints = constraints

    # 6) 折算（总量口径：按工期折算年产量；年产量口径：×工期得总量）
    if total_volume_t > 0 and req.duration_years > 0:
        req.total_t = total_volume_t
        req.annual_t = total_volume_t / req.duration_years
    if req.annual_t > 0:
        req.daily_t = req.annual_t / 330.0
    if req.annual_t > 0 and req.duration_years > 0 and req.total_t <= 0:
        req.total_t = req.annual_t * req.duration_years

    # 7) 缺失参数 -> 追问
    if not req.scene_type:
        req.missing.append("scene_type")
    if req.annual_t <= 0:
        req.missing.append("annual")
    if req.budget_cny <= 0:
        req.missing.append("budget")
    if req.duration_years <= 0:
        req.missing.append("duration")
    req.followup_questions = [f"请补充：{MISSING_CN[k]}" for k in req.missing]
    return req
