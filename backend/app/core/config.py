"""Application configuration loaded from environment variables.

Required settings are validated when the Settings instance is created,
which happens during application startup. A missing DATABASE_URL fails
fast instead of surfacing later as an obscure connection error.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    service_name: str = "partflow-api"


@lru_cache
def get_settings() -> Settings:
    # BaseSettings loads required values from environment at runtime.
    return Settings()  # type: ignore[call-arg]
