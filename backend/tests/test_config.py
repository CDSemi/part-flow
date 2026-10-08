"""Tests for the database connection settings (Phase 16 slice 2).

The backend takes its connection from exactly one of ``DATABASE_URL`` or
the file-based form ``DATABASE_HOST`` / ``DATABASE_NAME`` /
``DATABASE_USER`` / ``DATABASE_PASSWORD_FILE`` (+ optional
``DATABASE_PORT``) of the production stack. The password file must hold
exactly one line, the way ``initdb`` reads it, and no refusal ever
repeats an input value. Phase 16 slice 3 adds the release identity and
release-gate settings (C-1 … C-5) and Phase 16 slice 4
``DATABASE_ROLES_REQUIRED`` (CF-12). No database: ``Settings`` is
constructed only.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy.engine import make_url

from app.core.config import Settings

_FORM_TWO = ("DATABASE_HOST", "DATABASE_PORT", "DATABASE_NAME", "DATABASE_USER")
_SECRET = "S3cr3tValue"
_RELEASE = ("RELEASE_TAG", "RELEASE_COMMIT", "ENFORCE_CLIENT_RELEASE", "ACCEPT_SCHEMA_REVISION")


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every case with no connection setting (the suite's DATABASE_URL removed)."""
    for name in (
        "DATABASE_URL",
        *_FORM_TWO,
        "DATABASE_PASSWORD_FILE",
        "SITE_TIMEZONE",
        *_RELEASE,
        "DATABASE_ROLES_REQUIRED",
    ):
        monkeypatch.delenv(name, raising=False)


def _settings() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


def _password_file(tmp_path: Path, content: bytes) -> Path:
    path = tmp_path / "postgres_password"
    path.write_bytes(content)
    return path


def _form_two(monkeypatch: pytest.MonkeyPatch, password_file: Path) -> None:
    monkeypatch.setenv("DATABASE_HOST", "db")
    monkeypatch.setenv("DATABASE_NAME", "partflow")
    monkeypatch.setenv("DATABASE_USER", "owner")
    monkeypatch.setenv("DATABASE_PASSWORD_FILE", str(password_file))


def _refusal() -> str:
    with pytest.raises(ValidationError) as raised:
        _settings()
    return str(raised.value)


def test_form_two_composes_the_url(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """CF-1."""
    password = "p@ss%w:rd/ #+é"
    _form_two(monkeypatch, _password_file(tmp_path, (password + "\n").encode()))
    monkeypatch.setenv("DATABASE_PORT", "6543")
    url = make_url(_settings().database_url)
    assert url.drivername == "postgresql+psycopg"
    assert (url.username, url.password, url.host, url.port, url.database) == (
        "owner",
        password,
        "db",
        6543,
        "partflow",
    )


def test_form_two_default_port(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _form_two(monkeypatch, _password_file(tmp_path, b"secret"))
    settings = _settings()
    assert make_url(settings.database_url).port == 5432
    assert settings.database_port == 5432


@pytest.mark.parametrize(
    ("content", "password"),
    [(b"secret\r\n\r\n", "secret"), (b"  secret  \n", "  secret  ")],
)
def test_only_trailing_line_breaks_are_stripped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, content: bytes, password: str
) -> None:
    """CF-2."""
    _form_two(monkeypatch, _password_file(tmp_path, content))
    assert make_url(_settings().database_url).password == password


def test_unreadable_password_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """CF-3: a missing file and a directory."""
    for path in (tmp_path / "missing", tmp_path):
        _form_two(monkeypatch, path)
        message = _refusal()
        assert f"DATABASE_PASSWORD_FILE {path} cannot be read (" in message
        assert "Check that the secret file exists" in message


@pytest.mark.parametrize("content", [b"", b"\n"])
def test_empty_password_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, content: bytes
) -> None:
    """CF-4."""
    path = _password_file(tmp_path, content)
    _form_two(monkeypatch, path)
    assert f"DATABASE_PASSWORD_FILE {path} is empty." in _refusal()


def test_password_file_not_utf8(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """CF-5."""
    path = _password_file(tmp_path, b"\xff\xfe")
    _form_two(monkeypatch, path)
    assert f"DATABASE_PASSWORD_FILE {path} is not UTF-8 text." in _refusal()


def test_both_forms_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """CF-6."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@h/d")
    monkeypatch.setenv("DATABASE_HOST", "db")
    assert (
        "Set either DATABASE_URL or DATABASE_HOST, DATABASE_NAME, DATABASE_USER and"
        " DATABASE_PASSWORD_FILE, not both."
    ) in _refusal()


@pytest.mark.parametrize(
    ("given", "missing"),
    [
        ((), "DATABASE_HOST, DATABASE_NAME, DATABASE_USER, DATABASE_PASSWORD_FILE"),
        (("DATABASE_HOST", "DATABASE_NAME"), "DATABASE_USER, DATABASE_PASSWORD_FILE"),
    ],
)
def test_no_complete_form_is_refused(
    monkeypatch: pytest.MonkeyPatch, given: tuple[str, ...], missing: str
) -> None:
    """CF-7."""
    for name in given:
        monkeypatch.setenv(name, "value")
    assert (
        "PartFlow has no database connection: set DATABASE_URL, or DATABASE_HOST,"
        " DATABASE_NAME, DATABASE_USER and DATABASE_PASSWORD_FILE"
        f" (missing: {missing})."
    ) in _refusal()


def test_an_empty_setting_counts_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@h/d")
    monkeypatch.setenv("DATABASE_HOST", "")
    assert _settings().database_host is None


def test_database_url_form_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """CF-8."""
    url = "postgresql+psycopg://user:pw@host:5432/name"
    monkeypatch.setenv("DATABASE_URL", url)
    settings = _settings()
    assert settings.database_url == url
    assert settings.database_host is None
    assert settings.database_name is None
    assert settings.database_user is None
    assert settings.database_password_file is None


def test_refusals_never_echo_an_input(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """CF-9."""
    monkeypatch.setenv("DATABASE_URL", f"postgresql+psycopg://u:{_SECRET}@h/d")
    monkeypatch.setenv("SITE_TIMEZONE", "Not/AZone")
    with pytest.raises(ValidationError) as first:
        _settings()
    monkeypatch.delenv("DATABASE_URL")
    monkeypatch.delenv("SITE_TIMEZONE")
    _form_two(monkeypatch, _password_file(tmp_path, _SECRET.encode()))
    monkeypatch.setenv("DATABASE_PORT", "abc")
    with pytest.raises(ValidationError) as second:
        _settings()
    for raised in (first, second):
        assert _SECRET not in str(raised.value)
        assert _SECRET not in repr(raised.value.errors())


@pytest.mark.parametrize("port", ["0", "70000"])
def test_out_of_range_port_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, port: str
) -> None:
    """CF-10."""
    _form_two(monkeypatch, _password_file(tmp_path, b"secret"))
    monkeypatch.setenv("DATABASE_PORT", port)
    assert "DATABASE_PORT must be a whole number from 1 to 65535." in _refusal()


@pytest.mark.parametrize("content", [b"line1\nline2\n", b"pass\rword\n"])
def test_password_file_must_hold_one_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, content: bytes
) -> None:
    """CF-11."""
    path = _password_file(tmp_path, content)
    _form_two(monkeypatch, path)
    message = _refusal()
    assert f"DATABASE_PASSWORD_FILE {path} must hold exactly one line." in message
    # The path itself may contain "word" (the test's temporary directory).
    for fragment in ("line1", "line2", "word"):
        assert fragment not in message.replace(str(path), "")


# ---------------------------------------------------------------------------
# Phase 16 slice 3: release identity and release-gate settings
# ---------------------------------------------------------------------------

_TAG_REFUSED = (
    "RELEASE_TAG must be a release tag of at most 64 letters, digits, '.', '_' or '-'"
    " (for example v1.0.0-rc.1)."
)
_COMMIT_REFUSED = "RELEASE_COMMIT must be a full 40-character lowercase Git commit SHA."
_REVISION_REFUSED = (
    "ACCEPT_SCHEMA_REVISION must be one Alembic revision id of at most 32 letters, digits or"
    " underscores."
)
_ENFORCEMENT_REFUSED = (
    'ENFORCE_CLIENT_RELEASE=true needs a release image: RELEASE_TAG is "development"'
    " (build the image with PARTFLOW_RELEASE)."
)
_COMMIT = "0123456789abcdef0123456789abcdef01234567"


@pytest.fixture
def database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", f"postgresql+psycopg://u:{_SECRET}@h/d")


@pytest.mark.usefixtures("database_url")
def test_release_defaults() -> None:
    """C-1."""
    settings = _settings()
    assert settings.release_tag == "development"
    assert settings.release_commit is None
    assert settings.enforce_client_release is False
    assert settings.accept_schema_revision is None


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("tag", ["bad tag", "-x", "v" * 65])
def test_release_tag(monkeypatch: pytest.MonkeyPatch, tag: str) -> None:
    """C-2."""
    monkeypatch.setenv("RELEASE_TAG", "v1.0.0-rc.1")
    assert _settings().release_tag == "v1.0.0-rc.1"
    monkeypatch.setenv("RELEASE_TAG", tag)
    message = _refusal()
    assert _TAG_REFUSED in message
    assert tag not in message


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("commit", [_COMMIT[:39], _COMMIT.upper()])
def test_release_commit(monkeypatch: pytest.MonkeyPatch, commit: str) -> None:
    """C-3."""
    monkeypatch.setenv("RELEASE_COMMIT", _COMMIT)
    assert _settings().release_commit == _COMMIT
    monkeypatch.setenv("RELEASE_COMMIT", "")
    assert _settings().release_commit is None
    monkeypatch.setenv("RELEASE_COMMIT", commit)
    message = _refusal()
    assert _COMMIT_REFUSED in message
    assert commit not in message


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("revision", ["a-b", "r" * 33])
def test_accept_schema_revision(monkeypatch: pytest.MonkeyPatch, revision: str) -> None:
    """C-4."""
    monkeypatch.setenv("ACCEPT_SCHEMA_REVISION", "0032_phase14_route_adjusted")
    assert _settings().accept_schema_revision == "0032_phase14_route_adjusted"
    monkeypatch.setenv("ACCEPT_SCHEMA_REVISION", "")
    assert _settings().accept_schema_revision is None
    monkeypatch.setenv("ACCEPT_SCHEMA_REVISION", revision)
    message = _refusal()
    assert _REVISION_REFUSED in message
    assert revision not in message


@pytest.mark.usefixtures("database_url")
def test_enforcement_needs_a_release_image(monkeypatch: pytest.MonkeyPatch) -> None:
    """C-5: the refusal carries the flag only, never another setting's value."""
    monkeypatch.setenv("ENFORCE_CLIENT_RELEASE", "true")
    with pytest.raises(ValidationError) as raised:
        _settings()
    assert _ENFORCEMENT_REFUSED in str(raised.value)
    assert _SECRET not in str(raised.value)
    assert _SECRET not in repr(raised.value.errors())
    monkeypatch.setenv("RELEASE_TAG", "v1.0.0")
    settings = _settings()
    assert settings.enforce_client_release is True
    assert settings.release_tag == "v1.0.0"
    # An invalid tag is reported once, not also as a missing release.
    monkeypatch.setenv("RELEASE_TAG", "bad tag")
    with pytest.raises(ValidationError) as invalid:
        _settings()
    assert [error["loc"] for error in invalid.value.errors()] == [("release_tag",)]


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize(("value", "expected"), [("true", True), ("false", False), ("", False)])
def test_database_roles_required(
    monkeypatch: pytest.MonkeyPatch, value: str, expected: bool
) -> None:
    """CF-12: off by default; production Compose sets it on backend and migrate."""
    assert _settings().database_roles_required is False
    monkeypatch.setenv("DATABASE_ROLES_REQUIRED", value)
    if value == "":
        # An empty value is not a boolean (pydantic, like ENFORCE_CLIENT_RELEASE).
        with pytest.raises(ValidationError):
            _settings()
        return
    assert _settings().database_roles_required is expected
