"""P16-S2: static checks of the production artifacts (compose.production.yaml, .env.production.example, both
Dockerfiles and the web tier configuration in frontend/nginx/).

Case mapping (P16-S2 SPEC section 6.2):
  Compose model (resolved by `docker compose config`)  -> ComposeModel (ST-1..ST-12, ST-14, ST-15)
  Dockerfiles and the development default              -> Dockerfiles (ST-13)
  nginx configuration (parsed as text)                 -> WebTier (NX-1..NX-12)

Run from anywhere on a host with the docker CLI (Compose v2) and git:
  python -B -m unittest discover -s deploy/production/tests -p 'test*.py'
`docker compose config` only resolves the model: no image is built or pulled, no container, network or volume is
created, and no daemon state is read or changed. A missing docker CLI fails the run (never a skip). The stack smoke
(stack_smoke.py) is a separate, manual integration run and is not collected here.
"""
import json
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[3]
COMPOSE_FILE = REPO / "compose.production.yaml"
ENV_EXAMPLE = REPO / ".env.production.example"
NGINX_DIR = REPO / "frontend" / "nginx"
TEMPLATE = NGINX_DIR / "templates" / "default.conf.template"
PROXY_API = NGINX_DIR / "partflow" / "proxy-api.conf"
STATIC_HEADERS = NGINX_DIR / "partflow" / "static-headers.conf"
STATIC_HEADERS_INCLUDE = "/etc/nginx/partflow/static-headers.conf"
PROXY_API_INCLUDE = "/etc/nginx/partflow/proxy-api.conf"

DOCKER_REQUIRED = "docker compose is required for the production artifact tests"
RELEASE = "static-test"
LONG_RUNNING = {"db", "backend", "web"}
PARTFLOW_IMAGES = ("backend", "web", "migrate")
# Variables that must stop `config` when empty (ST-1).
REQUIRED_VARIABLES = (
    "PARTFLOW_RELEASE",
    "PARTFLOW_SECRETS_DIR",
    "PARTFLOW_SITE_TIMEZONE",
    "PARTFLOW_HTTP_PORT",
    "POSTGRES_USER",
    "POSTGRES_DB",
)
# Site-specific values (and the optional trusted-proxy override) the example leaves empty (ST-14).
EMPTY_IN_EXAMPLE = ("PARTFLOW_RELEASE", "PARTFLOW_SECRETS_DIR", "PARTFLOW_SITE_TIMEZONE", "PARTFLOW_TRUSTED_PROXY")
SECRET_LIKE = ("PASSWORD", "SECRET", "TOKEN", "DSN")
# A URL that carries a credential (user:password@).
CREDENTIAL_URL = re.compile(r"://[^/\s]*:[^/\s]*@")
# Shell variables that would override the generated env file (Compose precedence) or change the model.
_HOST_VARIABLE_PREFIXES = ("PARTFLOW_", "POSTGRES_", "COMPOSE_")

# The exact web tier contract (SPEC section 4.5.4 / 4.6).
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
    "connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'self'"
)
STATIC_HEADER_LINES = [
    ("add_header", ["X-Content-Type-Options", "nosniff", "always"]),
    ("add_header", ["Referrer-Policy", "same-origin", "always"]),
    ("add_header", ["Content-Security-Policy", CSP, "always"]),
]
PROXY_API_LINES = [
    ("proxy_pass", ["$partflow_backend"]),
    ("proxy_set_header", ["Host", "$host"]),
    ("proxy_set_header", ["X-Forwarded-For", "$remote_addr"]),
    ("proxy_set_header", ["X-Forwarded-Proto", "$partflow_forwarded_proto"]),
]
PROXY_BODIES = {
    "@partflow_too_large": (
        "413",
        '{"detail":"This request is too large for PartFlow. Nothing was changed.","request_too_large":true}',
    ),
    "@partflow_rate_limited": (
        "429",
        '{"detail":"Too many attempts from this computer. Wait a minute, then try again. Nothing was changed.",'
        '"rate_limited":true}',
    ),
    "@partflow_bad_gateway": (
        "502",
        '{"detail":"The PartFlow server did not complete the request. If you were saving a change, check whether'
        ' it was saved before repeating it.","server_unavailable":true}',
    ),
    "@partflow_gateway_timeout": (
        "504",
        '{"detail":"The PartFlow server did not answer in time.","server_unavailable":true}',
    ),
}
# The backend's raw-body upload/import routes (backend tests/test_web_tier_contract.py B-W1), as nginx sees them.
UPLOAD_ROUTES = (
    "/api/workers/17/avatar",
    "/api/users/17/avatar",
    "/api/part-numbers/image",
    "/api/work-orders/import/preview",
    "/api/work-orders/import",
)
UPLOAD_LOCATIONS = {
    ("~", "^/api/(workers|users)/[^/]+/avatar$"),
    ("=", "/api/part-numbers/image"),
    ("=", "/api/work-orders/import/preview"),
    ("=", "/api/work-orders/import"),
}
SIGN_IN_ROUTE = "/api/session"
PASSWORD_ROUTES = ("/api/session/password", "/api/setup/administrator", "/api/users/17/password")
PASSWORD_LOCATIONS = {
    ("=", "/api/session/password"),
    ("=", "/api/setup/administrator"),
    ("~", "^/api/users/[^/]+/password$"),
}
# Ordinary API routes that must keep the server defaults (1 MiB, 60 s, no rate limit).
ORDINARY_ROUTES = (
    "/api/session/theme-preference",
    "/api/users/17",
    "/api/workers/17",
    "/api/part-numbers",
    "/api/work-orders",
    "/api/work-orders/import/template.csv",
    "/api/scan-stations/3/device-activations",
)


# ---------------------------------------------------------------------------
# Helpers: Compose
# ---------------------------------------------------------------------------


def _compose_environment():
    return {k: v for k, v in os.environ.items() if not k.upper().startswith(_HOST_VARIABLE_PREFIXES)}


def _example_lines():
    return ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()


def example_values():
    values = {}
    for line in _example_lines():
        match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)=(.*)", line)
        if match:
            values[match.group(1)] = match.group(2)
    return values


def write_env(path, overrides):
    """The example with `overrides` applied (keys not in the example are appended)."""
    lines, seen = [], set()
    for line in _example_lines():
        match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)=(.*)", line)
        if match and match.group(1) in overrides:
            key = match.group(1)
            lines.append(f"{key}={overrides[key]}")
            seen.add(key)
        else:
            lines.append(line)
    lines.extend(f"{key}={value}" for key, value in overrides.items() if key not in seen)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_compose_config(env_file):
    """`docker compose config --format json` of the production file, every profile active."""
    return subprocess.run(
        [
            "docker", "compose", "-f", str(COMPOSE_FILE), "--env-file", str(env_file),
            "--profile", "ops", "config", "--format", "json",
        ],
        cwd=REPO, env=_compose_environment(), capture_output=True, text=True, encoding="utf-8", timeout=120,
    )


def docker_compose_available():
    if shutil.which("docker") is None:
        return False
    try:
        result = subprocess.run(
            ["docker", "compose", "version"], capture_output=True, text=True, timeout=60, env=_compose_environment()
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def duration_seconds(value):
    """Compose duration ("3m20s", "1m0s", "200s") in seconds."""
    units = {"h": 3600, "m": 60, "s": 1, "ms": 0.001, "us": 0.000001}
    parts = re.findall(r"(\d+(?:\.\d+)?)(h|ms|us|m|s)", str(value))
    if not parts or "".join(n + u for n, u in parts) != str(value):
        raise ValueError(f"not a Compose duration: {value!r}")
    return sum(float(number) * units[unit] for number, unit in parts)


def same_path(left, right):
    if os.name == "nt":
        return PureWindowsPath(left) == PureWindowsPath(right)
    return Path(left) == Path(right)


# ---------------------------------------------------------------------------
# Helpers: nginx configuration as text
# ---------------------------------------------------------------------------


def nginx_tokens(text):
    """Words, quoted strings and the punctuation `{`, `}`, `;`. `${NAME}` stays inside its word."""
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
        elif c == "#":
            while i < n and text[i] != "\n":
                i += 1
        elif c in "{};":
            yield c, False
            i += 1
        elif c in "\"'":
            j, buf = i + 1, []
            while j < n and text[j] != c:
                if text[j] == "\\" and j + 1 < n:
                    buf.append(text[j : j + 2])
                    j += 2
                    continue
                buf.append(text[j])
                j += 1
            if j >= n:
                raise ValueError("unterminated quoted string")
            yield "".join(buf), True
            i = j + 1
        else:
            j, buf = i, []
            while j < n and not text[j].isspace() and text[j] not in ";{}":
                if text[j] == "$" and j + 1 < n and text[j + 1] == "{":
                    end = text.index("}", j)
                    buf.append(text[j : end + 1])
                    j = end + 1
                    continue
                buf.append(text[j])
                j += 1
            yield "".join(buf), False
            i = j


class Directive:
    def __init__(self, name, args, block):
        self.name, self.args, self.block = name, args, block

    def __repr__(self):
        return f"Directive({self.name!r}, {self.args!r}, block={self.block is not None})"

    def children(self, name):
        return [d for d in (self.block or []) if d.name == name]


def parse_nginx(text):
    tokens = list(nginx_tokens(text))
    position = 0

    def parse_block(closing):
        nonlocal position
        directives, words = [], []
        while position < len(tokens):
            value, quoted = tokens[position]
            position += 1
            if not quoted and value == ";":
                if not words:
                    raise ValueError("empty directive")
                directives.append(Directive(words[0], words[1:], None))
                words = []
            elif not quoted and value == "{":
                if not words:
                    raise ValueError("block without a name")
                directives.append(Directive(words[0], words[1:], parse_block(True)))
                words = []
            elif not quoted and value == "}":
                if not closing or words:
                    raise ValueError("unexpected }")
                return directives
            else:
                words.append(value)
        if closing or words:
            raise ValueError("unexpected end of configuration")
        return directives

    return parse_block(False)


def walk(directives):
    for directive in directives:
        yield directive
        if directive.block is not None:
            yield from walk(directive.block)


def strip_comments(text):
    return "\n".join(re.sub(r"(^|\s)#.*$", "", line) for line in text.splitlines())


def nginx_config_files():
    return sorted(p for p in NGINX_DIR.rglob("*") if p.is_file() and p.suffix in (".template", ".conf"))


def all_nginx_files():
    return sorted(p for p in NGINX_DIR.rglob("*") if p.is_file())


def select_location(locations, path):
    """The location nginx selects for `path` (exact, then longest prefix, then the first matching regex)."""
    exact = [loc for loc in locations if loc.args[0] == "=" and loc.args[1] == path]
    if exact:
        return exact[0]
    prefixes = [loc for loc in locations if len(loc.args) == 1 and path.startswith(loc.args[0])]
    prefixes += [loc for loc in locations if loc.args[0] == "^~" and path.startswith(loc.args[1])]
    best = max(prefixes, key=lambda loc: len(loc.args[-1]), default=None)
    if best is not None and best.args[0] == "^~":
        return best
    for loc in locations:
        if loc.args[0] in ("~", "~*"):
            flags = re.IGNORECASE if loc.args[0] == "~*" else 0
            if re.search(loc.args[1], path, flags):
                return loc
    return best


# ---------------------------------------------------------------------------
# ST-1..ST-12, ST-14, ST-15
# ---------------------------------------------------------------------------


class ComposeModel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not docker_compose_available():
            raise AssertionError(DOCKER_REQUIRED)
        cls._tmp = tempfile.TemporaryDirectory(prefix="pf-s2-static-")
        root = Path(cls._tmp.name)
        cls.secrets_dir = root / "secrets"
        cls.secrets_dir.mkdir()
        (cls.secrets_dir / "postgres_password").write_text("static-test-password\n", encoding="utf-8")
        cls.base_overrides = {
            "PARTFLOW_RELEASE": RELEASE,
            "PARTFLOW_SECRETS_DIR": cls.secrets_dir.as_posix(),
            "PARTFLOW_SITE_TIMEZONE": "UTC",
        }
        cls.env_file = root / "env"
        write_env(cls.env_file, cls.base_overrides)
        result = run_compose_config(cls.env_file)
        cls.config_result = result
        cls.model = json.loads(result.stdout) if result.returncode == 0 else None
        cls.root = root

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        self.assertEqual(self.config_result.returncode, 0, self.config_result.stderr)
        self.services = self.model["services"]

    def variant(self, name, **overrides):
        path = self.root / f"env-{name}"
        write_env(path, {**self.base_overrides, **overrides})
        return run_compose_config(path)

    def ops_services(self):
        return {name for name, service in self.services.items() if service.get("profiles")}

    # ST-1
    def test_st1_required_variables(self):
        for variable in REQUIRED_VARIABLES:
            with self.subTest(variable=variable):
                result = self.variant(variable.lower(), **{variable: ""})
                self.assertNotEqual(result.returncode, 0, f"config accepted an empty {variable}")
                self.assertIn(variable, result.stderr)

    # ST-2
    def test_st2_service_set(self):
        self.assertEqual({n for n, s in self.services.items() if not s.get("profiles")}, LONG_RUNNING)
        for name in self.ops_services():
            self.assertEqual(self.services[name]["profiles"], ["ops"], name)
        self.assertEqual(self.ops_services(), {"migrate"})

    # ST-3
    def test_st3_only_web_published_on_loopback(self):
        for name, service in self.services.items():
            if name != "web":
                self.assertFalse(service.get("ports"), f"{name} publishes a port")
        ports = self.services["web"]["ports"]
        self.assertEqual(len(ports), 1)
        self.assertEqual(ports[0]["host_ip"], "127.0.0.1")
        self.assertEqual(ports[0]["target"], 80)
        self.assertEqual(str(ports[0]["published"]), example_values()["PARTFLOW_HTTP_PORT"])

    # ST-4
    def test_st4_volumes(self):
        for name, service in self.services.items():
            for volume in service.get("volumes") or []:
                self.assertNotEqual(volume.get("type"), "bind", f"{name} has a host-path mount")
        for name in ("backend", "web", "migrate"):
            self.assertFalse(self.services[name].get("volumes"), f"{name} has a volume")
        volumes = self.services["db"]["volumes"]
        self.assertEqual(
            [(v["type"], v["source"], v["target"]) for v in volumes],
            [("volume", "postgres_data", "/var/lib/postgresql/data")],
        )
        self.assertEqual(set(self.model["volumes"]), {"postgres_data"})

    # ST-5
    def test_st5_restart_and_grace(self):
        for name in LONG_RUNNING:
            self.assertEqual(self.services[name]["restart"], "unless-stopped", name)
        for name in self.ops_services():
            self.assertEqual(self.services[name]["restart"], "no", name)
        for name, service in self.services.items():
            self.assertNotIn("on-failure", str(service.get("restart")), name)
        timeouts = [
            duration_seconds(d.args[0])
            for path in nginx_config_files()
            for d in walk(parse_nginx(path.read_text(encoding="utf-8")))
            if d.name == "proxy_read_timeout"
        ]
        self.assertTrue(timeouts, "no proxy_read_timeout in frontend/nginx")
        longest = max(timeouts)
        self.assertEqual(longest, 180)
        for name in ("backend", "web"):
            self.assertGreater(duration_seconds(self.services[name]["stop_grace_period"]), longest, name)

    # ST-6
    def test_st6_health_checks(self):
        for name in LONG_RUNNING:
            check = self.services[name].get("healthcheck")
            self.assertTrue(check and check.get("test"), f"{name} has no health check")
            self.assertFalse(check.get("disable"), name)
        backend_test = " ".join(self.services["backend"]["healthcheck"]["test"])
        self.assertNotIn("/api/health", backend_test)
        self.assertNotIn("http", backend_test)
        for name in self.ops_services():
            self.assertIs(self.services[name]["healthcheck"].get("disable"), True, name)

    # ST-7
    def test_st7_resource_limits(self):
        for name, service in self.services.items():
            limits = service.get("deploy", {}).get("resources", {}).get("limits", {})
            self.assertTrue(limits.get("memory"), f"{name} has no memory limit")
            self.assertTrue(limits.get("cpus"), f"{name} has no cpus limit")

    # ST-8
    def test_st8_logging(self):
        for name, service in self.services.items():
            logging = service.get("logging") or {}
            self.assertEqual(logging.get("driver"), "json-file", name)
            self.assertTrue(logging.get("options", {}).get("max-size"), name)
            self.assertTrue(logging.get("options", {}).get("max-file"), name)

    # ST-9
    def test_st9_secret(self):
        secrets = self.model["secrets"]
        self.assertEqual(set(secrets), {"postgres_password"})
        self.assertTrue(same_path(secrets["postgres_password"]["file"], self.secrets_dir / "postgres_password"))
        users = {name for name, s in self.services.items() if any(x["source"] == "postgres_password" for x in s.get("secrets") or [])}
        self.assertEqual(users, {"db", "backend", "migrate"})
        self.assertFalse(self.services["web"].get("secrets"))

    # ST-10
    def test_st10_environment(self):
        for name, service in self.services.items():
            for key, value in (service.get("environment") or {}).items():
                with self.subTest(service=name, key=key):
                    if any(word in key.upper() for word in SECRET_LIKE):
                        self.assertTrue(key.endswith("_FILE"), f"{name}.{key} looks like a secret value")
                    self.assertNotEqual(key, "DATABASE_URL")
                    self.assertIsNone(CREDENTIAL_URL.search(str(value or "")), f"{name}.{key} carries a credential")
        backend = self.services["backend"]["environment"]
        self.assertEqual(backend["SESSION_COOKIE_SECURE"], "true")
        self.assertEqual(backend["WEB_CONCURRENCY"], "2")
        self.assertEqual(backend["FORWARDED_ALLOW_IPS"], example_values()["PARTFLOW_EDGE_SUBNET"])
        self.assertEqual(self.services["web"]["environment"], {"PARTFLOW_TRUSTED_PROXY": ""})
        for name in ("backend", "migrate"):
            self.assertEqual(self.services[name]["environment"]["DATABASE_PASSWORD_FILE"], "/run/secrets/postgres_password")
        self.assertEqual(self.services["db"]["environment"]["POSTGRES_PASSWORD_FILE"], "/run/secrets/postgres_password")
        override = self.variant("trusted-proxy", PARTFLOW_TRUSTED_PROXY="192.0.2.10")
        self.assertEqual(override.returncode, 0, override.stderr)
        web = json.loads(override.stdout)["services"]["web"]
        self.assertEqual(web["environment"], {"PARTFLOW_TRUSTED_PROXY": "192.0.2.10"})

    # ST-11
    def test_st11_networks(self):
        networks = self.model["networks"]
        self.assertIs(networks["internal"].get("internal"), True)
        subnets = [c["subnet"] for c in networks["edge"]["ipam"]["config"]]
        self.assertEqual(subnets, [example_values()["PARTFLOW_EDGE_SUBNET"]])
        attached = {name: set(service.get("networks") or {}) for name, service in self.services.items()}
        self.assertEqual(attached["db"], {"internal"})
        self.assertEqual(attached["migrate"], {"internal"})
        self.assertEqual(attached["backend"], {"internal", "edge"})
        self.assertEqual(attached["web"], {"edge"})
        self.assertFalse(self.services["web"].get("depends_on"), "web must start without backend")

    # ST-12
    def test_st12_images(self):
        self.assertRegex(self.services["db"]["image"], r"^postgres:16\.\d+$")
        self.assertEqual(self.services["backend"]["image"], f"partflow/backend:{RELEASE}")
        self.assertEqual(self.services["web"]["image"], f"partflow/web:{RELEASE}")
        for name in ("backend", "web"):
            self.assertEqual(self.services[name]["build"]["target"], "production", name)
        self.assertEqual(self.services["migrate"]["image"], self.services["backend"]["image"])
        self.assertNotIn("build", self.services["migrate"])
        for name in PARTFLOW_IMAGES:
            self.assertEqual(self.services[name].get("pull_policy"), "never", name)

    # ST-14
    def test_st14_inventory(self):
        text = "\n".join(line for line in COMPOSE_FILE.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("#"))
        text = text.replace("$$", "")
        referenced = set(re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)", text))
        values = example_values()
        self.assertEqual(referenced, set(values))
        for key in EMPTY_IN_EXAMPLE:
            self.assertEqual(values[key], "", key)
        for key, value in values.items():
            # A *_DIR / *_FILE key names where secrets live, never a secret value.
            if not key.endswith(("_DIR", "_FILE")):
                self.assertFalse(any(word in key for word in SECRET_LIKE), f"secret-like key {key}")
            self.assertNotIn("://", value, key)

    # ST-15
    def test_st15_git_ignore(self):
        def ignored(name):
            return subprocess.run(["git", "check-ignore", "-q", name], cwd=REPO, capture_output=True).returncode

        self.assertEqual(ignored(".env.production.example"), 1, ".env.production.example is ignored")
        self.assertEqual(ignored(".env.production"), 0, ".env.production is not ignored")


# ---------------------------------------------------------------------------
# ST-13
# ---------------------------------------------------------------------------


def dockerfile_stages(path):
    """[(base, stage name, [instructions])] with line continuations joined and comments removed."""
    logical, buffer = [], ""
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not buffer and (not line or line.startswith("#")):
            continue
        if line.endswith("\\"):
            buffer += line[:-1] + " "
            continue
        logical.append(buffer + line)
        buffer = ""
    stages = []
    for line in logical:
        match = re.fullmatch(r"FROM\s+(\S+)(?:\s+AS\s+(\S+))?", line, re.IGNORECASE)
        if match:
            stages.append((match.group(1), match.group(2), []))
        else:
            stages[-1][2].append(line)
    return stages


class Dockerfiles(unittest.TestCase):
    def stage(self, stages, name):
        found = [s for s in stages if s[1] == name]
        self.assertEqual(len(found), 1, f"stage {name}")
        return found[0]

    def test_st13_backend(self):
        stages = dockerfile_stages(REPO / "backend" / "Dockerfile")
        self.assertEqual(stages[-1][1], "development")
        base, _, body = self.stage(stages, "production")
        self.assertEqual(base, "python:3.12-slim")
        self.assertIn("USER 10001:10001", body)
        self.assertNotIn("COPY . .", body)
        joined = "\n".join(body)
        self.assertNotIn("--reload", joined)
        cmd = [line for line in body if line.startswith("CMD")]
        self.assertEqual(len(cmd), 1)
        self.assertIn("--no-access-log", cmd[0])
        self.assertNotIn("alembic", cmd[0])
        # The production venv comes from a stage that installs the locked runtime dependencies only.
        source = re.search(r"COPY --from=(\S+) /app/\.venv /app/\.venv", joined)
        self.assertIsNotNone(source, "production does not copy a locked venv")
        _, _, deps = self.stage(stages, source.group(1))
        self.assertIn("RUN uv sync --frozen --no-dev", deps)

    def test_st13_frontend(self):
        stages = dockerfile_stages(REPO / "frontend" / "Dockerfile")
        self.assertEqual(stages[-1][1], "development")
        base, _, body = self.stage(stages, "production")
        self.assertRegex(base, r"^nginx:\d+\.\d+\.\d+-alpine$")
        self.assertIn("ENV NGINX_ENVSUBST_FILTER=^PARTFLOW_", body)

    def test_st13_development_default_unchanged(self):
        for line in (REPO / "compose.yaml").read_text(encoding="utf-8").splitlines():
            self.assertIsNone(re.match(r"\s*target\s*:", line), "compose.yaml selects a build target")


# ---------------------------------------------------------------------------
# NX-1..NX-12
# ---------------------------------------------------------------------------


class WebTier(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template_text = TEMPLATE.read_text(encoding="utf-8")
        cls.http = parse_nginx(cls.template_text)
        servers = [d for d in cls.http if d.name == "server"]
        assert len(servers) == 1, "exactly one server block"
        cls.server = servers[0]
        cls.locations = cls.server.children("location")
        cls.proxy_api = parse_nginx(PROXY_API.read_text(encoding="utf-8"))
        cls.static_headers = parse_nginx(STATIC_HEADERS.read_text(encoding="utf-8"))
        cls.everything = [d for p in nginx_config_files() for d in walk(parse_nginx(p.read_text(encoding="utf-8")))]

    def location(self, *args):
        found = [loc for loc in self.locations if tuple(loc.args) == args]
        self.assertEqual(len(found), 1, f"location {args}")
        return found[0]

    def value(self, block, name):
        found = [d for d in block if d.name == name]
        self.assertEqual(len(found), 1, name)
        return found[0].args

    def http_level(self, name):
        return [d for d in self.http if d.name == name]

    def includes(self, location):
        return [d.args[0] for d in location.block if d.name == "include"]

    # NX-1 (and the "never present" list of SPEC section 4.5.4)
    def test_nx1_no_cors_and_forbidden_constructs(self):
        for path in all_nginx_files():
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("Access-Control-", text, path)
            self.assertNotIn("$http_cookie", text.lower(), path)
            self.assertNotIn("$http_x_partflow", text.lower(), path)
        for directive in self.everything:
            self.assertNotEqual(directive.name, "upstream")
            self.assertFalse(directive.name.startswith("ssl_"), directive)
            self.assertNotIn("keepalive", directive.name, directive)
            if directive.name == "listen":
                self.assertNotIn("443", " ".join(directive.args))
            if directive.name == "proxy_intercept_errors":
                self.assertNotEqual(directive.args, ["on"])
            if directive.name == "proxy_next_upstream":
                self.assertEqual(directive.args, ["off"])

    # NX-2
    def test_nx2_body_limits_and_import_timeout(self):
        self.assertEqual(self.value(self.server.block, "client_max_body_size"), ["1m"])
        bigger = {tuple(loc.args) for loc in self.locations if loc.children("client_max_body_size")}
        self.assertEqual(bigger, UPLOAD_LOCATIONS)
        for loc in self.locations:
            for d in loc.children("client_max_body_size"):
                self.assertEqual(d.args, ["4m"], loc)
        for path in UPLOAD_ROUTES:
            selected = select_location(self.locations, path)
            self.assertIn(tuple(selected.args), UPLOAD_LOCATIONS, path)
        for path in ORDINARY_ROUTES + PASSWORD_ROUTES + (SIGN_IN_ROUTE,):
            self.assertFalse(select_location(self.locations, path).children("client_max_body_size"), path)
        self.assertEqual(self.value(self.server.block, "proxy_read_timeout"), ["60s"])
        self.assertEqual(self.value(self.server.block, "proxy_send_timeout"), ["60s"])
        for name in ("proxy_read_timeout", "proxy_send_timeout"):
            owners = {tuple(loc.args) for loc in self.locations if loc.children(name)}
            self.assertEqual(owners, {("=", "/api/work-orders/import")}, name)
            self.assertEqual(self.value(self.location("=", "/api/work-orders/import").block, name), ["180s"])

    # NX-3
    def test_nx3_rate_limits(self):
        zones = {tuple(d.args) for d in self.http_level("limit_req_zone")}
        self.assertEqual(
            zones,
            {
                ("$partflow_sign_in_client", "zone=partflow_sign_in:1m", "rate=10r/m"),
                ("$partflow_password_client", "zone=partflow_password:1m", "rate=5r/m"),
            },
        )
        self.assertEqual([d.args for d in self.http_level("limit_req_status")], [["429"]])
        limited = {tuple(loc.args): loc.children("limit_req") for loc in self.locations if loc.children("limit_req")}
        self.assertEqual(set(limited), {("=", SIGN_IN_ROUTE)} | PASSWORD_LOCATIONS)
        self.assertEqual([d.args for d in limited[("=", SIGN_IN_ROUTE)]], [["zone=partflow_sign_in", "burst=5", "nodelay"]])
        for key in PASSWORD_LOCATIONS:
            self.assertEqual([d.args for d in limited[key]], [["zone=partflow_password", "burst=4", "nodelay"]], key)
        self.assertEqual(tuple(select_location(self.locations, SIGN_IN_ROUTE).args), ("=", SIGN_IN_ROUTE))
        for path in PASSWORD_ROUTES:
            self.assertIn(tuple(select_location(self.locations, path).args), PASSWORD_LOCATIONS, path)
        for path in ORDINARY_ROUTES + UPLOAD_ROUTES:
            self.assertFalse(select_location(self.locations, path).children("limit_req"), path)
        maps = {tuple(d.args): {e.name: e.args for e in d.block} for d in self.http_level("map")}
        self.assertEqual(
            maps[("$request_method", "$partflow_sign_in_client")], {"POST": ["$binary_remote_addr"], "default": [""]}
        )
        self.assertEqual(
            maps[("$request_method", "$partflow_password_client")],
            {"POST": ["$binary_remote_addr"], "PUT": ["$binary_remote_addr"], "default": [""]},
        )

    # NX-4
    def test_nx4_trusted_hop(self):
        real_ip = [d for d in self.everything if d.name == "set_real_ip_from"]
        self.assertEqual([d.args for d in real_ip], [["${PARTFLOW_TRUSTED_PROXY}"]])
        self.assertEqual(self.value(self.server.block, "real_ip_header"), ["X-Forwarded-For"])
        self.assertEqual(self.value(self.server.block, "real_ip_recursive"), ["off"])
        occurrences = sum(
            strip_comments(p.read_text(encoding="utf-8")).count("${PARTFLOW_TRUSTED_PROXY}") for p in nginx_config_files()
        )
        self.assertEqual(occurrences, 2)

    # NX-5
    def test_nx5_upstream_resolution(self):
        self.assertEqual(self.value(self.server.block, "resolver")[0], "127.0.0.11")
        self.assertEqual(self.value(self.server.block, "set"), ["$partflow_backend", "http://backend:8000"])
        passes = [d.args for d in self.everything if d.name == "proxy_pass"]
        self.assertTrue(passes)
        self.assertTrue(all(args == ["$partflow_backend"] for args in passes), passes)
        self.assertEqual(self.value(self.server.block, "proxy_next_upstream"), ["off"])
        self.assertFalse([d for d in self.everything if d.name == "proxy_intercept_errors" and d.args == ["on"]])

    # NX-6
    def test_nx6_access_log_format(self):
        formats = [d for d in self.http_level("log_format") if d.args[0] == "partflow"]
        self.assertEqual(len(formats), 1)
        text = "".join(formats[0].args[1:]).lower()
        for forbidden in ("$http_cookie", "$http_x_partflow", "$request_uri", "$args", "$query_string", "$request_body"):
            self.assertNotIn(forbidden, text)
        self.assertIsNone(re.search(r"\$request(?![_a-z])", text), "bare $request logs the query string")
        self.assertEqual(self.value(self.server.block, "access_log"), ["/var/log/nginx/access.log", "partflow"])

    # NX-7
    def test_nx7_static_caching(self):
        assets = self.location("/assets/")
        self.assertEqual(self.value(assets.block, "try_files"), ["$uri", "=404"])
        self.assertIn(["Cache-Control", "public, max-age=31536000, immutable", "always"], [d.args for d in assets.children("add_header")])
        index = self.location("=", "/index.html")
        self.assertIn(["Cache-Control", "no-cache", "always"], [d.args for d in index.children("add_header")])
        self.assertEqual(self.value(self.location("/").block, "try_files"), ["$uri", "/index.html"])

    # NX-8
    def test_nx8_proxy_generated_bodies(self):
        pages = {tuple(d.args) for d in self.server.children("error_page")}
        self.assertEqual(
            pages,
            {
                ("413", "=", "@partflow_too_large"),
                ("429", "=", "@partflow_rate_limited"),
                ("502", "=", "@partflow_bad_gateway"),
                ("504", "=", "@partflow_gateway_timeout"),
            },
        )
        named = {loc.args[0]: loc for loc in self.locations if loc.args[0].startswith("@")}
        self.assertEqual(set(named), set(PROXY_BODIES))
        for name, (status, body) in PROXY_BODIES.items():
            self.assertEqual(self.value(named[name].block, "return"), [status, body], name)
            self.assertEqual(self.value(named[name].block, "default_type"), ["application/json"], name)
            json.loads(body)
            if status != "429":
                self.assertNotIn("try again", body.lower(), name)
        self.assertIn(["Retry-After", "60", "always"], [d.args for d in named["@partflow_rate_limited"].children("add_header")])

    # NX-9
    def test_nx9_header_inheritance(self):
        self.assertIn(STATIC_HEADERS_INCLUDE, [d.args[0] for d in self.server.children("include")])
        for loc in self.locations:
            if loc.children("add_header"):
                self.assertIn(STATIC_HEADERS_INCLUDE, self.includes(loc), loc)

    # NX-10
    def test_nx10_proxy_api(self):
        self.assertEqual([(d.name, d.args) for d in self.proxy_api], PROXY_API_LINES)
        api = [loc for loc in self.locations if loc.args[-1].startswith(("/api", "^/api"))]
        self.assertTrue(api)
        for loc in api:
            self.assertIn(PROXY_API_INCLUDE, self.includes(loc), loc)
        for loc in self.locations:
            if loc not in api:
                self.assertNotIn(PROXY_API_INCLUDE, self.includes(loc), loc)

    # NX-11
    def test_nx11_static_headers_and_csp(self):
        self.assertEqual([(d.name, d.args) for d in self.static_headers], STATIC_HEADER_LINES)
        directives = dict(part.strip().split(" ", 1) for part in CSP.split(";"))
        self.assertNotIn("'unsafe-eval'", CSP)
        self.assertNotIn("'unsafe-inline'", directives["script-src"])

    # NX-12
    def test_nx12_forwarded_proto_gate(self):
        readers = [line for line in strip_comments(self.template_text).splitlines() if "$http_x_forwarded_proto" in line]
        self.assertEqual(len(readers), 1, readers)
        maps = [d for d in self.http_level("map") if d.args == ["$partflow_from_trusted_proxy$http_x_forwarded_proto", "$partflow_forwarded_proto"]]
        self.assertEqual(len(maps), 1)
        self.assertEqual([(e.name, e.args) for e in maps[0].block], [("1https", ["https"]), ("default", ["$scheme"])])
        for path in nginx_config_files():
            if path != TEMPLATE:
                self.assertNotIn("$http_x_forwarded_proto", path.read_text(encoding="utf-8").lower(), path)
        geos = [d for d in self.http_level("geo") if d.args == ["$realip_remote_addr", "$partflow_from_trusted_proxy"]]
        self.assertEqual(len(geos), 1)
        self.assertEqual(sorted((e.name, e.args) for e in geos[0].block), [("${PARTFLOW_TRUSTED_PROXY}", ["1"]), ("default", ["0"])])
        self.assertEqual(len(self.http_level("geo")), 1)


if __name__ == "__main__":
    unittest.main()
