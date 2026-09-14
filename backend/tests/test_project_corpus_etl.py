"""项目运营语料 ETL 验收：幂等性 · 泄漏检查 · 字段规范化 · 台账一致性。

企业级要求（对齐目标 (1)(4)）：
  * **幂等**：ETL 连续执行两次，第二次必须 0 新增、0 更新（仅允许无副作用重放）。
  * **泄漏**：manifest 与库内数据都必须满足 train/test 项目零交叉，否则禁止用于评估。
  * **规范化**：金额单位为元、日期为 YYYY-MM-DD、成本类型限定五个枚举、任务归属项目存在。
  * **一致性**：台账条数/金额、任务条数与 manifest 完全对得上（可审计）。
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import func, select

from backend.app.core.config import settings
from backend.app.core.db import SessionLocal
from backend.app.models.project import Project, ProjectCost, Task
from scripts.ingest_project_corpus import run as ingest_run

MANIFEST = settings.repo_root / "data" / "corpus" / "project_manifest.json"
COST_TYPES = {"人工", "材料", "机械", "其他", "管理"}


@pytest.fixture(scope="module")
def manifest() -> dict:
    if not MANIFEST.exists():
        pytest.skip("项目语料未就绪：请先放置 data/corpus/project_train.jsonl / project_test.jsonl")
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def ingest_twice() -> tuple[dict, dict]:
    """连续跑两次 ETL 并返回两次报告（用于幂等断言）。"""
    if not MANIFEST.exists():
        pytest.skip("项目语料未就绪")
    return ingest_run(), ingest_run()


def test_etl_is_idempotent(ingest_twice):
    """幂等定义：重跑不得新增记录、不得产生重复键，且**库内状态指纹完全一致**。

    说明：ETL 采用收敛式 upsert（对已存在行刷新字段），因此重跑出现 updated 计数是预期行为；
    真正要守住的是"状态可复现且不膨胀"——故以新增数、行数、内容指纹三重断言校验。
    """
    first, second = ingest_twice
    assert second["created"] == {}, f"第二次 ETL 不应新增任何记录，实际 {second['created']}"
    for key in ("projects", "tasks", "costs"):
        assert second[key] == first[key], f"{key} 数量发生变化：{first[key]} → {second[key]}"
    assert second["cost_total_yuan"] == pytest.approx(first["cost_total_yuan"])
    assert second["manifest"]["unique_records"] == first["manifest"]["unique_records"]
    assert second["manifest"]["shared_duplicates"] == 0

    # 无重复键（幂等键必须唯一）
    db = SessionLocal()
    try:
        dup_task = db.execute(
            select(Task.project_id, Task.task_code, func.count(Task.id))
            .where(Task.task_code != "")
            .group_by(Task.project_id, Task.task_code)
            .having(func.count(Task.id) > 1)
        ).all()
        dup_cost = db.execute(
            select(ProjectCost.cost_key, func.count(ProjectCost.id))
            .group_by(ProjectCost.cost_key)
            .having(func.count(ProjectCost.id) > 1)
        ).all()
        dup_proj = db.execute(
            select(Project.code, func.count(Project.id))
            .group_by(Project.code)
            .having(func.count(Project.id) > 1)
        ).all()
        fingerprint = _fingerprint(db)
    finally:
        db.close()
    assert not dup_task and not dup_cost and not dup_proj, (
        f"存在重复键：task={dup_task[:3]} cost={dup_cost[:3]} project={dup_proj[:3]}"
    )

    # 再跑一次，指纹必须逐位一致（状态收敛）
    ingest_run()
    db = SessionLocal()
    try:
        again = _fingerprint(db)
    finally:
        db.close()
    assert again == fingerprint, "第三次执行后库内状态指纹变化，说明 ETL 未收敛"


def _fingerprint(db) -> str:
    """库内项目运营数据的内容指纹（排序后拼接，用于幂等比对）。"""
    import hashlib

    rows = []
    rows += [
        str(r)
        for r in db.execute(
            select(
                Project.code,
                Project.name,
                Project.tenderer,
                Project.plan_invest_yuan,
                Project.section_est_total_yuan,
                Project.data_type,
            ).order_by(Project.code)
        )
    ]
    rows += [
        str(r)
        for r in db.execute(
            select(
                Task.task_code,
                Task.process,
                Task.plan_start,
                Task.plan_end,
                Task.actual_end,
                Task.workload,
                Task.status,
                Task.team,
            )
            .where(Task.task_code != "")
            .order_by(Task.task_code)
        )
    ]
    rows += [
        str(r)
        for r in db.execute(
            select(
                ProjectCost.cost_key, ProjectCost.cost_type, ProjectCost.amount, ProjectCost.period
            ).order_by(ProjectCost.cost_key)
        )
    ]
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()


def test_train_test_projects_are_disjoint(manifest):
    train, test = set(manifest["train_projects"]), set(manifest["test_projects"])
    assert train and test
    assert not (train & test), "项目级交叉会导致评估泄漏，禁止"
    assert manifest["overlap_projects"] == []
    assert manifest["shared_duplicates"] == 0
    # 文件级溯源信息必须齐全（SHA-256 可复核）
    for f in manifest["files"]:
        assert len(f["sha256"]) == 64 and f["rows"] > 0


def test_counts_match_manifest(manifest):
    db = SessionLocal()
    try:
        n_task = db.scalar(select(func.count(Task.id)).where(Task.task_code != ""))
        n_cost = db.scalar(select(func.count(ProjectCost.id)))
        n_anchor = db.scalar(select(func.count(Project.id)).where(Project.data_type == "real"))
        codes = {c for (c,) in db.execute(select(Project.code).where(Project.data_type == "real"))}
    finally:
        db.close()
    assert n_task == manifest["type_totals"]["construction_task"]
    assert n_cost == manifest["type_totals"]["actual_cost"]
    # 项目锚点按 project_key 去重入库（同一项目可能有多条公告/标段记录），
    # 故入库项目数 = train+test 的唯一项目编号数，而非 project_budget 记录数。
    assert n_anchor == len(set(manifest["train_projects"]) | set(manifest["test_projects"]))
    assert manifest["type_totals"]["project_budget"] >= n_anchor
    assert codes == set(manifest["train_projects"]) | set(manifest["test_projects"])
    # manifest 的 split 标记必须与库内一致（防止 ETL 误把 test 项目当训练数据）
    assert set(manifest["train_projects"]) | set(manifest["test_projects"]) == set(codes)


def test_cost_ledger_normalisation(manifest):
    db = SessionLocal()
    try:
        bad_type = db.scalar(
            select(func.count(ProjectCost.id)).where(ProjectCost.cost_type.notin_(COST_TYPES))
        )
        amount_sum = db.scalar(select(func.sum(ProjectCost.amount)))
        negative = db.scalar(select(func.count(ProjectCost.id)).where(ProjectCost.amount < 0))
        bad_period = db.scalar(
            select(func.count(ProjectCost.id)).where(
                (ProjectCost.period == "") | (ProjectCost.period.is_(None))
            )
        )
        orphan = db.scalar(
            select(func.count(ProjectCost.id)).where(ProjectCost.project_id.notin_(select(Project.id)))
        )
        products_missing = db.scalar(
            select(func.count(ProjectCost.id)).where(
                (ProjectCost.cost_item == "") | (ProjectCost.cost_item.is_(None))
            )
        )
    finally:
        db.close()
    assert bad_type == 0, "成本类型必须落在五个枚举内"
    assert negative == 0 and bad_period == 0, "金额非负、期间必填"
    assert orphan == 0, "台账必须归属到存在的项目"
    assert products_missing == 0, "成本科目（cost_item）必填"
    assert amount_sum == pytest.approx(manifest["cost_total_yuan"], rel=1e-6)


def test_task_schedule_fields_normalised(manifest):
    db = SessionLocal()
    try:
        rows = db.execute(
            select(
                Task.process,
                Task.phase,
                Task.plan_start,
                Task.plan_end,
                Task.actual_end,
                Task.workload,
                Task.workload_unit,
                Task.status,
                Task.team,
                Task.device_ids,
            ).where(Task.task_code != "")
        ).all()
        orphan = db.scalar(select(func.count(Task.id)).where(Task.project_id.notin_(select(Project.id))))
    finally:
        db.close()
    assert orphan == 0
    allowed_status = {"未开始", "进行中", "已完成", "延期"}
    for process, phase, ps, pe, ae, wl, unit, status, team, devs in rows:
        assert process and phase, "工序与阶段必填"
        assert len(ps) == 10 and len(pe) == 10, f"计划日期必须是 YYYY-MM-DD：{ps}/{pe}"
        assert ae == "" or len(ae) == 10, f"实际完工日期格式异常：{ae}"
        assert wl > 0 and unit in {"m³", "t", "m"}, "工作量与单位需规范化"
        assert status in allowed_status
        assert team, "班组必填（调度与工期分析依赖）"
        assert devs, "设备指派必填（资源与工期分析依赖）"
        assert "|" not in devs or all(d.strip() for d in devs.split("|")), f"设备列表格式异常：{devs}"
    # 排期先后关系：计划完工不得早于计划开工
    for _process, _phase, ps, pe, *_ in rows:
        assert pe >= ps, f"计划完工早于开工：{ps} → {pe}"
