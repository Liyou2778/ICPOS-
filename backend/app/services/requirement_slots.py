"""需求槽位（slots）引擎：跨轮自动抽取、合并、缺失追问与补录续跑。

设计（企业级）：
  * 会话内维护结构化需求槽位，每轮由规则引擎（数字/单位/关键词）抽取并与历史合并，
    杜绝"用户说一半、Agent 重新问一遍"的体验问题；真实模式可叠加 LLM 抽取做补充。
  * 关键槽位缺失 → 阻塞方案生成，返回清单式追问 + 补录表单规格（前端直接渲染）。
  * 补录后自动续跑原任务（pending_intent 记忆），无需用户重复描述。
"""

from __future__ import annotations

import re

from backend.app.services.requirements import parse_requirement

# 关键槽位（缺失即阻塞生成）
REQUIRED_SLOTS = ["scene_type", "annual_t", "duration_years", "budget_cny"]

SLOT_META: dict[str, dict] = {
    "scene_type": {
        "label": "作业场景",
        "question": "作业场景是矿山开采、土方工程还是农业作业？",
        "kind": "select",
        "required": True,
        "options": [
            {"value": "mining", "label": "露天矿山开采"},
            {"value": "earthwork", "label": "土方工程"},
            {"value": "agriculture", "label": "农业作业"},
        ],
    },
    "annual_t": {
        "label": "年作业量",
        "question": "年作业量/工程量大约多少（如：年产 200 万吨，或 150 万方）？",
        "kind": "number",
        "required": True,
        "unit": "吨/年（方量按 2.6t/m³ 折算）",
    },
    "duration_years": {
        "label": "工期",
        "question": "工期要求多久（如：工期 3 年，或 18 个月）？",
        "kind": "number",
        "required": True,
        "unit": "年（月/天自动折算）",
    },
    "budget_cny": {
        "label": "预算",
        "question": "预算范围是多少（如：预算 1.5 亿元，或 6000 万元）？",
        "kind": "number",
        "required": True,
        "unit": "元",
    },
    "constraints": {
        "label": "特殊约束",
        "question": "是否有限制条件（如：电动化优先、高海拔、严寒工况）？",
        "kind": "text",
        "required": False,
    },
}


def slots_from_text(text: str) -> dict:
    """从文本抽取槽位（复用需求解析规则引擎，数字不经过大模型）。"""
    req = parse_requirement(text or "")
    out: dict = {}
    if req.scene_type:
        out["scene_type"] = req.scene_type
    if req.annual_t > 0:
        out["annual_t"] = req.annual_t
    if req.duration_years > 0:
        out["duration_years"] = round(req.duration_years, 3)
    if req.budget_cny > 0:
        out["budget_cny"] = req.budget_cny
    if req.constraints:
        out["constraints"] = req.constraints
    return out


_UNIT_HINT = {
    "budget_cny": ("亿", "万", "元"),
    "duration_years": ("年", "月", "天", "日"),
    "annual_t": ("吨", "t", "方", "m3", "m³"),
}


def interpret_bare_value(text: str, slot: str) -> object | None:
    """单槽位补充场景：用户只回一个数值（如"1.5亿"/"3年"/"200万吨"）时按单位语义解释。"""
    t = (text or "").strip()
    if not t or not re.search(r"\d", t):
        return None
    m = re.search(r"([\d.]+)\s*(万亿|亿|万|千)?\s*(吨|t|T|方|m3|m³|年|月|天|日|元)?", t)
    if not m:
        return None
    val = float(m.group(1))
    mag, unit = m.group(2) or "", m.group(3) or ""
    hints = _UNIT_HINT.get(slot, ())
    if unit and hints and unit not in hints and not (slot == "annual_t" and unit in ("吨", "t", "T", "方")):
        # 单位与该槽位语义不符时，仍允许无歧义的单槽位场景（例如预算回"1.5亿"无单位）
        if not mag:
            return None
    mult = 1.0
    if mag == "万":
        mult = 10_000
    elif mag == "亿":
        mult = 100_000_000
    elif mag == "千":
        mult = 1_000
    elif mag == "万亿":
        mult = 1_000_000_000_000
    raw = val * mult
    if slot == "budget_cny":
        if mag in ("亿", "万", "千", "万亿"):
            return raw
        return raw if unit == "元" else None
    if slot == "duration_years":
        if unit == "年" or not unit:
            return round(raw, 3)
        if unit == "月":
            return round(raw / 12, 3)
        if unit in ("天", "日"):
            return round(raw / 330, 3)
        return None
    if slot == "annual_t":
        if unit in ("方", "m3", "m³"):
            return raw * 2.6
        if unit in ("吨", "t", "T") or not unit:
            return raw
        return None
    return None


def merge_slots(base: dict | None, new: dict | None) -> dict:
    """合并槽位：新值覆盖旧值；约束条件做并集。"""
    out = dict(base or {})
    for k, v in (new or {}).items():
        if v in (None, "", 0):
            continue
        if k == "constraints":
            old = out.get("constraints") or []
            out["constraints"] = sorted(set(list(old) + list(v)))
        else:
            out[k] = v
    return out


def missing_slots(slots: dict | None) -> list[str]:
    s = slots or {}
    return [k for k in REQUIRED_SLOTS if not s.get(k)]


def form_spec(slots: dict | None, missing: list[str] | None = None) -> list[dict]:
    """补录表单规格（前端按 kind 渲染控件）。"""
    miss = missing if missing is not None else missing_slots(slots)
    return [{"key": k, **SLOT_META[k], "filled": bool((slots or {}).get(k))} for k in miss if k in SLOT_META]


def slot_summary(slots: dict | None) -> list[dict]:
    """已收集槽位摘要（前端展示状态条）。"""
    s = slots or {}
    out = []
    for k, meta in SLOT_META.items():
        if s.get(k):
            v = s[k]
            if k == "annual_t":
                v = f"{v:,.0f} 吨/年"
            elif k == "duration_years":
                v = f"{v:g} 年"
            elif k == "budget_cny":
                v = f"{v / 1e4:,.0f} 万元"
            elif k == "scene_type":
                v = {"mining": "露天矿山开采", "earthwork": "土方工程", "agriculture": "农业作业"}.get(v, v)
            out.append({"key": k, "label": meta["label"], "value": v})
    return out


def requirement_text(slots: dict | None) -> str:
    """把槽位还原为自然语言需求（用于方案生成智能体）。"""
    s = slots or {}
    parts = []
    if s.get("scene_type"):
        parts.append(
            {"mining": "露天矿山开采", "earthwork": "土方工程", "agriculture": "农业作业"}.get(
                s["scene_type"], s["scene_type"]
            )
        )
    if s.get("annual_t"):
        parts.append(f"年作业量 {s['annual_t'] / 1e4:,.0f} 万吨")
    if s.get("duration_years"):
        parts.append(f"工期 {s['duration_years']:g} 年")
    if s.get("budget_cny"):
        parts.append(f"预算 {s['budget_cny'] / 1e8:g} 亿元")
    if s.get("constraints"):
        parts.append("约束：" + "、".join(s["constraints"]))
    return "，".join(parts) + "，请生成方案并推荐设备。"


def merge_form_values(slots: dict | None, values: dict | None) -> dict:
    """把补录表单的原始输入转成槽位值（支持带单位文本与纯数字）。"""
    out = dict(slots or {})
    for k, raw in (values or {}).items():
        if k not in SLOT_META:
            continue
        if k == "constraints":
            if raw:
                items = (
                    raw if isinstance(raw, list) else [x.strip() for x in str(raw).split("、") if x.strip()]
                )
                out["constraints"] = sorted(set((out.get("constraints") or []) + items))
            continue
        if k == "scene_type":
            if raw:
                out["scene_type"] = str(raw)
            continue
        parsed = interpret_bare_value(str(raw), k)
        if parsed is None:
            # 允许表单直接传数值（无单位）
            try:
                num = float(str(raw).replace(",", "").strip())
            except ValueError:
                continue
            if k == "budget_cny":
                # 表单单位为万元时前端会带上"万"字；此处按元处理
                parsed = num
            elif k == "annual_t":
                parsed = num
            else:
                parsed = num
        out[k] = parsed
    return out
