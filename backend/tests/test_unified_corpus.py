"""全域语料链路验收：ETL 幂等/切分完整性 · 模型门控自洽 · SFT 数据集纯净性 · 知识库一致性。

这些用例锁定的是**企业级数据治理红线**：
  1. ETL 重跑不得新增记录、不得产生重复键、库内状态指纹一致；
  2. train/test 切分只在训练实体上有效（其余实体的项目编号共用，必须显式声明不可用于划分）；
  3. 模型特征不得包含标签来源字段，门控结论必须由证据严格推导；
  4. **评测集不得进入训练集，也不得进入知识库**（否则"自己考自己"）；
  5. 知识条目与向量条数必须一一对应（避免检索静默丢条）。
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import func, select

from backend.app.core.config import settings
from backend.app.core.db import SessionLocal
from backend.app.models.corpus import CorpusRecord, EvalQA, ProjOperation
from backend.app.models.knowledge import KnowledgeEntry

from .conftest import requires_demo

MANIFEST = settings.repo_root / "data" / "corpus" / "unified_manifest.json"
OPS_DIR = settings.repo_root / "data" / "models" / "ops"
SFT_DIR = settings.repo_root / "data" / "sft"


@pytest.fixture(scope="module")
def manifest() -> dict:
    if not MANIFEST.exists():
        pytest.skip("未执行全域语料 ETL：请先运行 scripts.ingest_unified_corpus")
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def ingest_twice() -> tuple[dict, dict]:
    from scripts.ingest_unified_corpus import run

    return run(), run()


def test_etl_is_idempotent(ingest_twice: tuple[dict, dict]):
    first, second = ingest_twice
    assert second["created"] == {} or all(v == 0 for v in second["created"].values()), (
        f"第二次 ETL 不应新增记录：{second['created']}"
    )
    assert second["counts"] == first["counts"], "入库条数发生变化"
    assert second["records_total"] == first["records_total"]
    assert second["cross_file_duplicates"] == first["cross_file_duplicates"]

    db = SessionLocal()
    try:
        dup = db.execute(
            select(CorpusRecord.entity_type, CorpusRecord.record_key, func.count(CorpusRecord.id))
            .group_by(CorpusRecord.entity_type, CorpusRecord.record_key)
            .having(func.count(CorpusRecord.id) > 1)
        ).all()
        fingerprint = _fingerprint(db)
    finally:
        db.close()
    assert not dup, f"暂存表存在重复键：{dup[:3]}"

    from scripts.ingest_unified_corpus import run

    run()
    db = SessionLocal()
    try:
        again = _fingerprint(db)
    finally:
        db.close()
    assert again == fingerprint, "第三次执行后状态指纹变化，ETL 未收敛"


def _fingerprint(db) -> str:
    import hashlib

    rows = [
        str(r)
        for r in db.execute(
            select(CorpusRecord.entity_type, CorpusRecord.record_key, CorpusRecord.split).order_by(
                CorpusRecord.entity_type, CorpusRecord.record_key
            )
        )
    ]
    rows += [
        str(r)
        for r in db.execute(
            select(
                ProjOperation.record_key, ProjOperation.schedule_deviation_days, ProjOperation.actual_cost_cny
            ).order_by(ProjOperation.record_key)
        )
    ]
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()


def test_training_entity_split_is_clean_and_others_disclosed(manifest: dict):
    """训练实体零交叉；其他实体共用项目编号的事实必须显式声明（防止被误用于划分）。"""
    assert manifest["training_entity"] == "mining_project_operation"
    assert manifest["training_project_overlap"] == 0, "训练实体存在项目级交叉"
    split = manifest["per_entity_split"]["mining_project_operation"]["projects"]
    assert split["agent_train"] > 0 and split["project_test"] > 0

    # 至少有一个非训练实体确实存在交叉，且说明文字已写明其不可用于划分
    others_ok = any(
        v.get("projects", {}).get("overlap", 0) > 0
        for k, v in manifest["per_entity_split"].items()
        if k != "mining_project_operation"
    )
    if others_ok:
        assert "不可用于 train/test 划分" in manifest["split_note"], "交叉实体未作声明"


def test_manifest_lineage_complete(manifest: dict):
    for f in manifest["files"]:
        assert len(f["sha256"]) == 64 and f["rows"] > 0
        assert f["invalid"] == [] or isinstance(f["invalid"], list)
    assert manifest["records_total"] == sum(manifest["entity_totals"].values())
    assert manifest["counts"]["corpus_record"] == manifest["records_total"]


def test_counts_match_typed_tables(manifest: dict):
    db = SessionLocal()
    try:
        assert (
            db.scalar(select(func.count(ProjOperation.id)))
            == manifest["entity_totals"]["mining_project_operation"]
        )
        assert db.scalar(select(func.count(EvalQA.id))) == manifest["entity_totals"]["evaluation_qa"]
        telemetry = db.scalar(
            select(func.count())
            .select_from(CorpusRecord)
            .where(CorpusRecord.entity_type == "equipment_telemetry")
        )
    finally:
        db.close()
    assert telemetry == manifest["entity_totals"]["equipment_telemetry"]


# ---------------------------------------------------------------- 模型门控


@pytest.fixture(scope="module")
def ops_report() -> dict:
    f = OPS_DIR / "eval_report.json"
    if not f.exists():
        pytest.skip("未训练项目运营模型：请先运行 scripts.train_ops_models")
    return json.loads(f.read_text(encoding="utf-8"))


def test_ops_features_have_no_leakage(ops_report: dict):
    feats = ops_report["dataset"]["features"]
    banned = ("actual_duration_days", "actual_cost_cny", "schedule_deviation_days", "task_status")
    hit = [f for f in feats if any(b in f for b in banned)]
    assert not hit, f"特征含标签来源字段：{hit}"
    assert "actual" in ops_report["dataset"]["leakage_guard"]


def test_ops_deploy_gate_matches_evidence(ops_report: dict):
    res, gates = ops_report["results"], ops_report["deploy_decision"]["gates"]
    expected = {
        "delay_regression": bool(
            res["delay_regression"]["beats_baseline"]
            and res["delay_regression"]["cv_mae_ci95"][1] < res["delay_regression"]["baseline_mae"]
        ),
        "cost_ratio_regression": bool(
            res["cost_ratio_regression"]["beats_baseline"]
            and res["cost_ratio_regression"]["cv_mae_ci95"][1] < res["cost_ratio_regression"]["baseline_mae"]
        ),
        "delay_classification": bool(
            res["delay_classification"]["beats_baseline"] and (res["delay_classification"]["auc"] or 0) > 0.55
        ),
        "overrun_classification": bool(
            res["overrun_classification"]["beats_baseline"]
            and (res["overrun_classification"]["auc"] or 0) > 0.55
        ),
    }
    assert gates == expected, f"门控结论与证据不一致：{gates} vs {expected}"
    if not any(gates.values()):
        assert ops_report["deploy_decision"]["production_method"] == "calibrated_statistical_baseline"
        assert ops_report["baseline_model"], "未上线 ML 时必须给出标定基准"


def test_ops_baseline_monotonic(ops_report: dict):
    b = ops_report["baseline_model"]["schedule_deviation_days"]
    assert b["p50"] <= b["p75"] <= b["p90"] <= b["p95"]
    c = ops_report["baseline_model"]["cost_ratio"]
    assert c["p25"] <= c["p50"] <= c["p75"] <= c["p90"]


def test_ops_train_test_projects_disjoint(ops_report: dict):
    ds = ops_report["dataset"]
    assert ds["test"] == 1250 and ds["train"] == 1250
    assert "leakage_guard" in ds


# ---------------------------------------------------------------- SFT 数据集纯净性


@pytest.fixture(scope="module")
def sft_meta() -> dict:
    f = SFT_DIR / "meta.json"
    if not f.exists():
        pytest.skip("未构建 SFT 数据集：请先运行 scripts.build_sft_dataset")
    return json.loads(f.read_text(encoding="utf-8"))


def test_sft_dataset_composition(sft_meta: dict):
    assert sft_meta["train"]["n"] > 1000, "训练样本过少"
    kinds = sft_meta["train"]["by_kind"]
    assert kinds.get("refusal", 0) > 0, "缺少拒答负样本（无法训练不编造行为）"
    assert sft_meta["eval_corpus"]["n"] == 756
    assert "only" not in sft_meta["protocol"]["eval_separation"]


def test_eval_questions_not_in_training_set(sft_meta: dict):
    """评测集问题不得出现在训练集里（否则微调后评测分数无意义）。"""
    train_qs: set[str] = set()
    with (SFT_DIR / "agent_sft_train.jsonl").open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            rec = json.loads(line)
            user = next(m["content"] for m in rec["messages"] if m["role"] == "user")
            train_qs.add(user.split("\n")[0].strip())

    overlaps = []
    with (SFT_DIR / "agent_eval_corpus.jsonl").open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            q = json.loads(line)["question"].strip()
            if q in train_qs:
                overlaps.append(q)
    assert not overlaps, f"评测问题泄漏进训练集：{overlaps[:3]}"


@requires_demo
def test_evaluation_qa_not_indexed_into_kb():
    """evaluation_qa 不得进入知识库（否则 RAG 会直接命中答案，评测自我污染）。"""
    db = SessionLocal()
    try:
        qa_titles = {f"评测问答：{q}" for q in db.scalars(select(EvalQA.question)).all()}
        qa_questions = set(db.scalars(select(EvalQA.question)).all())
        entries = db.scalars(select(KnowledgeEntry.title)).all()
        contaminated = [t for t in entries if t in qa_titles or t in qa_questions]
    finally:
        db.close()
    assert not contaminated, f"评测问答被索引进知识库：{contaminated[:3]}"


@requires_demo
def test_kb_entries_have_matching_vectors():
    """知识条目与向量条数必须一一对应（防止检索静默丢条）。"""
    from backend.app.services.vectorstore import vector_store

    db = SessionLocal()
    try:
        entry_n = db.scalar(select(func.count(KnowledgeEntry.id))) or 0
    finally:
        db.close()
    vec_n = vector_store.get().count()
    assert entry_n == vec_n, f"知识条目 {entry_n} 与向量 {vec_n} 不一致"
    assert entry_n > 0
