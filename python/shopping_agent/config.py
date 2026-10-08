"""Environment-backed configuration for the shopping assistant."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///./shopping_agent.db"
    seed_demo: bool = True
    llm_api_key: str = field(default="", repr=False)
    llm_base_url: str = ""
    llm_model: str = ""
    llm_timeout_seconds: float = 8.0
    embedding_model: str = ""
    embedding_base_url: str = ""
    embedding_provider: str = ""
    embedding_api_key: str = field(default="", repr=False)
    embedding_allow_remote: bool = False
    embedding_timeout_seconds: float = 8.0
    retrieval_mode: str = "bm25"
    tool_timeout_seconds: float = 10.0
    experiment_enabled: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.getenv("SHOPPING_DATABASE_URL", cls.database_url),
            seed_demo=os.getenv("SHOPPING_SEED_DEMO", "true").lower() in {"1", "true", "yes"},
            llm_api_key=os.getenv("SHOPPING_LLM_API_KEY", ""),
            llm_base_url=os.getenv("SHOPPING_LLM_BASE_URL", ""),
            llm_model=os.getenv("SHOPPING_LLM_MODEL", ""),
            llm_timeout_seconds=float(os.getenv("SHOPPING_LLM_TIMEOUT_SECONDS", "8")),
            embedding_model=os.getenv("SHOPPING_EMBEDDING_MODEL", ""),
            embedding_base_url=os.getenv(
                "SHOPPING_EMBEDDING_BASE_URL", ""
            ),
            embedding_provider=os.getenv("SHOPPING_EMBEDDING_PROVIDER", ""),
            embedding_api_key=os.getenv("SHOPPING_EMBEDDING_API_KEY") or os.getenv("DASHSCOPE_API_KEY", ""),
            embedding_allow_remote=os.getenv("SHOPPING_EMBEDDING_ALLOW_REMOTE", "false").lower() in {"1", "true", "yes"},
            embedding_timeout_seconds=float(
                os.getenv("SHOPPING_EMBEDDING_TIMEOUT_SECONDS", "8")
            ),
            retrieval_mode=os.getenv("SHOPPING_RETRIEVAL_MODE", "bm25"),
            tool_timeout_seconds=float(os.getenv("SHOPPING_TOOL_TIMEOUT_SECONDS", "10")),
            experiment_enabled=os.getenv("SHOPPING_EXPERIMENT_ENABLED", "false").lower()
            in {"1", "true", "yes"},
        )
