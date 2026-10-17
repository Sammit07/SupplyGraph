"""Runtime configuration, loaded from environment variables (prefix ``SUPPLYGRAPH_``)."""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Annotated

from dotenv import load_dotenv
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

TENANT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SUPPLYGRAPH_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # FalkorDB (FalkorDB Cloud: host, port, username and password from the console)
    falkordb_host: str = "localhost"
    falkordb_port: int = 6379
    falkordb_username: str | None = None
    falkordb_password: SecretStr | None = None
    falkordb_ssl: bool = False
    # Set for FalkorDB cluster deployments; standalone/Cloud single-node instances leave it off.
    falkordb_cluster: bool = False
    falkordb_max_connections: int = 16
    graph_prefix: str = "supplygraph"
    default_tenant: str = "demo"
    # Server-side query timeout in milliseconds; every query is bounded.
    query_timeout_ms: int = Field(default=10_000, ge=100, le=60_000)

    # Business settings
    base_currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")

    # LLM (OpenAI; the SDK reads OPENAI_API_KEY from the environment).
    openai_model: str = "gpt-5.5"
    llm_effort: str = Field(default="medium", pattern="^(none|minimal|low|medium|high|xhigh|max)$")
    llm_extraction_effort: str = Field(
        default="medium", pattern="^(none|minimal|low|medium|high|xhigh|max)$"
    )
    llm_max_output_tokens: int = Field(default=16_000, ge=1_000, le=128_000)
    agent_max_tool_rounds: int = Field(default=8, ge=1, le=25)

    # Document passages (GraphRAG). The vector index dimension is fixed per workspace when
    # the first document is indexed; changing it later needs a new workspace.
    openai_embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = Field(default=1536, ge=8, le=3072)
    passage_chars: int = Field(default=900, ge=200, le=4000)
    passage_overlap_chars: int = Field(default=150, ge=0, le=1000)
    max_passages_per_document: int = Field(default=300, ge=1, le=5000)
    # Cosine similarity below which a passage is not considered relevant.
    min_passage_similarity: float = Field(default=0.2, ge=0.0, le=1.0)

    # Imports. Vercel Functions accept request bodies up to 4.5 MB.
    import_max_bytes: int = Field(default=4_000_000, ge=10_000)
    import_max_rows_per_file: int = Field(default=200_000, ge=1)
    pdf_max_bytes: int = Field(default=4_000_000, ge=10_000)

    # Explorer / dashboards
    network_max_nodes: int = Field(default=1_500, ge=50, le=20_000)
    overview_max_disruptions: int = Field(default=12, ge=1, le=100)

    # API
    # Shared secret(s) between the Next.js server and this API. Required in production.
    api_keys: Annotated[list[SecretStr], NoDecode] = Field(default_factory=list)
    expose_docs: bool = False
    log_level: str = "INFO"
    log_json: bool = True

    @field_validator("default_tenant")
    @classmethod
    def _tenant_ok(cls, v: str) -> str:
        if not TENANT_RE.fullmatch(v):
            raise ValueError("default_tenant must match " + TENANT_RE.pattern)
        return v

    @field_validator("api_keys", mode="before")
    @classmethod
    def _split_keys(cls, v: object) -> object:
        # Accept "key1,key2" from env vars as well as JSON lists.
        if isinstance(v, str):
            v = v.strip()
            if v.startswith("["):
                import json

                return json.loads(v)
            return [k.strip() for k in v.split(",") if k.strip()]
        return v


@lru_cache
def get_settings() -> Settings:
    # Third-party SDKs (OpenAI) read their keys from the process environment, not from
    # Settings. Export .env for local runs; real environment variables (Vercel) win.
    load_dotenv(".env", override=False)
    return Settings()
