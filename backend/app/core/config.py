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

Phase 16 slice 3 adds the release identity and the release gate:
``RELEASE_TAG`` / ``RELEASE_COMMIT`` (set by the production image from its
build arguments, never by Compose), ``ENFORCE_CLIENT_RELEASE`` (fixed on in
the production Compose file) and ``ACCEPT_SCHEMA_REVISION`` (the per-revision
readiness override of rollback path 2).

Phase 16 slice 4 adds ``DATABASE_ROLES_REQUIRED`` and shares the secret-file
reader (``read_secret_line``) with ``provision-roles``.
"""

import re
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    Field,
    TypeAdapter,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)
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

DEVELOPMENT_RELEASE = "development"
_RELEASE_TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_RELEASE_COMMIT = re.compile(r"[0-9a-f]{40}")
_ALEMBIC_REVISION = re.compile(r"[A-Za-z0-9_]{1,32}")
_RELEASE_TAG_INVALID = (
    "RELEASE_TAG must be a release tag of at most 64 letters, digits, '.', '_' or '-'"
    " (for example v1.0.0-rc.1)."
)
_RELEASE_COMMIT_INVALID = "RELEASE_COMMIT must be a full 40-character lowercase Git commit SHA."
_ACCEPT_SCHEMA_REVISION_INVALID = (
    "ACCEPT_SCHEMA_REVISION must be one Alembic revision id of at most 32 letters, digits or"
    " underscores."
)
_ENFORCEMENT_NEEDS_RELEASE = (
    'ENFORCE_CLIENT_RELEASE=true needs a release image: RELEASE_TAG is "development"'
    " (build the image with PARTFLOW_RELEASE)."
)


def _present(value: object) -> bool:
    """Set to a non-empty value (an empty environment variable counts as unset)."""
    return value is not None and value != ""


class SecretFileError(Exception):
    """A secret file is not exactly one UTF-8 line; never carries its content."""

    def __init__(
        self,
        kind: Literal["unreadable", "not_utf8", "empty", "multiline"],
        reason: str | None = None,
    ) -> None:
        super().__init__(kind)
        self.kind = kind
        #: The operating-system reason of an ``unreadable`` file.
        self.reason = reason


def read_secret_line(path: Path) -> str:
    """The one line of the secret file ``path`` (trailing CR/LF dropped).

    Shared by ``DATABASE_PASSWORD_FILE`` and ``provision-roles``' role
    password files (Phase 16 slice 4); each caller words the error.
    """
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise SecretFileError("unreadable", exc.strerror or type(exc).__name__) from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        # `from None`: the decode error quotes a byte of the secret.
        raise SecretFileError("not_utf8") from None
    # initdb takes the first line without its CR/LF, so only trailing line
    # breaks are dropped and the file must hold exactly one line.
    line = text.rstrip("\r\n")
    if not line:
        raise SecretFileError("empty")
    if "\r" in line or "\n" in line:
        raise SecretFileError("multiline")
    return line


def _read_password_file(path: str) -> str:
    """The one-line password of ``path``; its content never reaches a message."""
    try:
        return read_secret_line(Path(path))
    except SecretFileError as exc:
        if exc.kind == "unreadable":
            raise ValueError(
                f"DATABASE_PASSWORD_FILE {path} cannot be read ({exc.reason}). Check that the"
                " secret file exists and that the backend user may read it."
            ) from exc
        if exc.kind == "not_utf8":
            raise ValueError(f"DATABASE_PASSWORD_FILE {path} is not UTF-8 text.") from None
        if exc.kind == "empty":
            raise ValueError(f"DATABASE_PASSWORD_FILE {path} is empty.") from None
        raise ValueError(f"DATABASE_PASSWORD_FILE {path} must hold exactly one line.") from None


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
    # Release identity (Phase 16 slice 3): baked into the production image
    # from its build arguments; "development" everywhere else.
    release_tag: str = DEVELOPMENT_RELEASE
    release_commit: str | None = None
    # Refuse unsafe requests whose X-PartFlow-Release differs from
    # release_tag (app.api.release_gate). Fixed on in production Compose.
    enforce_client_release: bool = False
    # Rollback path 2 only: the one database revision, unknown to this
    # release, that it may serve after a verified compatibility check.
    accept_schema_revision: str | None = None
    # Phase 16 slice 4: the PartFlow database roles must exist, so migrate
    # refuses without them and reconcile check (h) runs (true on backend
    # and migrate in production Compose only; development and test
    # databases keep one owner role).
    database_roles_required: bool = False

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

    @field_validator("release_tag")
    @classmethod
    def _release_tag(cls, value: str) -> str:
        if _RELEASE_TAG.fullmatch(value) is None:
            raise ValueError(_RELEASE_TAG_INVALID)
        return value

    @field_validator("release_commit", mode="before")
    @classmethod
    def _release_commit(cls, value: object) -> object:
        if value is None or value == "":
            return None
        if not isinstance(value, str) or _RELEASE_COMMIT.fullmatch(value) is None:
            raise ValueError(_RELEASE_COMMIT_INVALID)
        return value

    @field_validator("accept_schema_revision", mode="before")
    @classmethod
    def _accept_schema_revision(cls, value: object) -> object:
        if value is None or value == "":
            return None
        if not isinstance(value, str) or _ALEMBIC_REVISION.fullmatch(value) is None:
            raise ValueError(_ACCEPT_SCHEMA_REVISION_INVALID)
        return value

    @field_validator("enforce_client_release")
    @classmethod
    def _enforcement_needs_a_release(cls, value: bool, info: ValidationInfo) -> bool:
        # A field validator (release_tag is validated before it; absent when
        # refused): the error's input is this flag only, never the whole
        # configuration.
        if value and info.data.get("release_tag") == DEVELOPMENT_RELEASE:
            raise ValueError(_ENFORCEMENT_NEEDS_RELEASE)
        return value


@lru_cache
def get_settings() -> Settings:
    # BaseSettings loads required values from environment at runtime.
    return Settings()  # type: ignore[call-arg]
