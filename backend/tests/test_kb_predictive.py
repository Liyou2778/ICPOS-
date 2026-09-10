"""指导书 5.2 DoD / 6.2 验收映射：知识检索与预测性维护模型质量。"""

from __future__ import annotations

import json

from backend.app.core.config import settings
from backend.app.core.db import SessionLocal
from backend.app.models import KnowledgeEntry
from backend.app.services import rag

from .conftest import requires_demo


@requires_demo
def test_hybrid_search_process():
    db = SessionLocal()
    try:
        hits = rag.hybrid_search(db, "露天矿台阶穿孔的孔网参数怎么确定？", top_k=5)
        assert hits
        assert any("穿孔" in h.title for h in hits) or any("穿孔" in h.excerpt for h in hits)
    finally:
        db.close()


@requires_demo
def test_hybrid_search_template():
    db = SessionLocal()
    try:
        hits = rag.hybrid_search(db, "矿山投标方案需要哪些章节？", top_k=5, kb_type="template")
        assert hits and hits[0].kb_type == "template"
        assert any("投标" in h.title for h in hits)
    finally:
        db.close()


@requires_demo
def test_kb_coverage_counts():
    db = SessionLocal()
    try:
        assert db.query(KnowledgeEntry).count() >= 6  # 六篇工艺文档
        assert rag.vector_store.get().count() >= 6
    finally:
        db.close()


@requires_demo
def test_predictive_model_report_meets_prd():
    """PRD 功能 3.1：预测准确率 ≥85%，非突发故障预警提前量 ≥24h（模拟数据评估）。"""
    report_path = settings.repo_root / "data" / "models" / "eval_report.json"
    assert report_path.exists(), "请先执行 uv run python -m scripts.train_models"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["accuracy"] >= 0.85
    assert (report["lead_hours"] or 0) >= 24
    assert "模拟数据" in report["note"]


@requires_demo
def test_predict_device_risky_on_faulted():
    """在线预测：故障设备（T04 HYD-01）应判为风险并给 RUL。"""
    from backend.app.models import Device
    from backend.app.services.predictive import predict_device

    db = SessionLocal()
    try:
        dev = db.query(Device).filter(Device.code == "T04").first()
        assert dev is not None
        r = predict_device(db, "T04")
        assert r.get("risky") is True
        assert r.get("top_code") == "HYD-01"
        assert (r.get("remaining_hours") or 0) >= 0
    finally:
        db.close()
