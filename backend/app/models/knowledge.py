"""知识库域：统一知识条目（表4-3 知识库域，向量化入库的文本来源登记）。"""

from __future__ import annotations

from datetime import datetime, UTC

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class KnowledgeEntry(Base):
    """知识条目登记：每一条向量化文本/结构化记录的元数据，供引用溯源（版本+来源）。"""

    __tablename__ = "kb_knowledge_entry"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kb_type: Mapped[str] = mapped_column(String(24), index=True)  # equipment|process|maintenance|template
    title: Mapped[str] = mapped_column(String(256))
    content: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[str] = mapped_column(String(512), default="")
    source: Mapped[str] = mapped_column(String(256), default="")
    version: Mapped[str] = mapped_column(String(16), default="V1.0")
    ref_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SolutionDocument(Base):
    """方案文档（选型方案/投标方案/施工组织设计）的已生成记录，支撑下载与溯源。"""

    __tablename__ = "kb_solution_document"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    doc_type: Mapped[str] = mapped_column(String(24), default="selection")  # selection|bid|construction
    title: Mapped[str] = mapped_column(String(256))
    payload: Mapped[str] = mapped_column(Text, default="")  # 结构化内容 JSON 文本
    file_path: Mapped[str] = mapped_column(String(512), default="")  # 生成文件路径
    model_version: Mapped[str] = mapped_column(String(32), default="")
    kb_version: Mapped[str] = mapped_column(String(16), default="V1.0")
    meta: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
