"""检索层回归用例：故障码/型号精确召回 · IDF 加权 · 覆盖率门槛。

背景（由真实案例评测发现）：原关键词召回只用中文 2-gram，
"E110故障代码对应什么故障" 中的 **E110 被切成 E1/11/10**，于是"故障"这类高频词主导打分，
检索返回错误故障码条目（E108/E111）。修复后（代码 token 提取 + IDF 加权 + 精确代码强加权）
同一批 60 条评测样本的数字覆盖率由 **0.632 → 0.895**。
本文件锁定该能力，防止后续改动把检索精度again退回去。
"""

from __future__ import annotations

import json

import pytest

from backend.app.core.config import settings
from backend.app.core.db import SessionLocal
from backend.app.services import rag

from .conftest import requires_demo

pytestmark = requires_demo

SFT_DIR = settings.repo_root / "data" / "sft"


def test_query_codes_extraction():
    """故障码/型号 token 必须能被单独抽出（大写归一），这是精确召回的前提。"""
    assert "E110" in rag.query_codes("E110 故障代码对应什么故障？")
    cases = rag.query_codes("XE700EV 与 XDY1000、AKLE864 的差别，工单 WO20260914")
    for token in ("XE700EV", "XDY1000", "AKLE864", "WO20260914"):
        assert token in cases, f"未抽出 {token}：{cases}"
    assert rag.query_codes("排土场安全车挡高度要求") == [], "纯中文问题不应抽出代码 token"


@pytest.mark.parametrize("code", ["E100", "E106", "E110", "E114"])
def test_fault_code_query_hits_exact_entry(code: str):
    """问某个故障码，Top1 必须是该故障码条目（而不是相邻码）。"""
    db = SessionLocal()
    try:
        hits = rag.hybrid_search(db, f"{code}故障代码对应什么故障？", top_k=3, kb_type="maintenance")
    finally:
        db.close()
    assert hits, f"{code} 检索无结果"
    top = hits[0]
    assert code in f"{top.title} {top.excerpt}".upper(), (
        f"{code} Top1 未命中该故障码：{top.title} / {top.excerpt[:80]}")


def test_model_query_hits_exact_model():
    """问某个型号，Top1 必须是该型号档案。"""
    db = SessionLocal()
    try:
        hits = rag.hybrid_search(db, "XE700EV 的铲斗容量和电池容量是多少", top_k=3)
    finally:
        db.close()
    assert hits
    assert "XE700EV" in f"{hits[0].title} {hits[0].excerpt}".upper()


def test_retrieval_coverage_floor():
    """检索覆盖率门槛：修复后应显著高于修复前的 0.63，锁定在 ≥0.85。"""
    candidates = sorted(
        settings.repo_root.glob("data/models/agent_sft/eval_retrieval_only*.json"),
        key=lambda p: p.stat().st_mtime,
    )
    if not candidates:
        pytest.skip("未跑过检索上界评测：python -m scripts.eval_agent_sft --dry-run")
    rep = json.loads(candidates[-1].read_text(encoding="utf-8"))
    hit = rep["summary"]["num_hit_mean"]
    assert hit is not None
    assert hit >= 0.85, (
        f"检索覆盖率退化到 {hit}（修复后基线 0.895，修复前 0.632）；"
        f"报告：{candidates[-1].name}")
