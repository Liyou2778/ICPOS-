"""ICOPS 全局配置（依据研制指导书附录 B 环境变量基线）。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# 仓库根：backend/app/core/config.py -> parents[3] == icops/
REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- 环境 ---
    app_env: str = "dev"  # dev | demo（演示环境必须 demo）
    app_host: str = "0.0.0.0"
    app_port: int = 8000

    # --- 大模型网关 ---
    llm_provider: str = "deepseek"
    llm_model: str = "deepseek-chat"
    llm_backup_provider: str = "dashscope"
    llm_backup_model: str = "qwen-plus"
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_backup_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    deepseek_api_key: str = ""
    dashscope_api_key: str = ""
    llm_timeout_stream_first_byte: float = 3.0
    llm_timeout_stream_total: float = 60.0
    llm_timeout_non_stream: float = 30.0
    llm_max_retries: int = 2
    llm_daily_budget_cny: float = 50.0

    # --- Embedding ---
    embedding_provider: str = "local"  # dashscope | local
    embedding_model: str = "text-embedding-v3"

    # --- 地图（前端使用为主；后端围栏/编码预留）---
    amap_key: str = ""
    amap_security_code: str = ""  # 高德 JS API 2.0 安全密钥（jscode）

    # --- 存储 ---
    database_url: str = "sqlite:///./data/icops.db"
    vector_store_path: str = "./data/vectorstore"
    vector_backend: str = "auto"  # auto | chroma | builtin

    # --- RAG ---
    rag_top_k: int = Field(default=5, ge=1, le=20)
    rag_chunk_size: int = Field(default=700, ge=100)
    rag_chunk_overlap: float = Field(default=0.12, ge=0.0, lt=0.5)

    # --- 会话安全 ---
    jwt_secret: str = "CHANGE_ME_to_a_long_random_secret"
    token_ttl_hours: int = 12

    # --- 可观测（可选）---
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "http://localhost:3000"

    # --- 文档导出 ---
    libreoffice_path: str = "auto"  # auto | 可执行文件绝对路径

    @property
    def repo_root(self) -> Path:
        return REPO_ROOT

    @property
    def db_path(self) -> str:
        """把相对 sqlite 路径解析到仓库根，避免依赖进程 cwd。"""
        url = self.database_url
        if url.startswith("sqlite:///"):
            rel = url[len("sqlite:///") :]
            p = Path(rel)
            if not p.is_absolute():
                p = REPO_ROOT / p
            return f"sqlite:///{p.as_posix()}"
        return url

    @property
    def vector_path(self) -> Path:
        p = Path(self.vector_store_path)
        return p if p.is_absolute() else REPO_ROOT / p

    @property
    def is_demo_env(self) -> bool:
        return self.app_env == "demo"

    @property
    def has_deepseek(self) -> bool:
        return bool(self.deepseek_api_key.strip())

    @property
    def has_dashscope(self) -> bool:
        return bool(self.dashscope_api_key.strip())

    @property
    def has_real_llm(self) -> bool:
        return self.has_deepseek or self.has_dashscope


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
