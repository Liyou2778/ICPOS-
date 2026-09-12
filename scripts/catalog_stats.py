"""设备目录语料统计（流式扫描，用于知识库/训练集设计）。"""

from __future__ import annotations

import json
import os
from collections import Counter

PATHS = {
    "train": r"C:\Users\Liyou\.dsh\attachments\v1\files\64\6467ba6f500c0312f3ec5b7e0cb7d609b6f47b33c670579ca97ecabfbef06e77\train(1).jsonl",
    "test": r"C:\Users\Liyou\.dsh\attachments\v1\files\78\78ca260f6b1cdfcf53fed1ad9632080f969d7c7081a4a2d168f31c4a160b6694\test(1).jsonl",
}


def scan(name: str, path: str) -> None:
    print("=" * 78)
    print(f"[{name}] {path}  ({os.path.getsize(path) / 1e6:.1f} MB)")
    types: Counter = Counter()
    cats: Counter = Counter()
    mining: Counter = Counter()
    models: set[str] = set()
    urls: set[str] = set()
    ids: set[str] = set()
    all_keys: set[str] = set()
    param_counts: list[int] = []
    n = 0
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            n += 1
            try:
                o = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            all_keys |= set(o.keys())
            types[o.get("_type")] += 1
            cats[o.get("category")] += 1
            mining[o.get("is_mining")] += 1
            if o.get("model"):
                models.add(str(o["model"]))
            if o.get("url"):
                urls.add(str(o["url"]))
            if o.get("_id"):
                ids.add(str(o["_id"]))
            pc = o.get("param_count")
            if isinstance(pc, int):
                param_counts.append(pc)
    param_counts.sort()
    med = param_counts[len(param_counts) // 2] if param_counts else 0
    print(f"  记录数={n}  _type={dict(types)}  is_mining={dict(mining)}")
    print(f"  唯一 _id={len(ids)}  唯一 model={len(models)}  唯一 url={len(urls)}")
    print(
        f"  param_count: min={param_counts[0] if param_counts else 0} 中位={med} max={param_counts[-1] if param_counts else 0}"
    )
    print(f"  字段全集({len(all_keys)}): {sorted(all_keys)}")
    print("  Top15 品类:")
    for c, k in cats.most_common(15):
        print(f"    {c}: {k}")


if __name__ == "__main__":
    for nm, p in PATHS.items():
        scan(nm, p)
    print("=" * 78)
