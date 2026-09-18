"""向量库抽象：Chroma（完整模式）与内置降级存储（离线演示，零外部依赖）。

VECTOR_BACKEND=auto 时优先 Chroma；不可用（未安装 chromadb）则自动降级内置存储。
两类存储对外接口一致：add / query / count。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np

from backend.app.core.config import settings
from backend.app.core.embedding import embed_texts, local_embed_texts

logger = logging.getLogger("icops.vectorstore")


class BaseVectorStore:
    def add(self, ids: list[str], texts: list[str], metadatas: list[dict]) -> None: ...
    def query(self, query_text: str, top_k: int = 5) -> list[dict]:
        """返回 [{id, score, metadata}] 按相似度降序。"""

    def count(self) -> int: ...

    def reset(self) -> int:
        """清空全部向量，返回清空前的条目数（知识库整体重建时使用）。"""
        raise NotImplementedError


class BuiltinVectorStore(BaseVectorStore):
    """内置降级向量库：numpy 余弦检索 + JSON/二进制持久化。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.meta_file = path / "meta.json"
        self.vec_file = path / "vectors.npy"
        self.records: list[dict] = []
        self.vectors: np.ndarray | None = None
        self._load()

    def _load(self) -> None:
        if self.meta_file.exists() and self.vec_file.exists():
            self.records = json.loads(self.meta_file.read_text(encoding="utf-8"))
            self.vectors = np.load(self.vec_file)

    def _save(self) -> None:
        self.path.mkdir(parents=True, exist_ok=True)
        self.meta_file.write_text(json.dumps(self.records, ensure_ascii=False), encoding="utf-8")
        np.save(self.vec_file, self.vectors)

    def add(self, ids: list[str], texts: list[str], metadatas: list[dict]) -> None:
        vecs = embed_texts(texts)
        existing = {r["id"] for r in self.records}
        for i, vid in enumerate(ids):
            if vid in existing:
                idx = next(j for j, r in enumerate(self.records) if r["id"] == vid)
                self.records[idx].update(metadatas[i])
                self.vectors[idx] = vecs[i]
            else:
                self.records.append({"id": vid, **metadatas[i]})
                if self.vectors is None:
                    self.vectors = vecs[i : i + 1]
                else:
                    self.vectors = np.vstack([self.vectors, vecs[i : i + 1]])
        self._save()

    def query(self, query_text: str, top_k: int = 5) -> list[dict]:
        if not self.records:
            return []
        q = embed_texts([query_text])[0]
        scores = self.vectors @ q
        order = np.argsort(-scores)[:top_k]
        return [
            {
                "id": self.records[int(i)]["id"],
                "score": float(scores[int(i)]),
                "metadata": {k: v for k, v in self.records[int(i)].items() if k != "id"},
            }
            for i in order
        ]

    def count(self) -> int:
        return len(self.records)

    def reset(self) -> int:
        n = len(self.records)
        self.records = []
        self.vectors = None
        for f in (self.meta_file, self.vec_file):
            if f.exists():
                f.unlink()
        return n


def embed_one_local(text: str) -> np.ndarray:
    return local_embed_texts([text])[0].reshape(-1)


class ChromaVectorStore(BaseVectorStore):
    """Chroma 向量库（完整模式）。"""

    def __init__(self, path: Path) -> None:
        import chromadb

        self.client = chromadb.PersistentClient(path=str(path))
        self.col = self.client.get_or_create_collection("icops_kb", metadata={"hnsw:space": "cosine"})

    def add(self, ids: list[str], texts: list[str], metadatas: list[dict]) -> None:
        # 传 embeddings 以避免 chroma 默认下载嵌入模型
        self.col.upsert(
            ids=ids, embeddings=[v.tolist() for v in embed_texts(texts)], metadatas=metadatas, documents=texts
        )

    def query(self, query_text: str, top_k: int = 5) -> list[dict]:
        res = self.col.query(query_embeddings=[embed_texts([query_text])[0].tolist()], n_results=top_k)
        out = []
        for i, rid in enumerate(res["ids"][0]):
            md = res["metadatas"][0][i] or {}
            # chroma 返回 cosine 距离（越小越相似），统一转换为相似度得分
            out.append({"id": rid, "score": 1.0 - float(res["distances"][0][i]), "metadata": md})
        return out

    def count(self) -> int:
        return self.col.count()

    def reset(self) -> int:
        n = self.col.count()
        if n:
            self.client.delete_collection("icops_kb")
            self.col = self.client.get_or_create_collection("icops_kb", metadata={"hnsw:space": "cosine"})
        return n


class VectorStoreFactory:
    def __init__(self) -> None:
        self._store: BaseVectorStore | None = None

    def get(self) -> BaseVectorStore:
        if self._store is not None:
            return self._store
        path = settings.vector_path
        backend = settings.vector_backend
        store: BaseVectorStore | None = None
        if backend in ("auto", "chroma"):
            try:
                store = ChromaVectorStore(path)
                logger.info("向量库使用 Chroma（path=%s）", path)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Chroma 初始化失败，降级内置向量库：%s", exc)
                store = None
        if store is None:
            store = BuiltinVectorStore(path)
            logger.info("向量库使用内置降级存储（path=%s）", path)
        self._store = store
        return store


vector_store = VectorStoreFactory()
