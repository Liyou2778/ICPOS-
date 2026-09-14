"""工程项目运营语料体检：字段结构 / 项目分布 / 训练测试泄漏检查 / 数值覆盖。

用法：python -m scripts.diagnose_project_corpus
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from backend.app.core.config import settings

CORPUS = settings.repo_root / "data" / "corpus"
FILES = {"train": CORPUS / "project_train.jsonl", "test": CORPUS / "project_test.jsonl"}


def load(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def main() -> None:
    all_rows: dict[str, list[dict]] = {}
    for split, path in FILES.items():
        rows = load(path)
        all_rows[split] = rows
        print("=" * 78)
        print(f"[{split}] {path.name}  记录 {len(rows)}")
        types = Counter(r.get("_type") for r in rows)
        print("  _type:", dict(types))
        print("  data_type:", dict(Counter(r.get("data_type") for r in rows)))
        projects = {r.get("project_key") for r in rows}
        print(
            f"  唯一 project_key: {len(projects)}   唯一 project_id: {len({r.get('project_id') for r in rows})}"
        )
        for t in types:
            keys: Counter = Counter()
            for r in rows:
                if r.get("_type") == t:
                    keys.update(r.keys())
            print(f"  [{t}] 字段({len(keys)}): {sorted(keys)}")

    tr = {r.get("project_key") for r in all_rows["train"]}
    te = {r.get("project_key") for r in all_rows["test"]}
    print("=" * 78)
    print(f"泄漏检查：train 项目 {len(tr)} / test 项目 {len(te)} / 交集 {len(tr & te)}")
    if tr & te:
        print("  ⚠ 存在重叠项目：", sorted(tr & te)[:5])
    else:
        print("  ✅ 训练/测试项目完全不交叉（可用于无泄漏评估）")

    # 成本台账分布
    costs = [r for s in all_rows.values() for r in s if r.get("_type") == "actual_cost"]
    print("=" * 78)
    print(f"actual_cost 记录 {len(costs)}；成本类型分布：{dict(Counter(c.get('cost_type') for c in costs))}")
    print(f"  成本项 Top：{Counter(c.get('cost_item') for c in costs).most_common(8)}")
    amt = [c.get("amount") for c in costs if isinstance(c.get("amount"), (int, float))]
    if amt:
        print(f"  金额：合计 {sum(amt) / 1e4:,.1f} 万元，单笔 min {min(amt):,.0f} / max {max(amt):,.0f} 元")
    dates = sorted(str(c.get("cost_date")) for c in costs if c.get("cost_date"))
    if dates:
        print(f"  cost_date 范围：{dates[0]} ~ {dates[-1]}")

    tasks = [r for s in all_rows.values() for r in s if r.get("_type") == "construction_task"]
    print("=" * 78)
    print(f"construction_task 记录 {len(tasks)}；工序分布：{dict(Counter(t.get('process') for t in tasks))}")
    print(f"  阶段分布：{dict(Counter(t.get('phase') for t in tasks))}")
    tk: Counter = Counter()
    for t in tasks:
        tk.update(t.keys())
    print(f"  字段({len(tk)}): {sorted(tk)}")
    if tasks:
        print("  样例：", json.dumps(tasks[0], ensure_ascii=False)[:700])

    budgets = [r for s in all_rows.values() for r in s if r.get("_type") == "project_budget"]
    print("=" * 78)
    print(f"project_budget 记录 {len(budgets)}；字段：{sorted({k for b in budgets for k in b})}")
    if budgets:
        print("  样例：", json.dumps(budgets[0], ensure_ascii=False)[:700])


if __name__ == "__main__":
    main()
