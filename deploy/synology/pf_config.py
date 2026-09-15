"""Strict application ``.env`` parsing and the private frozen configuration snapshot (PF-A1.2).

The editable ``config/.env`` is a *proposal*. Parsing is data parsing only: no
``source``, no evaluation, no command substitution, no host-variable expansion.
Only the PartFlow A1 allowlist (ARCHITECTURE.md section 6) is accepted; unknown,
duplicate, missing, multiline and control-character entries are rejected before
any mutation. Accepted values are rendered into one private snapshot whose
literal round-trip is tested for ``$``, quotes, backslashes and URL credentials.
Existing values that cannot be rendered are an explicit migration issue; they are
never regenerated.

Python standard library only, Python 3.9 language baseline.
"""
import dataclasses
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import types
import urllib.parse
import uuid

APP_KEYS = (
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
    "SITE_TIMEZONE",
    "PARTFLOW_BIND_IP",
    "PARTFLOW_HTTP_PORT",
    "PARTFLOW_ALLOWED_HOST",
)
SECRET_KEYS = ("POSTGRES_PASSWORD",)
# Core-generated keys: never read from the editable file, always derived by the adapter.
GENERATED_KEYS = ("PARTFLOW_REPO_ROOT", "PARTFLOW_DATABASE_URL")
KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
UNQUOTED_VALUE_RE = re.compile(r"[^\s'\"#]+\Z")
SNAPSHOT_FILE = "app.env"
SNAPSHOT_RECORD = "frozen-config.json"
SCHEMA_VERSION = 1


class ConfigError(RuntimeError):
    """A rejected proposal. Nothing was written or changed."""


def _reject_control_characters(value, where):
    for char in value:
        if ord(char) < 0x20 or char == "\x7f":
            raise ConfigError(f"{where}: control character U+{ord(char):04X} is not supported")


def value_render_issue(value):
    """Why ``value`` cannot be rendered into the private snapshot, or None.

    The snapshot renders every value single-quoted, the one form whose contents are
    literal for the strict parser here and for Docker Compose's env-file reader. A
    value containing a single quote, or ending in a backslash (which would escape the
    closing quote for some readers), therefore has no unambiguous rendering.
    """
    if not isinstance(value, str):
        return "not a string"
    for char in value:
        if ord(char) < 0x20 or char == "\x7f":
            return f"control character U+{ord(char):04X}"
    if "'" in value:
        return "single quote (') has no unambiguous single-quoted rendering"
    if value.endswith("\\"):
        return "trailing backslash would escape the closing quote"
    return None


def _parse_double_quoted(body, where):
    """Escapes inside double quotes: ``\\\\`` and ``\\"`` only; anything else is unsupported."""
    result = []
    index = 0
    while index < len(body):
        char = body[index]
        if char == "\\":
            if index + 1 >= len(body):
                raise ConfigError(f"{where}: dangling backslash in a double-quoted value")
            nxt = body[index + 1]
            if nxt not in ("\\", '"'):
                raise ConfigError(f"{where}: unsupported escape \\{nxt} in a double-quoted value "
                                  "(only \\\\ and \\\" are accepted; no $ expansion)")
            result.append(nxt)
            index += 2
            continue
        result.append(char)
        index += 1
    return "".join(result)


def parse_app_env(data, *, label, allowed_keys=APP_KEYS, require_all=True):
    """Strictly parse ``.env`` bytes into {key: literal value}.

    Grammar: UTF-8; ``\\n`` or ``\\r\\n`` line ends (a bare ``\\r`` is rejected); blank
    lines and ``#`` comment lines; ``KEY=VALUE`` with no whitespace around ``=`` and no
    ``export`` prefix. VALUE is one of: unquoted (no whitespace, quotes or ``#``; ``$``
    and ``\\`` are literal), single-quoted (literal, cannot contain ``'``), or
    double-quoted (only ``\\\\`` and ``\\"`` escapes, no expansion). Text after a quoted
    value must be blank or a ``#`` comment. Unknown and duplicate keys are rejected;
    with ``require_all`` every allowed key must be present.
    """
    if not isinstance(data, (bytes, bytearray)):
        raise ConfigError(f"{label}: expected bytes")
    if b"\x00" in data:
        raise ConfigError(f"{label}: NUL byte")
    try:
        text = bytes(data).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigError(f"{label}: not valid UTF-8: {exc}") from exc
    values = {}
    for number, raw in enumerate(text.split("\n"), 1):
        where = f"{label}:{number}"
        if raw.endswith("\r"):
            raw = raw[:-1]
        if "\r" in raw:
            raise ConfigError(f"{where}: bare carriage return")
        _reject_control_characters(raw.replace("\t", ""), where)
        line = raw.strip(" \t")
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ConfigError(f"{where}: expected KEY=VALUE")
        key, rest = raw.split("=", 1)
        if key != key.strip() or not KEY_RE.fullmatch(key):
            raise ConfigError(f"{where}: invalid key {key.strip()!r} (no whitespace around '=', no 'export')")
        if key not in allowed_keys:
            raise ConfigError(f"{where}: unknown key {key!r}; accepted keys are {', '.join(allowed_keys)}")
        if key in values:
            raise ConfigError(f"{where}: duplicate key {key!r}")
        if rest[:1] == "'":
            end = rest.find("'", 1)
            if end < 0:
                raise ConfigError(f"{where}: unterminated single-quoted value")
            value = rest[1:end]
            tail = rest[end + 1:]
        elif rest[:1] == '"':
            end = 1
            while True:
                end = rest.find('"', end)
                if end < 0:
                    raise ConfigError(f"{where}: unterminated double-quoted value")
                backslashes = 0
                probe = end - 1
                while probe > 0 and rest[probe] == "\\":
                    backslashes += 1
                    probe -= 1
                if backslashes % 2 == 0:
                    break
                end += 1
            value = _parse_double_quoted(rest[1:end], where)
            tail = rest[end + 1:]
        else:
            value = rest.rstrip(" \t")
            tail = ""
            if value == "":
                pass
            elif not UNQUOTED_VALUE_RE.fullmatch(value):
                raise ConfigError(f"{where}: unquoted value of {key} contains whitespace, quotes or '#'; "
                                  "quote it with single quotes")
        if tail.strip(" \t") and not tail.strip(" \t").startswith("#"):
            raise ConfigError(f"{where}: unexpected text after the quoted value of {key}")
        _reject_control_characters(value, where)
        values[key] = value
    if require_all:
        missing = [key for key in allowed_keys if key not in values]
        if missing:
            raise ConfigError(f"{label}: missing keys {', '.join(missing)}")
    return values


def render_app_env(values, *, keys=APP_KEYS):
    """Private snapshot bytes: one ``KEY='literal'`` line per key, in allowlist order."""
    lines = ["# Deployment Admin frozen application configuration. Generated; do not edit."]
    for key in keys:
        if key not in values:
            raise ConfigError(f"render: missing {key}")
        issue = value_render_issue(values[key])
        if issue is not None:
            raise ConfigError(f"render: {key}: {issue}")
        lines.append(f"{key}='{values[key]}'")
    return ("\n".join(lines) + "\n").encode("utf-8")


def unsupported_values(values):
    """{key: issue} for every accepted value that has no unambiguous snapshot rendering."""
    problems = {}
    for key, value in values.items():
        issue = value_render_issue(value)
        if issue is not None:
            problems[key] = issue
    return problems


def database_url(user, password, database, *, host="db", port=5432):
    """``postgresql+psycopg://`` URL with percent-encoded credentials (exact round-trip)."""
    return "postgresql+psycopg://{}:{}@{}:{}/{}".format(
        urllib.parse.quote(user, safe=""), urllib.parse.quote(password, safe=""), host, port,
        urllib.parse.quote(database, safe=""),
    )


def child_values(values, *, workspace):
    """Allowlisted app values plus the core-generated keys for one Compose invocation."""
    result = {key: values[key] for key in APP_KEYS}
    result["PARTFLOW_REPO_ROOT"] = str(workspace)
    result["PARTFLOW_DATABASE_URL"] = database_url(values["POSTGRES_USER"], values["POSTGRES_PASSWORD"],
                                                   values["POSTGRES_DB"])
    return result


CHILD_KEYS = APP_KEYS + GENERATED_KEYS


# ------------------------------------------------------------- frozen snapshot


@dataclasses.dataclass(frozen=True)
class FrozenAppConfig:
    """Immutable reference to the private snapshot one operation consumes."""

    operation_id: str
    directory: Path
    env_file: Path
    env_sha256: str
    source_sha256: str
    values: types.MappingProxyType

    def child_values(self, workspace):
        return child_values(self.values, workspace=workspace)


def _utc():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _write_private(path, data, mode):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex[:8])
    fd = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(str(temporary), mode)
        os.replace(str(temporary), str(path))
    except BaseException:
        try:
            os.unlink(str(temporary))
        except OSError:
            pass
        raise
    directory = os.open(str(path.parent), os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _read_nofollow(path):
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        chunks = []
        while True:
            block = os.read(fd, 1024 * 1024)
            if not block:
                break
            chunks.append(block)
        return b"".join(chunks)
    finally:
        os.close(fd)


def freeze_app_config(values, *, source_bytes, operation_id, operation_dir):
    """Render the approved values into ``<operation_dir>/app.env`` (0400) and record it.

    ``values`` must already be validated by the adapter. The rendered file is parsed
    back and compared literally before the reference is returned, so a value that
    would not survive the round-trip never becomes an approved snapshot.
    """
    operation_dir = Path(operation_dir)
    problems = unsupported_values(values)
    if problems:
        raise ConfigError("migration-issue: existing configuration values cannot be frozen literally: "
                          + "; ".join(f"{key}: {issue}" for key, issue in sorted(problems.items()))
                          + ". The value was not changed or regenerated; fix config/.env explicitly.")
    rendered = render_app_env(values)
    parsed = parse_app_env(rendered, label="snapshot")
    if parsed != {key: values[key] for key in APP_KEYS}:
        raise ConfigError("snapshot round-trip failed; nothing was frozen")
    env_path = operation_dir / SNAPSHOT_FILE
    if os.path.lexists(str(env_path)):
        raise ConfigError(f"{env_path} already exists; a snapshot is written once per operation")
    _write_private(env_path, rendered, 0o400)
    env_sha = hashlib.sha256(rendered).hexdigest()
    source_sha = hashlib.sha256(source_bytes).hexdigest()
    record = {
        "schema_version": SCHEMA_VERSION,
        "operation_id": operation_id,
        "created_at": _utc(),
        "env_file": SNAPSHOT_FILE,
        "env_file_sha256": env_sha,
        "env_file_bytes": len(rendered),
        "source_env_sha256": source_sha,
        "keys": list(APP_KEYS),
    }
    _write_private(operation_dir / SNAPSHOT_RECORD,
                   json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n", 0o400)
    return FrozenAppConfig(operation_id=operation_id, directory=operation_dir, env_file=env_path,
                           env_sha256=env_sha, source_sha256=source_sha,
                           values=types.MappingProxyType(dict(parsed)))


def verify_frozen(frozen):
    """Re-read the snapshot; its bytes, hash and parsed values must equal the frozen reference."""
    try:
        data = _read_nofollow(frozen.env_file)
    except OSError as exc:
        raise ConfigError(f"frozen snapshot unreadable: {frozen.env_file}: {exc.strerror or exc}") from exc
    if hashlib.sha256(data).hexdigest() != frozen.env_sha256:
        raise ConfigError(f"frozen snapshot changed on disk: {frozen.env_file}; the operation stops")
    if parse_app_env(data, label=str(frozen.env_file)) != dict(frozen.values):
        raise ConfigError(f"frozen snapshot no longer parses to the approved values: {frozen.env_file}")
    return frozen
