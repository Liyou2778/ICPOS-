"""知识库入库：四大库（设备参数/施工工艺/维保知识/方案模板）+ 向量化。

用法（仓库根目录）：uv run python -m scripts.build_kb
"""

from __future__ import annotations

import sys

from backend.app.core.db import SessionLocal, init_db
from backend.app.services.kb import build_all_knowledge


def main() -> int:
    init_db()
    db = SessionLocal()
    try:
        stats = build_all_knowledge(db)
        print("[build_kb] 入库完成：")
        print(f"  设备参数库型号：{stats['equipment']}")
        print(f"  维保知识库故障码/保养定额：{stats['fault_codes']}")
        print(f"  施工工艺库分块：{stats['process_chunks']}")
        print(f"  方案模板库分块：{stats['template_chunks']}")
        print(f"  向量库条目总数：{stats['vector_entries']}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
