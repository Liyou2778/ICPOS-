"""文本嵌入：真实（dashscope text-embedding-v3）与本地离线哈希两种实现。

指导书 6.2 / 附录 B：EMBEDDING_PROVIDER = dashscope | local。
local 模式为确定性离线演示方案（零下载零密钥）；接入真实嵌入仅需配置密钥。
"""

from __future__ import annotations

import hashlib
import logging

import numpy as np

from backend.app.core.config import settings

logger = logging.getLogger("icops.embed")

LOCAL_DIM = 384


def local_embed_texts(texts: list[str]) -> np.ndarray:
    """字符 n-gram 哈希嵌入（确定性、离线），输出 L2 归一化向量。"""
    out = np.zeros((len(texts), LOCAL_DIM), dtype=np.float32)
    for i, text in enumerate(texts):
        vec = np.zeros(LOCAL_DIM, dtype=np.float64)
        grams: list[str] = []
        t = "".join(ch for ch in text if not ch.isspace())
        for n in (2, 3):
            grams.extend(t[j : j + n] for j in range(max(0, len(t) - n + 1)))
        for g in grams:
            h = int(hashlib.md5(g.encode("utf-8")).hexdigest()[:8], 16)
            vec[h % LOCAL_DIM] += 1.0
        norm = float(np.linalg.norm(vec))
        out[i] = vec / norm if norm > 0 else vec
    return out


def _dashscope_embed(texts: list[str], model: str) -> list[list[float]]:
    import httpx

    url = settings.llm_backup_base_url.rstrip("/") + "/embeddings"
    with httpx.Client(timeout=60.0) as client:
        resp = client.post(
            url,
            headers={"Authorization": f"Bearer {settings.dashscope_api_key}"},
            json={"model": model, "input": texts},
        )
        resp.raise_for_status()
        data = resp.json()
        items = sorted(data["data"], key=lambda x: x["index"])
        return [it["embedding"] for it in items]


def embed_texts(texts: list[str]) -> np.ndarray:
    """对外统一入口：返回 (n, dim) float32 矩阵。"""
    if not texts:
        return np.zeros((0, LOCAL_DIM), dtype=np.float32)
    provider = settings.embedding_provider
    if provider == "dashscope" and settings.has_dashscope:
        try:
            vecs = _dashscope_embed(texts, settings.embedding_model)
            return np.asarray(vecs, dtype=np.float32)
        except Exception as exc:  # noqa: BLE001  真实嵌入失败降级本地，保证可用性
            logger.warning("dashscope 嵌入失败，降级 local 嵌入：%s", exc)
    return local_embed_texts(texts)


def embed_one(text: str) -> np.ndarray:
    return embed_texts([text])[0]
