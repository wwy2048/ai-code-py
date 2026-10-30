from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用运行配置，全部通过环境变量注入。"""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    database_url: str
    redis_url: str = "redis://localhost:6379/0"
    api_prefix: str = "/api"
    session_cookie_name: str = "AICODE_SESSION"
    session_cookie_secure: bool = False
    session_ttl_seconds: int = 3600
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    password_hash_memory_cost: int = 65536
    password_hash_time_cost: int = 3
    password_hash_parallelism: int = 4

    anthropic_api_key: str | None = None
    anthropic_base_url: str = "https://api.deepseek.com/anthropic"
    anthropic_model: str = "deepseek-flash"
    anthropic_timeout_seconds: float = 30.0
    anthropic_max_tokens: int = 8000
    model_context_window_tokens: int = 128000
    context_compaction_trigger_ratio: float = 0.8
    context_compaction_summary_max_tokens: int = 2000
    context_compaction_fixed_overhead_tokens: int = 2000
    model_retry_base_seconds: float = 0.5
    model_max_retries: int = 3

    workspace_root: str = "tmp/codeOutput"
    deploy_root: str = "tmp/code_deploy"
    deploy_url_base: str = "http://localhost:8123/api/deploy"
    screenshots_root: str = "tmp/screenshots"
    screenshot_timeout_seconds: int = 30
    screenshot_wait_after_load_seconds: float = 1.0
    cover_shutdown_timeout_seconds: float = 10.0

    app_memory_enabled: bool = False
    app_memory_max_chars: int = 4000
    subagent_enabled: bool = False
    subagent_max_rounds: int = 6
    subagent_timeout_seconds: float = 60.0

    cos_secret_id: str | None = None
    cos_secret_key: str | None = None
    cos_region: str | None = None
    cos_bucket: str | None = None
    cos_public_base_url: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
