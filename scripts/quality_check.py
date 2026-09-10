"""知识库质检（指导书 5.2 阶段二 DoD 质检）。

检查项：
  1) 设备参数直读正确率（抽样比对 CSV 与库内记录，要求 ≥95%）；
  2) RAG 混合检索命中率：20 条标准问句，期望条目进入 Top5（要求 ≥80%）；
  3) 知识库覆盖统计（四库 + 向量）。
用法：uv run python -m scripts.quality_check [--strict]
"""

from __future__ import annotations

import argparse

import pandas as pd

from backend.app.core.config import settings
from backend.app.core.db import SessionLocal, init_db
from backend.app.models import EquipmentModel, FaultCode, KnowledgeEntry
from backend.app.services import rag

ROOT = settings.repo_root
# 标准问句 -> 期望命中关键词（与工艺/模板文档真实内容对应，防止自说自话）
RETRIEVAL_QUERIES = [
    ("露天矿台阶穿孔的孔网参数怎么确定？", "孔网"),
    ("钻孔孔径怎么选，牙轮钻机适合什么硬度？", "牙轮钻机"),
    ("爆破单耗一般取多少，怎么控制大块率？", "单耗"),
    ("多排毫秒微差爆破延时怎么设置？", "毫秒微差"),
    ("挖掘机铲装效率怎么估算？", "铲装"),
    ("矿用自卸车斗容和挖掘机铲斗怎么匹配？", "斗容"),
    ("装车时间控制在多少分钟内可以降低车等铲？", "装车时间"),
    ("矿岩运输单车循环时间包括哪些环节？", "循环时间"),
    ("运输道路坡度限制是多少？", "坡度"),
    ("调度优化怎么降低设备空载率？", "空载率"),
    ("排土台阶高度一般多少？", "排土台阶"),
    ("排土场安全车挡高度有什么要求？", "安全车挡"),
    ("露天矿开采全流程包括哪些工序？", "穿孔"),
    ("多机协同调度里车铲比怎么配置？", "车铲比"),
    ("电子围栏越界告警和调度有什么关系？", "电子围栏"),
    ("投标方案包括哪些标准章节？", "投标方案"),
    ("矿山施工组织设计模板包括哪些章节？", "施工组织"),
    ("设备选型方案里 TCO 成本怎么测算？", "TCO"),
    ("售后响应时间对方案有什么要求？", "售后服务"),
    ("装车偏载对设备有什么损伤？", "偏载"),
]


def check_params() -> dict:
    csvf = ROOT / "data" / "knowledge" / "equipment_models.csv"
    db = SessionLocal()
    try:
        csv_df = pd.read_csv(csvf, encoding="utf-8-sig")
        samples = csv_df.sample(min(6, len(csv_df)), random_state=7)
        ok = 0
        checked = 0
        for r in samples.itertuples():
            m = db.query(EquipmentModel).filter(EquipmentModel.code == r.code).first()
            checked += 1
            if (
                m is not None
                and abs(m.price_cny - float(r.price_cny)) < 1e-6
                and abs(m.bucket_m3 - float(r.bucket_m3)) < 1e-6
                and m.model_name == r.model_name
            ):
                ok += 1
        return {"checked": checked, "ok": ok, "accuracy": round(ok / max(checked, 1), 4)}
    finally:
        db.close()


def check_retrieval() -> dict:
    db = SessionLocal()
    try:
        hits_ok = 0
        detail: list[dict] = []
        for q, token in RETRIEVAL_QUERIES:
            hits = rag.hybrid_search(db, q, top_k=5)
            hit = any(token in h.title or token in _content(db, h.entry_id) for h in hits)
            hits_ok += int(hit)
            detail.append(
                {"q": q[:20], "expected": token, "hit": hit, "top_titles": [h.title for h in hits][:3]}
            )
        return {
            "queries": len(RETRIEVAL_QUERIES),
            "hits": hits_ok,
            "hit_rate": round(hits_ok / len(RETRIEVAL_QUERIES), 4),
            "detail": detail,
        }
    finally:
        db.close()


def _content(db, entry_id: int) -> str:
    e = db.get(KnowledgeEntry, entry_id)
    return e.content if e else ""


def counts() -> dict:
    db = SessionLocal()
    try:
        return {
            "equipment": db.query(EquipmentModel).count(),
            "fault_codes": db.query(FaultCode).count(),
            "kb_entries": db.query(KnowledgeEntry).count(),
            "vector_chunks": rag.vector_store.get().count(),
        }
    finally:
        db.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true", help="未达标时以非零退出码结束")
    args = ap.parse_args()
    init_db()
    c = counts()
    print("[quality_check] 知识库覆盖：", c)
    p = check_params()
    print(
        f"[quality_check] 设备参数直读正确率：{p['accuracy'] * 100:.1f}% "
        f"（{p['ok']}/{p['checked']}，要求 ≥95%）"
    )
    r = check_retrieval()
    print(
        f"[quality_check] RAG 混合检索命中率：{r['hit_rate'] * 100:.1f}% "
        f"（{r['hits']}/{r['queries']}，要求 ≥80%）"
    )
    for d in r["detail"]:
        if not d["hit"]:
            print(f"  [miss] 期望命中「{d['expected']}」 问句：{d['q']}… 实际 Top：{d['top_titles']}")
    ok = p["accuracy"] >= 0.95 and r["hit_rate"] >= 0.80 and c["equipment"] > 0 and c["vector_chunks"] > 0
    print("[quality_check] 结论：", "通过 ✅" if ok else "未达标 ❌")
    return 0 if ok or not args.strict else 1


if __name__ == "__main__":
    raise SystemExit(main())
