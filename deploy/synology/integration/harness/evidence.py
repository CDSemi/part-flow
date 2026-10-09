"""Evidence writer (SPEC 5.5, 6.3): bounded, redacted JSON/JSONL records under /pfa34/evidence/<run>/."""
import hashlib
import json
import os
from pathlib import Path
import re

from . import util

EXCERPT_LIMIT = 8192
LOG_LIMIT = 4 * 1024 * 1024
PASSWORD_LINE_RE = re.compile(r"(POSTGRES_PASSWORD=)([^\s'\"]+)")
URL_PASSWORD_RE = re.compile(r"(postgresql(?:\+\w+)?://[^:/@\s]+:)([^@\s]+)(@)")


def redaction(secret):
    return "<redacted:" + hashlib.sha256(secret.encode("utf-8")).hexdigest()[:8] + ">"


class Evidence:
    def __init__(self, base, run):
        self.base = Path(base) / run
        self.base.mkdir(parents=True, exist_ok=True)
        self.secrets = set()

    def add_secret(self, value):
        if value and len(value) >= 8:
            self.secrets.add(value)

    def redact(self, text):
        if text is None:
            return None
        if isinstance(text, bytes):
            text = text.decode("utf-8", "replace")
        for secret in sorted(self.secrets, key=len, reverse=True):
            if secret in text:
                text = text.replace(secret, redaction(secret))
        text = PASSWORD_LINE_RE.sub(lambda match: match.group(1) + (
            match.group(2) if match.group(2).startswith("<redacted:") else redaction(match.group(2))), text)
        text = URL_PASSWORD_RE.sub(lambda match: match.group(1) + (
            match.group(2) if match.group(2).startswith("<redacted:") else redaction(match.group(2)))
            + match.group(3), text)
        return text

    def excerpt(self, text, limit=EXCERPT_LIMIT):
        text = self.redact(text or "")
        if len(text) <= limit:
            return text
        half = limit // 2
        return text[:half] + f"\n[... {len(text) - limit} characters omitted ...]\n" + text[-half:]

    def path(self, relative):
        path = self.base / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def write_json(self, relative, value):
        text = self.redact(json.dumps(value, indent=1, sort_keys=True, default=str)) + "\n"
        util.write_private(self.path(relative), text, mode=0o644)
        return str(self.path(relative))

    def append_jsonl(self, relative, value):
        line = self.redact(json.dumps(value, sort_keys=True, default=str)) + "\n"
        path = self.path(relative)
        with open(str(path), "a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())

    def append_log(self, relative, text):
        path = self.path(relative)
        if path.exists() and path.stat().st_size > LOG_LIMIT:
            return
        with open(str(path), "a", encoding="utf-8") as handle:
            handle.write(self.redact(text))

    def read_json(self, relative):
        path = self.base / relative
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
