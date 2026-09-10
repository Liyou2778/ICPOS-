"""RAG 检索增强生成管线（指导书 6.2）：解析 -> 分块 -> 向量化 -> 混合检索 -> 引用组装。

规范要点：
  * 文本分块：500~800 词元（CJK 近似按字符数），重叠 10%~15%（rag_chunk_overlap）；
  * 表格类设备参数不参与文本分块，一律走结构化查询（见 services/selection、kb 直读）；
  * 检索 = 向量检索 ∥ SQLite 关键词检索，加权融合后取 top RAG_TOP_K；
  * 答案强制携带引用来源（知识条目标识与版本），实现“陈述必有出处”。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.models import KnowledgeEntry
from backend.app.services.vectorstore import vector_store

VECTOR_PREFIX = "kb:"

STOPWORDS = {
    "的",
    "了",
    "是",
    "在",
    "和",
    "与",
    "或",
    "吗",
    "呢",
    "我",
    "你",
    "他",
    "它",
    "请",
    "帮",
    "一下",
    "一个",
    "什么",
    "怎么",
    "如何",
    "有没有",
    "能",
    "可以",
    "要",
    "多少",
    "这台",
    "那个",
    "这套",
    "怎么配",
}


def chunk_text(text: str, size: int | None = None, overlap: float | None = None) -> list[str]:
    """按字符分块（词元近似），size 默认 RAG_CHUNK_SIZE，重叠默认 RAG_CHUNK_OVERLAP。"""
    size = size or settings.rag_chunk_size
    overlap = settings.rag_chunk_overlap if overlap is None else overlap
    text = re.sub(r"\s+", "", text)
    if len(text) <= size:
        return [text]
    step = max(1, int(size * (1 - overlap)))
    return [text[i : i + size] for i in range(0, len(text), step)]


@dataclass
class RAGHit:
    entry_id: int
    kb_type: str
    title: str
    excerpt: str
    source: str
    version: str
    score: float = 0.0
    citations: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "entry_id": self.entry_id,
            "kb_type": self.kb_type,
            "title": self.title,
            "excerpt": self.excerpt,
            "source": self.source,
            "version": self.version,
            "score": round(self.score, 4),
        }


def index_entry(
    db: Session,
    kb_type: str,
    title: str,
    content: str,
    *,
    tags: str = "",
    source: str = "",
    version: str = "V1.0",
) -> int:
    """入库：登记 KnowledgeEntry + 分块向量化。返回分块数。"""
    entry = (
        db.query(KnowledgeEntry)
        .filter(KnowledgeEntry.kb_type == kb_type, KnowledgeEntry.title == title)
        .first()
    )
    if entry is None:
        entry = KnowledgeEntry(
            kb_type=kb_type, title=title, content=content, tags=tags, source=source, version=version
        )
        db.add(entry)
        db.flush()
    else:
        entry.content = content
        entry.tags = tags
        entry.source = source
        entry.version = version
        db.flush()
    chunks = chunk_text(content)
    store = vector_store.get()
    ids: list[str] = []
    texts: list[str] = []
    metas: list[dict] = []
    for ci, c in enumerate(chunks):
        ids.append(f"{VECTOR_PREFIX}{entry.id}:{ci}")
        texts.append(c)
        metas.append(
            {
                "entry_id": entry.id,
                "kb_type": kb_type,
                "title": title,
                "source": source,
                "version": version,
                "tags": tags,
                "ci": ci,
            }
        )
    store.add(ids, texts, metas)
    db.commit()
    return len(chunks)


def _keyword_candidates(
    db: Session, query: str, kb_type: str | None = None, limit: int = 60
) -> dict[int, int]:
    """关键词（LIKE 中文 2-gram 词）召回：entry_id -> 命中次数。"""
    q = db.query(KnowledgeEntry)
    if kb_type:
        q = q.filter(KnowledgeEntry.kb_type == kb_type)
    q = q.limit(500)
    rows = q.all()
    terms = [t for t in _terms(query) if len(t) >= 2]
    scores: dict[int, int] = {}
    for row in rows:
        n = 0
        for t in terms:
            if t in row.title or t in row.content or t in row.tags:
                n += 1
        if n:
            scores[row.id] = n
    return scores


def _terms(query: str) -> list[str]:
    parts = re.split(r"[\s，。？！、；：,.!?;:()（）【】\[\]\-\u201c\u201d]+", query)
    out: list[str] = []
    for p in parts:
        if not p:
            continue
        if len(p) >= 4 and p not in STOPWORDS:
            out.append(p)  # 整词优先
        for j in range(max(0, len(p) - 1)):
            g = p[j : j + 2]
            if g not in STOPWORDS and g not in out:
                out.append(g)
    return out


def hybrid_search(
    db: Session, query: str, top_k: int | None = None, kb_type: str | None = None
) -> list[RAGHit]:
    """混合检索：向量 ∥ 关键词，加权融合取 top_k（指导书 6.2）。"""
    top_k = top_k or settings.rag_top_k
    store = vector_store.get()
    vec_hits = store.query(query, top_k=top_k * 3)
    vec_ids: set[int] = set()
    vec_scores: dict[int, float] = {}
    for h in vec_hits:
        meta = h["metadata"]
        eid = int(meta.get("entry_id", 0))
        if not eid:
            continue
        vec_ids.add(eid)
        vec_scores[eid] = max(vec_scores.get(eid, 0.0), float(h["score"]))

    kw_scores = _keyword_candidates(db, query, kb_type=kb_type)
    cand_ids = set(vec_ids) | set(kw_scores.keys())
    if not cand_ids:
        return []

    rows = db.query(KnowledgeEntry).filter(KnowledgeEntry.id.in_(cand_ids)).all()
    by_id = {r.id: r for r in rows}
    kw_max = max(kw_scores.values()) if kw_scores else 1.0
    combined: list[tuple[float, KnowledgeEntry]] = []
    for eid in cand_ids:
        row = by_id.get(eid)
        if row is None:
            continue
        vec_n = vec_scores.get(eid, 0.0)
        kw_n = kw_scores.get(eid, 0) / kw_max
        combined.append((0.6 * vec_n + 0.4 * kw_n, row))
    combined.sort(key=lambda x: -x[0])

    hits: list[RAGHit] = []
    for score, row in combined[:top_k]:
        if kb_type and row.kb_type != kb_type:
            continue
        excerpt = _excerpt(row.content, query)
        hits.append(
            RAGHit(
                entry_id=row.id,
                kb_type=row.kb_type,
                title=row.title,
                excerpt=excerpt,
                source=row.source,
                version=row.version,
                score=score,
            )
        )
    return hits


def _excerpt(content: str, query: str, width: int = 90) -> str:
    idx = 0
    for t in _terms(query):
        if len(t) >= 2:
            p = content.find(t)
            if p >= 0:
                idx = p
                break
    start = max(0, idx - width // 3)
    return content[start : start + width] + ("…" if len(content) > start + width else "")


def sentence_snippet(content: str, query: str, max_sentences: int = 2) -> str:
    """抽取与问句最相关的 1~2 个句子（按 。！？切分），找不到则取开头两短句。"""
    terms = [t for t in _terms(query) if len(t) >= 2]
    sentences = [s.strip() for s in re.split(r"(?<=[。！？])|\n", content) if len(s.strip()) > 8]
    if not sentences:
        return content[:120]
    scored: list[tuple[int, int, str]] = []
    for i, s in enumerate(sentences):
        hits = sum(1 for t in terms if t in s)
        if hits:
            scored.append((hits, -i, s))  # 命中数优先，同分取前句
    scored.sort(key=lambda x: (-x[0], x[1]))
    if not scored:
        return "".join(sentences[:2])[:160]
    pick = [s for _, _, s in scored[:max_sentences]]
    return " ".join(pick)[:240]


def citations_of(hits: list[RAGHit]) -> list[dict]:
    """引用来源（知识条目标识 + 版本），供前端折叠展示。"""
    return [
        {
            "entry_id": h.entry_id,
            "title": h.title,
            "kb_type": h.kb_type,
            "source": h.source,
            "version": h.version,
        }
        for h in hits
    ]
