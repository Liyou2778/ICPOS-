"""知识库整体重建：清空旧条目与向量 → 种子五大库 + 全域语料（新）重新入库。

用法：python -m scripts.rebuild_kb
产物：KnowledgeEntry 条目与向量库全部为新语料口径；打印各类统计与检索抽检结果。
"""

from __future__ import annotations

import sys

from backend.app.core.db import SessionLocal, init_db
from backend.app.services import rag
from backend.app.services.kb import rebuild_all_knowledge


def main() -> int:
    init_db()
    db = SessionLocal()
    try:
        stats = rebuild_all_knowledge(db)
        print("[rebuild_kb] 知识库已整体重建：")
        print(f"  清空：知识条目 {stats['cleared_kb_entries']} 条 / 向量 {stats['cleared_vectors']} 条")
        print(f"  种子设备参数库：{stats['equipment']} 型号")
        print(f"  种子维保知识库：{stats['fault_codes']} 条故障码")
        print(f"  施工工艺库分块：{stats['process_chunks']}")
        print(f"  方案模板库分块：{stats['template_chunks']}")
        print(f"  项目运营库分块：{stats['project_chunks']}")
        uc = stats["unified_corpus"]
        print("  全域语料入库：")
        print(
            f"    设备型号档案 {uc['equipment_model']} / 价格TCO {uc['equipment_price_tco']} / "
            f"故障案例 {uc['fault_case']} / 方案模板 {uc['project_template']}"
            f"（去重模板类型 {uc['project_template_types']}）/ 客户档案 {uc['customer']}"
        )
        print(f"    evaluation_qa 未入库（评测集保留）：{uc['eval_qa_excluded']} 条")
        print(f"  向量库条目合计：{stats['vector_entries']}")

        # 检索抽检（确认新语料真的能被检索到）
        probes = [
            ("XE700EV 的铲斗容量和电池容量是多少", "equipment"),
            ("XE700EV 的三年 TCO 和购置价", "price"),
            ("挖掘机液压系统异常的维修步骤和备件", "maintenance"),
            ("矿山项目投标方案包含哪些章节", "template"),
            ("内蒙古蒙泰矿业的服务合同金额", "customer"),
            ("排土场安全车挡高度要求", None),
        ]
        print("\n  检索抽检：")
        for q, expect in probes:
            hits = (
                rag.hybrid_search(db, q, top_k=3, kb_type=expect)
                if expect
                else rag.hybrid_search(db, q, top_k=3)
            )
            top = hits[0] if hits else None
            mark = "✅" if top else "❌"
            print(
                f"    {mark} [{expect or '全库'}] {q} → "
                f"{top.title if top else '无命中'}（score {top.score:.3f}）"
                if top
                else f"    ❌ [{expect or '全库'}] {q} → 无命中"
            )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
