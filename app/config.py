from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-20b"
    triage_model: str = "llama-3.1-8b-instant"
    database_url: str = "sqlite+aiosqlite:///./data/app.db"
    redis_url: str = "redis://localhost:6379/0"
    rerank_threshold: float = 0.42
    max_loop_steps: int = 5
    step_timeout_ms: int = 120
    loop_budget_ms: int = 450
    mcp_timeout_ms: int = 150

    # Design-driven settings (design.md §1.8, §1.4.2, ADR-3).
    extractive_deadline_ms: int = 450  # the gated path
    generative_deadline_ms: int = 2000  # generative answers are reported separately (design §1.4.2)
    llm_timeout_ms: int = 1500
    tenant_rate_per_s: float = 50.0
    tenant_burst: float = 100.0
    worker_concurrency: int = 256  # design Gate 9 llm_pool
    cache_ttl_s: int = 300
    cache_ttl_jitter_s: int = 45

    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).resolve().parents[1] / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
