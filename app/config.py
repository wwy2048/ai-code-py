from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用运行配置，全部通过环境变量注入。"""

    model_config = SettingsConfigDict(env_file='.env', extra='ignore', case_sensitive=False)

    database_url: str
    redis_url: str = 'redis://localhost:6379/0'
    api_prefix: str = '/api'
    session_cookie_name: str = 'AICODE_SESSION'
    session_cookie_secure: bool = False
    session_ttl_seconds: int = 3600
    cors_origins: list[str] = Field(default_factory=lambda: ['http://localhost:5173'])

    password_hash_memory_cost: int = 65536
    password_hash_time_cost: int = 3
    password_hash_parallelism: int = 4


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
