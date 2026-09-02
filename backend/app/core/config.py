"""Application configuration loaded from environment variables.

Required settings are validated when the Settings instance is created,
which happens during application startup. A missing DATABASE_URL fails
fast instead of surfacing later as an obscure connection error.
"""

from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    service_name: str = "partflow-api"
    # The factory's calendar (an IANA zone name): the ONE time zone in
    # which a timestamp becomes a calendar date wherever the domain
    # compares one with a date — the done date of a completed Work
    # Order against its due date (PROJECT_PROFILE §18 completion, GUI
    # §11.5). Derived on the server only, never in a browser's local
    # time, so a filter and the row it returns can never disagree.
    site_timezone: str = "UTC"

    @field_validator("site_timezone")
    @classmethod
    def _known_zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"SITE_TIMEZONE '{value}' is not a known IANA time zone.") from exc
        return value


@lru_cache
def get_settings() -> Settings:
    # BaseSettings loads required values from environment at runtime.
    return Settings()  # type: ignore[call-arg]
