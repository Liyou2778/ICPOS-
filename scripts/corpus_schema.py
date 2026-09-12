"""采样语料各 _type 的字段结构（每种类型打印 1 条，便于设计 ETL）。"""

from __future__ import annotations

import json

PATHS = [
    r"C:\Users\Liyou\.dsh\attachments\v1\files\64\6467ba6f500c0312f3ec5b7e0cb7d609b6f47b33c670579ca97ecabfbef06e77\train(1).jsonl",
    r"C:\Users\Liyou\.dsh\attachments\v1\files\78\78ca260f6b1cdfcf53fed1ad9632080f969d7c7081a4a2d168f31c4a160b6694\test(1).jsonl",
]
SKIP_BULK = {"sim_telemetry", "sim_trajectory"}  # 各取头部 1 条即可


def main() -> None:
    seen: dict[str, dict] = {}
    for p in PATHS:
        with open(p, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                t = str(o.get("_type"))
                if t not in seen:
                    seen[t] = o
                if len(seen) >= 16 and all(k in seen for k in list(seen)):
                    pass
    for t, o in seen.items():
        print("=" * 78)
        print(f"_type = {t}   keys({len(o)}): {sorted(o.keys())}")
        print(json.dumps(o, ensure_ascii=False)[:600])


if __name__ == "__main__":
    main()
