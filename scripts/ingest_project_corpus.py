"""工程项目运营语料 ETL（企业级）：校验 → 去重 → 泄漏检查 → 血缘清单 → 幂等入库。

数据源：data/corpus/project_train.jsonl / project_test.jsonl
记录类型：project_budget（真实招标锚点）/ construction_task（仿真任务）/ actual_cost（仿真成本）

产出：
  * data/corpus/project_manifest.json   血缘清单（SHA-256 / 行数 / 类型分布 / 去重 / 划分）
  * data/corpus/project_ingest_report.json 入库报告（各表条数、成本结构、工序分布）
  * SQLite：proj_project（招标锚点字段）/ proj_task（排期·工作量·资源）/ proj_cost（成本台账）

用法：python -m scripts.ingest_project_corpus [--strict]
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

from backend.app.core.config import settings
from backend.app.core.db import SessionLocal, init_db
from backend.app.models import Project, ProjectCost, Task

CORPUS = settings.repo_root / "data" / "corpus"
FILES = {"train": CORPUS / "project_train.jsonl", "test": CORPUS / "project_test.jsonl"}
PROCESS_MAP = {
    "穿孔凿岩": ("drilling", "穿孔"),
    "爆破": ("blasting", "爆破"),
    "铲装": ("loading", "铲装"),
    "运输": ("hauling", "运输"),
    "排土": ("dumping", "排土"),
}
PROJECT_FIELDS = [
    "tenderer",
    "industry",
    "region",
    "platform",
    "publish_date",
    "funding_source",
    "approval_authority",
    "source_url",
    "source_site",
]
BUDGET_META_FIELDS = [
    "announce_type",
    "project_code",
    "budget_scope",
    "currency",
    "section_est_wan_list",
    "section_names",
    "matched_keywords",
    "win_amount_wan",
    "plan_invest_wan",
    "source_site",
    "note",
]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_split() -> tuple[dict[str, list[dict]], dict]:
    """读取两份文件 + 血缘清单（含跨文件去重）。"""
    manifest = {"created_at": datetime.now(UTC).isoformat(), "files": [], "source": str(CORPUS)}
    splits: dict[str, list[dict]] = {}
    seen_ids: dict[str, str] = {}
    duplicates: list[str] = []
    for split, path in FILES.items():
        if not path.exists():
            raise FileNotFoundError(f"缺少语料文件：{path}")
        rows: list[dict] = []
        types: Counter = Counter()
        invalid: list[str] = []
        with path.open(encoding="utf-8", errors="replace") as f:
            for lineno, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError as exc:
                    invalid.append(f"line {lineno}: {exc}")
                    continue
                rid = obj.get("_id")
                if not rid:
                    invalid.append(f"line {lineno}: 缺少 _id")
                    continue
                if rid in seen_ids:
                    duplicates.append(rid)
                    continue
                seen_ids[rid] = split
                types[obj.get("_type", "unknown")] += 1
                rows.append(obj)
        splits[split] = rows
        manifest["files"].append(
            {
                "name": path.name,
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
                "rows": len(rows),
                "types": dict(types),
                "invalid": invalid[:20],
            }
        )
    manifest["unique_records"] = len(seen_ids)
    manifest["shared_duplicates"] = len(duplicates)
    manifest["duplicate_ids"] = duplicates[:50]
    type_totals: Counter = Counter()
    for f in manifest["files"]:
        type_totals.update(f["types"])
    manifest["type_totals"] = dict(type_totals)
    train_projects = sorted({r["project_key"] for r in splits["train"] if r.get("project_key")})
    test_projects = sorted({r["project_key"] for r in splits["test"] if r.get("project_key")})
    overlap = sorted(set(train_projects) & set(test_projects))
    manifest.update(
        {"train_projects": train_projects, "test_projects": test_projects, "overlap_projects": overlap}
    )
    return splits, manifest


def run(strict: bool = False) -> dict:
    init_db()
    splits, manifest = load_split()
    if manifest["overlap_projects"] and strict:
        raise ValueError(f"训练/测试项目存在交叉，禁止用于评估：{manifest['overlap_projects'][:5]}")

    db = SessionLocal()
    created = defaultdict(int)
    updated = defaultdict(int)
    cost_by_type: Counter = Counter()
    cost_total = 0.0
    tasks_by_process: Counter = Counter()
    try:
        # ---------- 1) 项目（真实招标锚点） ----------
        budgets: dict[str, dict] = {}
        for split in ("train", "test"):
            for r in splits[split]:
                if r.get("_type") == "project_budget":
                    budgets[r["project_key"]] = {**r, "_split": split}
        for key, b in budgets.items():
            proj = db.query(Project).filter(Project.code == key).first()
            section_total = float(b.get("section_est_total_yuan") or 0)
            plan_invest = float(b.get("plan_invest_yuan") or 0)
            data = {
                "name": b.get("project_name") or key,
                "tenderer": b.get("tenderer") or "",
                "industry": b.get("industry") or "",
                "region": b.get("region") or "",
                "platform": b.get("platform") or "",
                "publish_date": str(b.get("publish_date") or ""),
                "plan_invest_yuan": plan_invest,
                "section_est_total_yuan": section_total,
                "win_amount_yuan": float(b.get("win_amount_yuan") or 0),
                "funding_source": b.get("funding_source") or "",
                "approval_authority": b.get("approval_authority") or "",
                "source_url": b.get("source_url") or "",
                "source_site": b.get("source_site") or b.get("platform") or "",
                "data_type": b.get("data_type") or "real",
                "duration_days": int(b.get("duration_days") or 0),
                "budget_cny": section_total or plan_invest,
                "tender_meta": {k: b.get(k) for k in BUDGET_META_FIELDS if b.get(k) is not None},
            }
            if proj is None:
                db.add(Project(code=key, scene_type="mining", status="active", **data))
                created["project"] += 1
            else:
                for k, v in data.items():
                    setattr(proj, k, v)
                updated["project"] += 1
        db.commit()

        # ---------- 2) 施工任务（仿真） ----------
        for split in ("train", "test"):
            for r in splits[split]:
                if r.get("_type") != "construction_task":
                    continue
                proj = db.query(Project).filter(Project.code == r["project_key"]).first()
                if proj is None:
                    continue
                task_code = str(r.get("task_id") or r["_id"])
                process_cn = r.get("process") or ""
                code, cn = PROCESS_MAP.get(process_cn, ("other", process_cn))
                data = {
                    "process": code,
                    "process_cn": cn,
                    "phase": r.get("phase") or "",
                    "cycle": int(r.get("cycle") or 0),
                    "plan_start": str(r.get("plan_start") or ""),
                    "plan_end": str(r.get("plan_end") or ""),
                    "actual_start": str(r.get("actual_start") or ""),
                    "actual_end": str(r.get("actual_end") or ""),
                    "workload": float(r.get("workload") or 0),
                    "workload_unit": r.get("workload_unit") or "",
                    "status": r.get("status") or "",
                    "progress_pct": float(r.get("progress") or 0) / 100.0,
                    "team": r.get("team") or "",
                    "device_ids": r.get("device_ids") or "",
                    "device_models": r.get("device_models") or "",
                    "data_type": r.get("data_type") or "simulated",
                    "sched_meta": {
                        k: r.get(k)
                        for k in ("note", "sim_kickoff", "sim_now", "anchor_source_url", "announce_date")
                        if r.get(k)
                    },
                }
                task = db.query(Task).filter(Task.project_id == proj.id, Task.task_code == task_code).first()
                if task is None:
                    db.add(
                        Task(
                            project_id=proj.id,
                            code=task_code,
                            task_code=task_code,
                            quantity=float(r.get("workload") or 0),
                            unit=r.get("workload_unit") or "",
                            **data,
                        )
                    )
                    created["task"] += 1
                else:
                    for k, v in data.items():
                        setattr(task, k, v)
                    updated["task"] += 1
                tasks_by_process[process_cn] += 1
        db.commit()

        # ---------- 3) 成本台账（仿真） ----------
        for split in ("train", "test"):
            for r in splits[split]:
                if r.get("_type") != "actual_cost":
                    continue
                proj = db.query(Project).filter(Project.code == r["project_key"]).first()
                if proj is None:
                    continue
                key = r["_id"]
                amount = float(r.get("amount") or 0)
                data = {
                    "project_id": proj.id,
                    "cost_date": str(r.get("cost_date") or ""),
                    "period": r.get("period") or "",
                    "cost_item": r.get("cost_item") or "",
                    "cost_type": r.get("cost_type") or "",
                    "amount": amount,
                    "unit": r.get("unit") or "元",
                    "payee": r.get("payee") or "",
                    "invoice_no": r.get("invoice_no") or "",
                    "badge_no": r.get("badge_no") or "",
                    "data_type": r.get("data_type") or "simulated",
                    "note": r.get("note") or "",
                }
                row = db.query(ProjectCost).filter(ProjectCost.cost_key == key).first()
                if row is None:
                    db.add(ProjectCost(cost_key=key, **data))
                    created["cost"] += 1
                else:
                    for k, v in data.items():
                        setattr(row, k, v)
                    updated["cost"] += 1
                cost_by_type[data["cost_type"]] += 1
                cost_total += amount
        db.commit()

        report = {
            "manifest": manifest,
            "created": dict(created),
            "updated": dict(updated),
            "projects": db.query(Project).count(),
            "tasks": db.query(Task).count(),
            "costs": db.query(ProjectCost).count(),
            "cost_records_by_type": dict(cost_by_type),
            "cost_total_yuan": round(cost_total, 2),
            "tasks_by_process": dict(tasks_by_process),
            "note": "project_budget 为真实招标锚点；construction_task / actual_cost 为按锚点仿真生成。",
            "generated_at": datetime.now(UTC).isoformat(),
        }
        manifest["cost_total_yuan"] = round(cost_total, 2)
        (CORPUS / "project_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (CORPUS / "project_ingest_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return report
    finally:
        db.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="ICOPS 工程项目运营语料 ETL")
    ap.add_argument("--strict", action="store_true", help="项目级泄漏检查失败即报错退出")
    args = ap.parse_args()
    r = run(strict=args.strict)
    m = r["manifest"]
    print("[ingest_project_corpus] 完成：")
    print(f"  记录：唯一 {m['unique_records']} 条（跨文件重复 {m['shared_duplicates']} 条）")
    print(
        f"  项目：{r['projects']} 个（train {len(m['train_projects'])} / test {len(m['test_projects'])}，"
        f"交叉 {len(m['overlap_projects'])}）"
    )
    print(f"  任务：{r['tasks']} 条，工序分布 {r['tasks_by_process']}")
    print(
        f"  成本：{r['costs']} 条，合计 {r['cost_total_yuan'] / 1e4:,.1f} 万元，类型 {r['cost_records_by_type']}"
    )
    print(f"  血缘清单：{CORPUS / 'project_manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
