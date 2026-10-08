"""Application configuration loaded from environment variables.

Required settings are validated when the Settings instance is created,
which happens during application startup, so a missing or invalid
database connection fails fast instead of surfacing later as an obscure
connection error. The connection comes from exactly one of two forms:

1. ``DATABASE_URL`` (development, tests, CI, pf-managed staging), or
2. ``DATABASE_HOST``, ``DATABASE_NAME``, ``DATABASE_USER`` and
   ``DATABASE_PASSWORD_FILE`` plus optional ``DATABASE_PORT`` (the Phase 16
   production stack: the password is a secret file, never an environment
   value). The URL is composed from them into ``database_url``.
"""

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, TypeAdapter, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

_DatabasePort = Annotated[int, Field(ge=1, le=65535)]
_PORT_ADAPTER: TypeAdapter[int] = TypeAdapter(_DatabasePort)
_DEFAULT_DATABASE_PORT = 5432

# Field name -> environment name of the four required form-2 settings, in
# the order the "missing" list names them.
_FORM_TWO_REQUIRED = {
    "database_host": "DATABASE_HOST",
    "database_name": "DATABASE_NAME",
    "database_user": "DATABASE_USER",
    "database_password_file": "DATABASE_PASSWORD_FILE",
}
_FORM_TWO_NAMES = "DATABASE_HOST, DATABASE_NAME, DATABASE_USER and DATABASE_PASSWORD_FILE"
_CONNECTION_FIELDS = frozenset({"database_url", "database_port", *_FORM_TWO_REQUIRED})


def _present(value: object) -> bool:
    """Set to a non-empty value (an empty environment variable counts as unset)."""
    return value is not None and value != ""


def _read_password_file(path: str) -> str:
    """The one-line password of ``path``; its content never reaches a message."""
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        reason = exc.strerror or type(exc).__name__
        raise ValueError(
            f"DATABASE_PASSWORD_FILE {path} cannot be read ({reason}). Check that the secret"
            " file exists and that the backend user may read it."
        ) from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        # `from None`: the decode error quotes a byte of the secret.
        raise ValueError(f"DATABASE_PASSWORD_FILE {path} is not UTF-8 text.") from None
    # initdb takes the first line without its CR/LF, so only trailing line
    # breaks are dropped and the file must hold exactly one line.
    password = text.rstrip("\r\n")
    if not password:
        raise ValueError(f"DATABASE_PASSWORD_FILE {path} is empty.")
    if "\r" in password or "\n" in password:
        raise ValueError(f"DATABASE_PASSWORD_FILE {path} must hold exactly one line.")
    return password


class Settings(BaseSettings):
    # hide_input_in_errors: no ValidationError text repeats an input value
    # (a DATABASE_URL, a password or a file path's content).
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    # Filled from DATABASE_URL, or composed from the form-2 settings below.
    database_url: str
    database_host: str | None = None
    database_port: _DatabasePort = _DEFAULT_DATABASE_PORT
    database_name: str | None = None
    database_user: str | None = None
    database_password_file: Path | None = None
    service_name: str = "partflow-api"
    # The factory's calendar (an IANA zone name): the ONE time zone in
    # which a timestamp becomes a calendar date wherever the domain
    # compares one with a date — the done date of a completed Work
    # Order against its due date (PROJECT_PROFILE §18 completion, GUI
    # §11.5). Derived on the server only, never in a browser's local
    # time, so a filter and the row it returns can never disagree.
    site_timezone: str = "UTC"
    # Whether the User session cookie carries `Secure` (Phase 14 slice 1).
    # Off for plain-HTTP development; Phase 16 turns it on behind TLS.
    session_cookie_secure: bool = False

    @model_validator(mode="before")
    @classmethod
    def _database_connection(cls, data: Any) -> Any:
        """Accept exactly one connection form; compose the URL from form 2."""
        if not isinstance(data, dict):
            return data
        # A copy: an error's input is the raw mapping, never a composed URL.
        values = {
            key: value
            for key, value in data.items()
            if key not in _CONNECTION_FIELDS or _present(value)
        }
        if "database_url" in values:
            if any(field in values for field in _FORM_TWO_REQUIRED):
                raise ValueError(f"Set either DATABASE_URL or {_FORM_TWO_NAMES}, not both.")
            return values
        missing = [name for field, name in _FORM_TWO_REQUIRED.items() if field not in values]
        if missing:
            raise ValueError(
                "PartFlow has no database connection: set DATABASE_URL, or"
                f" {_FORM_TWO_NAMES} (missing: {', '.join(missing)})."
            )
        try:
            port = _PORT_ADAPTER.validate_python(
                values.get("database_port", _DEFAULT_DATABASE_PORT), strict=False
            )
        except ValidationError:
            raise ValueError("DATABASE_PORT must be a whole number from 1 to 65535.") from None
        password = _read_password_file(str(values["database_password_file"]))
        values["database_url"] = URL.create(
            "postgresql+psycopg",
            username=str(values["database_user"]),
            password=password,
            host=str(values["database_host"]),
            port=port,
            database=str(values["database_name"]),
        ).render_as_string(hide_password=False)
        return values

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
