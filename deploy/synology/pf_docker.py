"""Docker scope rules of the Deployment Admin (PF-A1.3): daemon binding, Compose envelope,
exact resource inventory and the frozen, closed deletion plan.

Pure functions only: no process, no file, no environment access. The controller supplies
observations (``docker info``, ``compose config --format json`` models, whitelisted inspect
fields) and acts on the results. Every rule is an allowlist and fails closed: anything not
recognised is a refusal with a JSON path or a resource key, never a guess. Values are never
rendered into messages (only keys and paths).

Python standard library only, Python 3.9 language baseline.
"""
import dataclasses
import datetime as dt
import hashlib
import json
import re
import types

INSTANCE_LABEL = "io.deploy-admin.instance-id"
COMPOSE_PROJECT_LABEL = "com.docker.compose.project"
COMPOSE_SERVICE_LABEL = "com.docker.compose.service"
COMPOSE_ONEOFF_LABEL = "com.docker.compose.oneoff"
COMPOSE_CONTAINER_NUMBER_LABEL = "com.docker.compose.container-number"
COMPOSE_CONFIG_HASH_LABEL = "com.docker.compose.config-hash"
COMPOSE_VOLUME_LABEL = "com.docker.compose.volume"
COMPOSE_NETWORK_LABEL = "com.docker.compose.network"
SERVICES = ("db", "backend", "frontend")
BUILT_SERVICES = ("backend", "frontend")
ENVELOPE_VERBS = frozenset({"up", "run", "build", "create", "start", "restart"})
DELETION_ORDER = ("container", "network", "volume", "image")
DAEMON_PROBE_ARGV = ("docker", "info", "--format", "{{json .}}")
RENDER_LIMIT = 4 * 1024 * 1024
PLAN_SCHEMA_VERSION = 1
PLAN_KINDS = ("purge", "abort-deploy")
IMAGE_RE = re.compile(r"[a-z0-9][a-z0-9._/-]*:[a-zA-Z0-9_.-]+\Z")

# Go templates for `docker <kind> inspect --format`: one JSON object per line with only the
# fields the inventory needs. A container's Config.Env is never requested (foreign secrets).
CONTAINER_FIELDS = (
    '{"id":{{json .Id}},"name":{{json .Name}},"labels":{{json .Config.Labels}},"image":{{json .Image}},'
    '"config_image":{{json .Config.Image}},"created":{{json .Created}},"status":{{json .State.Status}},'
    '"mounts":{{json .Mounts}},"networks":{{json .NetworkSettings.Networks}}}'
)
VOLUME_FIELDS = (
    '{"name":{{json .Name}},"driver":{{json .Driver}},"scope":{{json .Scope}},"created_at":{{json .CreatedAt}},'
    '"mountpoint":{{json .Mountpoint}},"labels":{{json .Labels}},"options":{{json .Options}}}'
)
NETWORK_FIELDS = (
    '{"id":{{json .Id}},"name":{{json .Name}},"driver":{{json .Driver}},"scope":{{json .Scope}},'
    '"created":{{json .Created}},"labels":{{json .Labels}}}'
)
IMAGE_FIELDS = '{"id":{{json .Id}},"repo_tags":{{json .RepoTags}},"labels":{{json .Config.Labels}}}'
FIELD_KEYS = {
    "container": ("id", "name", "labels", "image", "config_image", "created", "status", "mounts", "networks"),
    "volume": ("name", "driver", "scope", "created_at", "mountpoint", "labels", "options"),
    "network": ("id", "name", "driver", "scope", "created", "labels"),
    "image": ("id", "repo_tags", "labels"),
}

# ----------------------------------------------------------------- envelope allowlists
TOP_LEVEL_KEYS = frozenset({"name", "services", "volumes", "networks"})
SERVICE_KEYS = frozenset({"image", "build", "restart", "environment", "volumes", "healthcheck", "logging",
                          "depends_on", "ports", "networks", "labels", "command", "entrypoint"})
# Extra service keys observed in the recorded real-Compose renders (fixtures/compose/): none.
BENIGN_SERVICE_KEYS = frozenset()
FORBIDDEN_SERVICE_KEYS = frozenset({
    "privileged", "cap_add", "devices", "device_cgroup_rules", "network_mode", "pid", "ipc", "uts",
    "userns_mode", "cgroup", "cgroup_parent", "security_opt", "sysctls", "volumes_from", "env_file", "runtime",
    "isolation", "group_add", "extra_hosts", "tmpfs", "ulimits", "oom_kill_disable", "pull_policy", "profiles",
    "secrets", "configs", "user",
})
BUILD_KEYS = frozenset({"context", "dockerfile", "labels"})
PORT_KEYS = frozenset({"target", "published", "host_ip", "protocol", "mode"})
DEPENDS_ON_KEYS = frozenset({"condition", "required", "restart"})   # required/restart: recorded fixtures
DB_VOLUME_KEYS = frozenset({"type", "source", "target", "volume", "read_only"})
TOP_VOLUME_KEYS = frozenset({"name", "labels", "driver"})
TOP_NETWORK_KEYS = frozenset({"name", "labels", "driver"})
# The recorded real render carries `ipam: {}` on the default network; only that empty value is benign.
BENIGN_NETWORK_KEYS = frozenset({"ipam"})
DB_IMAGE = "postgres:16"
DB_VOLUME_TARGET = "/var/lib/postgresql/data"
FRONTEND_PORT = 5173
BACKEND_PROXY_TARGET = "http://backend:8000"
HEALTHCHECK_LITERAL = ["CMD-SHELL", 'pg_isready -U "${POSTGRES_USER}" -d "${POSTGRES_DB}"']
HEALTHCHECK_DOUBLED = ["CMD-SHELL", 'pg_isready -U "$${POSTGRES_USER}" -d "$${POSTGRES_DB}"']
SERVICE_ENVIRONMENT = {
    "db": ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"),
    "backend": ("DATABASE_URL", "SITE_TIMEZONE"),
    "frontend": ("BACKEND_PROXY_TARGET", "__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS"),
}


class DockerScopeError(RuntimeError):
    """A refusal of the Docker scope rules. ``code`` names the rule; ``findings`` say where."""

    def __init__(self, code, findings=()):
        self.code = code
        self.findings = tuple(findings)
        super().__init__(code + ": " + "; ".join(finding.render() for finding in self.findings))


@dataclasses.dataclass(frozen=True)
class Finding:
    code: str
    path: str       # JSON path ("$.services.db.image") or "<kind>:<key>"
    message: str

    def render(self):
        return f"{self.code} at {self.path}: {self.message}"


def _raise(code, findings):
    findings = sorted(findings, key=lambda finding: (finding.path, finding.code, finding.message))
    raise DockerScopeError(code, findings)


def _reject_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key " + json.dumps(key))
        result[key] = value
    return result


def _reject_constant(name):
    raise ValueError("non-finite JSON number " + name)


def strict_json(text, *, code, path):
    """Strict JSON text -> value; duplicate keys and non-finite numbers are refused as ``code``."""
    try:
        return json.loads(text, object_pairs_hook=_reject_duplicates, parse_constant=_reject_constant)
    except (ValueError, TypeError) as exc:
        raise DockerScopeError(code, [Finding(code, path, f"invalid JSON ({exc})")]) from exc


def normalize_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------- daemon


@dataclasses.dataclass(frozen=True)
class DaemonObservation:
    endpoint: str
    engine_id: str
    server_version: str
    operating_system: str
    rootless: bool


def parse_daemon_info(text, *, endpoint):
    """``docker info --format '{{json .}}'`` -> DaemonObservation.

    A non-empty ``ServerErrors`` is ``daemon-unreachable`` (some CLIs exit 0 with it). ``ID``
    must be a non-empty string and ``SecurityOptions`` must be present as a list of strings
    or ``null`` (the engine serializes an empty list as null); anything else is
    ``daemon-info-invalid``.
    """
    info = strict_json(text, code="daemon-info-invalid", path="$")
    if not isinstance(info, dict):
        raise DockerScopeError("daemon-info-invalid", [Finding("daemon-info-invalid", "$", "not a JSON object")])
    errors = info.get("ServerErrors")
    if errors:
        raise DockerScopeError("daemon-unreachable", [Finding("daemon-unreachable", "$.ServerErrors", "server error")])
    engine_id = info.get("ID")
    if not isinstance(engine_id, str) or not engine_id:
        raise DockerScopeError("daemon-info-invalid", [Finding("daemon-info-invalid", "$.ID", "ID")])
    if "SecurityOptions" not in info:
        raise DockerScopeError("daemon-info-invalid", [Finding("daemon-info-invalid", "$.SecurityOptions",
                                                               "SecurityOptions")])
    options = info["SecurityOptions"]
    if options is None:
        options = []
    if not isinstance(options, list) or not all(isinstance(item, str) for item in options):
        raise DockerScopeError("daemon-info-invalid", [Finding("daemon-info-invalid", "$.SecurityOptions",
                                                               "SecurityOptions")])
    rootless = any("name=rootless" in item.split(",") for item in options)
    version = info.get("ServerVersion")
    system = info.get("OperatingSystem")
    return DaemonObservation(endpoint=str(endpoint), engine_id=engine_id,
                             server_version=version if isinstance(version, str) else "",
                             operating_system=system if isinstance(system, str) else "",
                             rootless=rootless)


def check_daemon(observation, binding):
    """The observed daemon must be rootful and answer as the registered engine ID exactly."""
    if observation.rootless:
        raise DockerScopeError("daemon-rootless", [Finding("daemon-rootless", "$.SecurityOptions", "name=rootless")])
    if observation.engine_id != binding.engine_id:
        raise DockerScopeError("daemon-drift", [Finding("daemon-drift", "$.ID", "engine ID differs")])


# ------------------------------------------------------------------- image override


def topology_names(project):
    return {"volume": {"postgres_data": f"{project}_postgres_data"}, "network": {"default": f"{project}_default"}}


def render_image_override(images):
    """The protected image override: exact bytes; parse_image_override accepts only this grammar."""
    lines = ["services:"]
    for service in BUILT_SERVICES:
        value = images[service]
        reference = value["reference"] if isinstance(value, dict) else value
        if not isinstance(reference, str) or not IMAGE_RE.fullmatch(reference):
            raise DockerScopeError("envelope-override", [Finding("envelope-override", f"$.services.{service}.image",
                                                                 "invalid image reference")])
        lines += [f"  {service}:", f"    image: {json.dumps(reference)}"]
    return ("\n".join(lines) + "\n").encode("utf-8")


_OVERRIDE_IMAGE_RE = re.compile(r'    image: ("[^"\\]*")\Z')


def parse_image_override(data, *, project):
    """Protected override bytes -> {"backend": ref, "frontend": ref}; any other text is refused."""
    def refuse(message):
        raise DockerScopeError("envelope-override", [Finding("envelope-override", "override", message)])

    if not isinstance(data, (bytes, bytearray)):
        refuse("not bytes")
    try:
        lines = bytes(data).decode("utf-8").split("\n")
    except UnicodeDecodeError:
        refuse("not UTF-8")
    if len(lines) != 6 or lines[0] != "services:" or lines[-1] != "":
        refuse("not the exact image override grammar")
    images = {}
    for index, service in enumerate(BUILT_SERVICES):
        if lines[1 + 2 * index] != f"  {service}:":
            refuse("not the exact image override grammar")
        match = _OVERRIDE_IMAGE_RE.fullmatch(lines[2 + 2 * index])
        if match is None:
            refuse("not the exact image override grammar")
        reference = json.loads(match.group(1))
        if not re.fullmatch(re.escape(project) + "-" + service + r":[A-Za-z0-9_.-]+", reference):
            refuse(f"{service} image is not a {project}-{service} tag")
        images[service] = reference
    try:
        rendered = render_image_override(images)
    except DockerScopeError:
        refuse("invalid image reference")
    if rendered != bytes(data):
        refuse("not the exact image override grammar")
    return images


# ------------------------------------------------------------------- Compose envelope


@dataclasses.dataclass(frozen=True)
class ComposeExpectation:
    project: str
    instance_id: str
    repo_root: str
    values: types.MappingProxyType
    database_url: str
    images: object = None      # None, or {"backend": ref, "frontend": ref}


@dataclasses.dataclass(frozen=True)
class EnvelopeResult:
    escape_mode: str
    names: dict


def _escape(mode):
    if mode == "doubled":
        return lambda value: value.replace("$", "$$")
    return lambda value: value


def validate_envelope(model, expectation):
    """Allowlist validation of one resolved Compose model; raises DockerScopeError(envelope-*)."""
    findings = []

    def add(code, path, message):
        findings.append(Finding(code, path, message))

    project = expectation.project
    label = {INSTANCE_LABEL: expectation.instance_id}
    if not isinstance(model, dict):
        _raise("envelope-render-failed", [Finding("envelope-render-failed", "$", "the model is not a JSON object")])
    for key in sorted(set(model) - TOP_LEVEL_KEYS):
        add("envelope-top-level", f"$.{key}", "top-level key outside the PartFlow topology")
    if model.get("name") != project:
        add("envelope-project-name", "$.name", "project name differs from the registered Compose project")
    services = model.get("services")
    if not isinstance(services, dict) or set(services) != set(SERVICES):
        add("envelope-service-set", "$.services", "the service set must be exactly db, backend, frontend")
        services = services if isinstance(services, dict) else {}
    escape_mode = None
    db = services.get("db")
    test = db.get("healthcheck", {}).get("test") if isinstance(db, dict) and isinstance(db.get("healthcheck"), dict) \
        else None
    if test == HEALTHCHECK_LITERAL:
        escape_mode = "literal"
    elif test == HEALTHCHECK_DOUBLED:
        escape_mode = "doubled"
    else:
        add("envelope-escape-unrecognized", "$.services.db.healthcheck.test",
            "the db healthcheck does not calibrate a known dollar-escape mode")
    for service in SERVICES:
        if service in services:
            _validate_service(service, services[service], expectation, escape_mode, label, add)
    names = topology_names(project)
    volumes = model.get("volumes")
    if not isinstance(volumes, dict) or set(volumes) != {"postgres_data"}:
        add("envelope-volume", "$.volumes", "top-level volumes must be exactly postgres_data")
    else:
        body = volumes["postgres_data"]
        path = "$.volumes.postgres_data"
        if not isinstance(body, dict):
            add("envelope-volume", path, "not an object")
        else:
            for key in sorted(set(body) - TOP_VOLUME_KEYS):
                add("envelope-volume", f"{path}.{key}", "volume option outside the allowlist")
            if body.get("name") != names["volume"]["postgres_data"]:
                add("envelope-volume", f"{path}.name", "volume name differs from <project>_postgres_data")
            if body.get("driver", "local") != "local":
                add("envelope-volume", f"{path}.driver", "only the local driver is supported")
            if body.get("labels") != label:
                add("envelope-label", f"{path}.labels", "instance label missing or wrong")
    networks = model.get("networks")
    if not isinstance(networks, dict) or set(networks) != {"default"}:
        add("envelope-network", "$.networks", "top-level networks must be exactly default")
    else:
        body = networks["default"]
        path = "$.networks.default"
        if not isinstance(body, dict):
            add("envelope-network", path, "not an object")
        else:
            for key in sorted(set(body) - TOP_NETWORK_KEYS):
                if key in BENIGN_NETWORK_KEYS and body[key] == {}:
                    continue
                add("envelope-network", f"{path}.{key}", "network option outside the allowlist")
            if body.get("name") != names["network"]["default"]:
                add("envelope-network", f"{path}.name", "network name differs from <project>_default")
            if body.get("driver", "bridge") != "bridge":
                add("envelope-network", f"{path}.driver", "only the bridge driver is supported")
            if body.get("labels") != label:
                add("envelope-label", f"{path}.labels", "instance label missing or wrong")
    if findings:
        ordered = sorted(findings, key=lambda finding: (finding.path, finding.code, finding.message))
        _raise(ordered[0].code, ordered)
    return EnvelopeResult(escape_mode=escape_mode, names=names)


def _validate_service(service, body, expectation, escape_mode, label, add):
    base = f"$.services.{service}"
    if not isinstance(body, dict):
        add("envelope-service-set", base, "service is not an object")
        return
    for key in sorted(body):
        if key in FORBIDDEN_SERVICE_KEYS:
            add("envelope-forbidden", f"{base}.{key}", "host-privilege option is forbidden")
        elif key not in SERVICE_KEYS and key not in BENIGN_SERVICE_KEYS:
            add("envelope-unknown-key", f"{base}.{key}", "service key outside the allowlist")
    project = expectation.project
    # Rule 3: image.
    if service == "db":
        if body.get("image") != DB_IMAGE:
            add("envelope-image", f"{base}.image", "db must run postgres:16")
        if "build" in body:
            add("envelope-image", f"{base}.build", "db is never built")
    else:
        if expectation.images is None:
            if "image" in body:
                add("envelope-image", f"{base}.image", "no image override is selected for this invocation")
        else:
            image = body.get("image")
            if not isinstance(image, str) \
                    or not re.fullmatch(re.escape(project) + "-" + service + r":[A-Za-z0-9_.-]+", image) \
                    or image != expectation.images.get(service):
                add("envelope-image", f"{base}.image", "image differs from the protected override")
        # Rule 4: build.
        build = body.get("build")
        if not isinstance(build, dict):
            add("envelope-build-context", f"{base}.build", "build context missing")
        else:
            for key in sorted(set(build) - BUILD_KEYS):
                add("envelope-build-option", f"{base}.build.{key}", "build option outside the allowlist")
            if build.get("context") != expectation.repo_root + "/" + service:
                add("envelope-build-context", f"{base}.build.context", "context is not <repo_root>/" + service)
            if build.get("dockerfile", "Dockerfile") != "Dockerfile":
                add("envelope-build-option", f"{base}.build.dockerfile", "only the default Dockerfile is allowed")
            if build.get("labels") != label:
                add("envelope-label", f"{base}.build.labels", "instance label missing or wrong")
    # Rule 5: service volumes.
    volumes = body.get("volumes", [])
    if not isinstance(volumes, list):
        add("envelope-volume", f"{base}.volumes", "not a list")
        volumes = []
    for index, mount in enumerate(volumes):
        path = f"{base}.volumes[{index}]"
        if not isinstance(mount, dict):
            add("envelope-volume", path, "not an object")
            continue
        source = mount.get("source")
        if isinstance(source, str) and "docker.sock" in source:
            add("envelope-docker-socket", path, "the Docker socket is never mounted")
        elif mount.get("type") == "bind":
            add("envelope-bind-mount", path, "bind mounts are not part of the topology")
        elif service != "db" or len(volumes) != 1 or not _db_volume_ok(mount):
            add("envelope-volume", path, "only db mounts postgres_data at " + DB_VOLUME_TARGET)
    if service == "db" and not volumes:
        add("envelope-volume", f"{base}.volumes", "db must mount postgres_data")
    # Rule 6: ports.
    ports = body.get("ports")
    if service != "frontend":
        if ports:
            add("envelope-port", f"{base}.ports", "only frontend publishes a port")
    else:
        values = expectation.values
        if not isinstance(ports, list) or len(ports) != 1 or not isinstance(ports[0], dict):
            add("envelope-port", f"{base}.ports", "frontend publishes exactly one port")
        else:
            port = ports[0]
            path = f"{base}.ports[0]"
            for key in sorted(set(port) - PORT_KEYS):
                add("envelope-port", f"{path}.{key}", "port option outside the allowlist")
            if port.get("target") != FRONTEND_PORT:
                add("envelope-port", f"{path}.target", "target must be 5173")
            if str(port.get("published")) != values["PARTFLOW_HTTP_PORT"]:
                add("envelope-port", f"{path}.published", "published port differs from the approved value")
            if port.get("host_ip") != values["PARTFLOW_BIND_IP"]:
                add("envelope-port", f"{path}.host_ip", "listener address differs from the approved value")
            if port.get("protocol", "tcp") != "tcp":
                add("envelope-port", f"{path}.protocol", "only tcp is allowed")
            if port.get("mode", "ingress") != "ingress":
                add("envelope-port", f"{path}.mode", "only ingress mode is allowed")
    # Rule 7: environment, compared literally through the calibrated escape.
    environment = body.get("environment")
    expected_keys = SERVICE_ENVIRONMENT[service]
    if not isinstance(environment, dict) or set(environment) != set(expected_keys):
        add("envelope-environment", f"{base}.environment", "environment keys differ from the allowlist")
    elif escape_mode is not None:
        escape = _escape(escape_mode)
        values = expectation.values
        if service == "db":
            expected = {key: values[key] for key in expected_keys}
        elif service == "backend":
            expected = {"DATABASE_URL": expectation.database_url, "SITE_TIMEZONE": values["SITE_TIMEZONE"]}
        else:
            expected = {"BACKEND_PROXY_TARGET": BACKEND_PROXY_TARGET,
                        "__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS": values["PARTFLOW_ALLOWED_HOST"]}
        for key in expected_keys:
            if environment.get(key) != escape(expected[key]):
                add("envelope-environment", f"{base}.environment.{key}", "value differs from the frozen value")
    # Rule 9: labels.
    if body.get("labels") != label:
        add("envelope-label", f"{base}.labels", "instance label missing or wrong")
    # Rule 12: other service keys.
    if body.get("restart") != "unless-stopped":
        add("envelope-service-option", f"{base}.restart", "restart must be unless-stopped")
    logging = body.get("logging")
    if not isinstance(logging, dict) or logging.get("driver") != "json-file":
        add("envelope-service-option", f"{base}.logging", "logging driver must be json-file")
    depends = body.get("depends_on", {})
    if not isinstance(depends, dict):
        add("envelope-service-option", f"{base}.depends_on", "not an object")
    else:
        for name, condition in sorted(depends.items()):
            path = f"{base}.depends_on.{name}"
            if name not in SERVICES or name == service or not isinstance(condition, dict) \
                    or set(condition) - DEPENDS_ON_KEYS or condition.get("condition") != "service_healthy" \
                    or not isinstance(condition.get("required", True), bool) \
                    or not isinstance(condition.get("restart", False), bool):
                add("envelope-service-option", path, "dependency must be a healthy topology service")
    if body.get("networks") not in ({"default": None}, {"default": {}}):
        add("envelope-network", f"{base}.networks", "service must join only the default network")
    for key in ("command", "entrypoint"):
        if body.get(key) is not None:
            add("envelope-service-option", f"{base}.{key}", key + " overrides are not part of the topology")
    if not isinstance(body.get("healthcheck"), dict):
        add("envelope-service-option", f"{base}.healthcheck", "healthcheck must be an object")


def _db_volume_ok(mount):
    if set(mount) - DB_VOLUME_KEYS:
        return False
    if mount.get("type") != "volume" or mount.get("source") != "postgres_data" or mount.get("target") != DB_VOLUME_TARGET:
        return False
    if mount.get("volume", {}) not in ({}, {"nocopy": False}):
        return False
    return mount.get("read_only", False) is False


# ----------------------------------------------------------------------- inventory


def image_tag_pattern(project):
    return re.compile("^" + re.escape(project) + r"-(backend|frontend):(candidate|backup)-[a-z0-9._-]+$")


def _labels(value, *, where):
    if value is None:
        return {}
    if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
        raise DockerScopeError("inventory-invalid", [Finding("inventory-invalid", where, "labels are not a string map")])
    return dict(value)


def _string(value, *, where, field, allow_empty=True):
    if not isinstance(value, str) or (not allow_empty and not value):
        raise DockerScopeError("inventory-invalid", [Finding("inventory-invalid", where, field + " is not a string")])
    return value


def parse_field_lines(text, *, kind):
    """Strict parse of one ``inspect --format <KIND>_FIELDS`` output (one JSON object per line).

    Duplicate keys are refused; the key set must equal the template's. Nested objects are
    reduced to the whitelisted sub-fields here; a volume's ``Options`` becomes a hash.
    """
    keys = FIELD_KEYS.get(kind)
    if keys is None:
        raise DockerScopeError("inventory-invalid", [Finding("inventory-invalid", kind, "unknown kind")])
    result = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        where = f"{kind}[{number}]"
        item = strict_json(line, code="inventory-invalid", path=where)
        if not isinstance(item, dict) or set(item) != set(keys):
            raise DockerScopeError("inventory-invalid", [Finding("inventory-invalid", where, "unexpected fields")])
        record = {"labels": _labels(item["labels"], where=where)}
        if kind == "container":
            record["id"] = _string(item["id"], where=where, field="id", allow_empty=False)
            record["name"] = _string(item["name"], where=where, field="name").lstrip("/")
            for field in ("image", "config_image", "created", "status"):
                record[field] = _string(item[field], where=where, field=field)
            mounts = []
            for mount in item["mounts"] or []:
                if not isinstance(mount, dict):
                    raise DockerScopeError("inventory-invalid", [Finding("inventory-invalid", where, "mounts")])
                mounts.append({"type": str(mount.get("Type", "")), "name": str(mount.get("Name", "")),
                               "source": str(mount.get("Source", "")),
                               "destination": str(mount.get("Destination", "")), "rw": bool(mount.get("RW"))})
            networks = {}
            raw_networks = item["networks"] or {}
            if not isinstance(raw_networks, dict):
                raise DockerScopeError("inventory-invalid", [Finding("inventory-invalid", where, "networks")])
            for name, settings in raw_networks.items():
                network_id = settings.get("NetworkID", "") if isinstance(settings, dict) else ""
                networks[str(name)] = {"network_id": str(network_id or "")}
            record["mounts"] = mounts
            record["networks"] = networks
        elif kind == "volume":
            record["name"] = _string(item["name"], where=where, field="name", allow_empty=False)
            for field in ("driver", "scope", "created_at", "mountpoint"):
                record[field] = _string(item[field], where=where, field=field)
            options = item["options"] or {}
            if not isinstance(options, dict):
                raise DockerScopeError("inventory-invalid", [Finding("inventory-invalid", where, "options")])
            record["options_sha256"] = sha256_bytes(normalize_json(options))
        elif kind == "network":
            record["id"] = _string(item["id"], where=where, field="id", allow_empty=False)
            record["name"] = _string(item["name"], where=where, field="name", allow_empty=False)
            for field in ("driver", "scope", "created"):
                record[field] = _string(item[field], where=where, field=field)
        else:
            record["id"] = _string(item["id"], where=where, field="id", allow_empty=False)
            tags = item["repo_tags"] or []
            if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
                raise DockerScopeError("inventory-invalid", [Finding("inventory-invalid", where, "repo_tags")])
            record["repo_tags"] = sorted(tags)
        result.append(record)
    return result


@dataclasses.dataclass(frozen=True)
class Resource:
    kind: str           # container | volume | network | image | bind
    key: str            # full container ID, volume name, network name, image reference, bind path
    identity: dict
    cls: str            # owned | <blocker code> | <exclusion class>
    reason: str
    users: tuple = ()

    def as_entry(self):
        return {"kind": self.kind, "key": self.key, "class": self.cls, "reason": self.reason}


@dataclasses.dataclass(frozen=True)
class Inventory:
    project: str
    instance_id: str
    owned: tuple
    excluded: tuple
    blockers: tuple
    bind_paths: tuple

    @property
    def present_topology(self):
        return any(item.kind in ("container", "volume", "network") for item in self.owned + self.blockers)

    @property
    def owned_image_tags(self):
        return frozenset(item.key for item in self.owned if item.kind == "image")

    def owned_of(self, kind):
        return [item for item in self.owned if item.kind == kind]

    def summary(self):
        counts = {kind: len(self.owned_of(kind)) for kind in ("container", "network", "volume", "image")}
        return {
            "containers": counts["container"], "networks": counts["network"], "volumes": counts["volume"],
            "image_tags": counts["image"], "excluded": len(self.excluded), "blocked": len(self.blockers),
            "blockers": [item.as_entry() for item in self.blockers],
            "exclusions": [item.as_entry() for item in self.excluded],
            "bind_paths": list(self.bind_paths),
        }

    def record(self):
        """Identities and users only (inventory-*.json); never values or environment."""
        def entry(item):
            data = item.as_entry()
            data["identity"] = item.identity
            if item.kind in ("volume", "network"):
                data["users"] = list(item.users)
            return data
        return {"project": self.project, "instance_id": self.instance_id,
                "owned": [entry(item) for item in self.owned], "blockers": [entry(item) for item in self.blockers],
                "excluded": [entry(item) for item in self.excluded], "bind_paths": list(self.bind_paths)}

    def index(self, kind):
        """(by_id, by_name) identity maps of every listed resource of ``kind``, for compare_identity."""
        by_id, by_name = {}, {}
        for item in self.owned + self.blockers + self.excluded:
            if item.kind != kind or not item.identity:
                continue
            if "id" in item.identity:
                by_id[item.identity["id"]] = item.identity
            name = item.identity.get("reference") if kind == "image" else item.identity.get("name")
            if name is not None:
                by_name[name] = item.identity
        return by_id, by_name

    def users_of(self, kind, key):
        for item in self.owned + self.blockers + self.excluded:
            if item.kind == kind and item.key == key:
                return item.users
        return ()


def _container_identity(container):
    labels = container["labels"]
    return {"id": container["id"], "name": container["name"], "service": labels.get(COMPOSE_SERVICE_LABEL, ""),
            "oneoff": labels.get(COMPOSE_ONEOFF_LABEL, ""), "created": container["created"],
            "image": container["image"]}


def _volume_identity(volume):
    return {"name": volume["name"], "driver": volume["driver"], "scope": volume["scope"],
            "created_at": volume["created_at"], "mountpoint": volume["mountpoint"], "labels": volume["labels"],
            "options_sha256": volume["options_sha256"]}


def _network_identity(network):
    return {"id": network["id"], "name": network["name"], "driver": network["driver"],
            "created": network["created"], "labels": network["labels"]}


def classify_inventory(*, project, instance_id, containers, volumes, networks, images):
    """Exact classification of observed resources (no prefixes, no guessing; ARCH section 8)."""
    owned, excluded, blockers, bind_paths = [], [], [], []
    owned_containers = set()
    container_ids = set()
    for container in containers:
        labels = container["labels"]
        claim = labels.get(INSTANCE_LABEL)
        compose_project = labels.get(COMPOSE_PROJECT_LABEL)
        service = labels.get(COMPOSE_SERVICE_LABEL)
        markers = labels.get(COMPOSE_ONEOFF_LABEL) in ("True", "False") and (
            COMPOSE_CONTAINER_NUMBER_LABEL in labels or COMPOSE_CONFIG_HASH_LABEL in labels)
        identity = _container_identity(container)
        container_ids.add(container["id"])
        if claim == instance_id:
            if compose_project == project and service in SERVICES and markers:
                owned.append(Resource("container", container["id"], identity, "owned", "compose " + service))
                owned_containers.add(container["id"])
            else:
                blockers.append(Resource("container", container["id"], identity, "resource-label-conflict",
                                         "instance label without the Compose project/service/container markers "
                                         "of this instance"))
        elif compose_project == project:
            code = "resource-legacy-unlabeled" if claim is None else "resource-foreign-claim"
            reason = "Compose project label without the instance label" if claim is None \
                else "claimed by instance " + claim
            blockers.append(Resource("container", container["id"], identity, code, reason))
    # Users: every container (running or stopped) that mounts a volume or joins a network.
    volume_users, network_users = {}, {}
    for container in containers:
        for mount in container["mounts"]:
            if mount["type"] == "volume" and mount["name"]:
                volume_users.setdefault(mount["name"], set()).add(container["id"])
        for name, settings in container["networks"].items():
            network_users.setdefault(name, set()).add(container["id"])
            if settings.get("network_id"):
                network_users.setdefault("id:" + settings["network_id"], set()).add(container["id"])
    names = topology_names(project)
    topology_volumes = {name: key for key, name in names["volume"].items()}
    topology_networks = {name: key for key, name in names["network"].items()}
    inspected_volumes = set()
    for volume in volumes:
        inspected_volumes.add(volume["name"])
        users = tuple(sorted(volume_users.get(volume["name"], ())))
        resource = _classify_shared("volume", volume["name"], _volume_identity(volume), volume["labels"],
                                    volume["driver"], volume["scope"], "local", topology_volumes,
                                    COMPOSE_VOLUME_LABEL, users, owned_containers, project, instance_id)
        if resource is not None:
            (owned if resource.cls == "owned" else blockers if resource.cls.startswith("resource-")
             else excluded).append(resource)
    for network in networks:
        users = tuple(sorted(network_users.get(network["name"], set()) | network_users.get("id:" + network["id"], set())))
        resource = _classify_shared("network", network["name"], _network_identity(network), network["labels"],
                                    network["driver"], network["scope"], "bridge", topology_networks,
                                    COMPOSE_NETWORK_LABEL, users, owned_containers, project, instance_id)
        if resource is not None:
            (owned if resource.cls == "owned" else blockers if resource.cls.startswith("resource-")
             else excluded).append(resource)
    # References and retained bind mounts of owned containers.
    references = set()
    for container in containers:
        if container["id"] not in owned_containers:
            continue
        for mount in container["mounts"]:
            if mount["type"] == "bind":
                if mount["source"] not in bind_paths:
                    bind_paths.append(mount["source"])
                    excluded.append(Resource("bind", mount["source"], {}, "bind-retained",
                                             "bind mount of container " + container["name"]))
            elif mount["type"] == "volume" and mount["name"] and mount["name"] not in inspected_volumes \
                    and mount["name"] not in references:
                references.add(mount["name"])
                excluded.append(Resource("volume", mount["name"], {}, "reference",
                                         "mounted by an owned container; not inspected for deletion",
                                         tuple(sorted(volume_users.get(mount["name"], ())))))
    # Image tags.
    pattern = image_tag_pattern(project)
    foreign_images = {container["config_image"] for container in containers if container["id"] not in owned_containers}
    for image in images:
        reference = image["reference"]
        identity = {"reference": reference, "image_id": image["id"]}
        if image["labels"].get(INSTANCE_LABEL) != instance_id:
            excluded.append(Resource("image", reference, identity, "excluded", "unlabelled"))
        elif not pattern.fullmatch(reference):
            excluded.append(Resource("image", reference, identity, "excluded", "grammar"))
        elif reference in foreign_images:
            excluded.append(Resource("image", reference, identity, "excluded", "foreign-in-use"))
        else:
            owned.append(Resource("image", reference, identity, "owned", "instance image tag"))
    order = {kind: index for index, kind in enumerate(DELETION_ORDER + ("bind",))}
    sort_key = lambda item: (order[item.kind], item.key)  # noqa: E731
    return Inventory(project=project, instance_id=instance_id, owned=tuple(sorted(owned, key=sort_key)),
                     excluded=tuple(sorted(excluded, key=sort_key)), blockers=tuple(sorted(blockers, key=sort_key)),
                     bind_paths=tuple(sorted(bind_paths)))


def _classify_shared(kind, name, identity, labels, driver, scope, expected_driver, topology, compose_label, users,
                     owned_containers, project, instance_id):
    claim = labels.get(INSTANCE_LABEL)
    if name in topology:
        key = topology[name]
        if driver != expected_driver or scope != "local":
            return Resource(kind, name, identity, "resource-unsupported-driver",
                            f"driver {driver!r} scope {scope!r} is not a local {expected_driver} {kind}", users)
        if claim is None:
            if labels.get(COMPOSE_PROJECT_LABEL) == project:
                return Resource(kind, name, identity, "resource-legacy-unlabeled",
                                "Compose project label without the instance label", users)
            return Resource(kind, name, identity, "resource-name-collision",
                            "topology name without the labels of this instance", users)
        if claim != instance_id:
            return Resource(kind, name, identity, "resource-foreign-claim", "claimed by instance " + claim, users)
        if labels.get(COMPOSE_PROJECT_LABEL) != project or labels.get(compose_label) != key:
            return Resource(kind, name, identity, "resource-label-conflict",
                            "instance label without the Compose project/" + kind + " labels of this instance", users)
        foreign_users = [user for user in users if user not in owned_containers]
        if foreign_users:
            return Resource(kind, name, identity, "resource-shared",
                            f"used by {len(foreign_users)} container(s) that do not belong to this instance", users)
        return Resource(kind, name, identity, "owned", "topology " + kind + " " + key, users)
    if claim == instance_id:
        return Resource(kind, name, identity, "owned-outside-topology",
                        "labelled for this instance but not part of the topology; retained", users)
    return None


# -------------------------------------------------------------------- deletion plan


def _utc():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _daemon_entry(daemon):
    if isinstance(daemon, DaemonObservation):
        return {"endpoint": daemon.endpoint, "engine_id": daemon.engine_id}
    return {"endpoint": str(daemon["endpoint"]), "engine_id": str(daemon["engine_id"])}


def _candidate(item):
    entry = {"kind": item.kind, "key": item.key, "identity": item.identity}
    if item.kind in ("volume", "network"):
        entry["users"] = list(item.users)
    return entry


def plan_deletion(inventory, *, kind, operation_id, daemon, recovery_id=None, covered_image_refs=None):
    """The closed deletion plan of ``kind`` built from one inventory. Blockers refuse."""
    if inventory.blockers:
        _raise("resource-blocked", [Finding(item.cls, f"{item.kind}:{item.key}", item.reason)
                                    for item in inventory.blockers])
    if kind not in PLAN_KINDS:
        _raise("plan-invalid", [Finding("plan-invalid", "$.kind", "unknown plan kind")])
    candidates = [_candidate(item) for item in inventory.owned if item.kind == "container"]
    candidates += [_candidate(item) for item in inventory.owned if item.kind == "network"]
    candidates += [_candidate(item) for item in inventory.owned
                   if item.kind == "volume" and item.key == topology_names(inventory.project)["volume"]["postgres_data"]]
    exclusions = [item.as_entry() for item in inventory.excluded]
    pending = []
    owned_images = [item for item in inventory.owned if item.kind == "image"]
    if kind == "abort-deploy":
        coverage = "none"
        exclusions += [{"kind": "image", "key": item.key, "class": "retained", "reason": "abort-retains-images"}
                       for item in owned_images]
    elif covered_image_refs is None:
        coverage = "pending"
        pending = sorted(item.key for item in owned_images)
    else:
        coverage = "bound"
        covered = set(covered_image_refs)
        covered_ids = {item.identity["image_id"] for item in owned_images if item.key in covered}
        for item in owned_images:
            if item.key in covered or item.identity["image_id"] in covered_ids:
                candidates.append(_candidate(item))
            else:
                exclusions.append({"kind": "image", "key": item.key, "class": "retained", "reason": "not-covered"})
    return {
        "schema_version": PLAN_SCHEMA_VERSION, "kind": kind, "operation_id": operation_id,
        "instance_id": inventory.instance_id, "slug": None, "compose_project": inventory.project,
        "daemon": _daemon_entry(daemon), "created": _utc(), "label_key": INSTANCE_LABEL,
        "recovery_id": recovery_id, "image_coverage": coverage, "order": list(DELETION_ORDER),
        "candidates": candidates, "pending_images": pending,
        "exclusions": sorted(exclusions, key=lambda entry: (entry["kind"], entry["key"])),
        "bind_paths": list(inventory.bind_paths),
    }


def _owned_tags(plan):
    tags = {item["key"] for item in plan["candidates"] if item["kind"] == "image"}
    tags |= set(plan.get("pending_images") or ())
    tags |= {entry["key"] for entry in plan["exclusions"]
             if entry["kind"] == "image" and entry.get("reason") in ("not-covered", "abort-retains-images")}
    return tags


def compare_plans(preliminary, binding, *, created_image_refs):
    """Container/network/volume candidates and identities equal; owned tags equal modulo this operation's."""
    def resources(plan):
        return {(item["kind"], item["key"]): item["identity"] for item in plan["candidates"] if item["kind"] != "image"}

    findings = []
    before, after = resources(preliminary), resources(binding)
    for key in sorted(set(before) | set(after)):
        path = f"{key[0]}:{key[1]}"
        if key not in after:
            findings.append(Finding("plan-changed", path, "disappeared"))
        elif key not in before:
            findings.append(Finding("plan-changed", path, "appeared"))
        elif before[key] != after[key]:
            findings.append(Finding("plan-changed", path, "identity changed"))
    created = set(created_image_refs)
    old_tags, new_tags = _owned_tags(preliminary) - created, _owned_tags(binding) - created
    for tag in sorted(old_tags ^ new_tags):
        findings.append(Finding("plan-changed", "image:" + tag, "appeared" if tag in new_tags else "disappeared"))
    if findings:
        _raise("plan-changed", findings)


def users_violations(plan, item, observed_users):
    """Observed users of a planned volume/network that are not planned container candidates."""
    planned = {entry["key"] for entry in plan["candidates"] if entry["kind"] == "container"}
    return tuple(Finding("plan-drift", f"{item['kind']}:{item['key']}", "used by container " + user[:12]
                         + " that is not in the plan")
                 for user in sorted(set(observed_users) - planned))


def plan_bytes(plan):
    return normalize_json(plan) + b"\n"


def plan_sha256(plan):
    return sha256_bytes(plan_bytes(plan))


def load_plan(data, *, expected_sha256, instance_id, kind, operation_id):
    """Verify frozen plan bytes against the journal reference; any mismatch is plan-invalid."""
    def refuse(message):
        raise DockerScopeError("plan-invalid", [Finding("plan-invalid", "deletion-plan.json", message)])

    if not isinstance(data, (bytes, bytearray)) or sha256_bytes(bytes(data)) != expected_sha256:
        refuse("hash differs from the journal reference")
    try:
        plan = strict_json(bytes(data).decode("utf-8"), code="plan-invalid", path="deletion-plan.json")
    except UnicodeDecodeError:
        refuse("not UTF-8")
    if not isinstance(plan, dict) or plan_bytes(plan) != bytes(data):
        refuse("not a canonical plan document")
    if plan.get("schema_version") != PLAN_SCHEMA_VERSION:
        refuse("unsupported schema_version")
    if plan.get("instance_id") != instance_id:
        refuse("belongs to another instance")
    if plan.get("kind") != kind:
        refuse("plan kind is not " + kind)
    if plan.get("operation_id") != operation_id:
        refuse("operation differs from the journal reference")
    expected_coverage = "bound" if kind == "purge" else "none"
    if plan.get("image_coverage") != expected_coverage:
        refuse("image coverage is not " + expected_coverage)
    candidates = plan.get("candidates")
    if not isinstance(candidates, list) or not all(
            isinstance(item, dict) and item.get("kind") in DELETION_ORDER and isinstance(item.get("key"), str)
            and isinstance(item.get("identity"), dict) for item in candidates):
        refuse("candidates are malformed")
    return plan


def compare_identity(item, observed_by_id, observed_by_name):
    """"identical", "absent" or ("drift", reason) for one planned item against fresh observations."""
    kind, identity = item["kind"], item["identity"]
    if kind in ("container", "network"):
        observed = observed_by_id.get(identity["id"])
        if observed is None:
            if identity["name"] in observed_by_name:
                return ("drift", "replaced: the same name now has a new ID")
            return "absent"
    else:
        name = identity["reference"] if kind == "image" else identity["name"]
        observed = observed_by_name.get(name)
        if observed is None:
            return "absent"
    if observed == identity:
        return "identical"
    changed = sorted(field for field in set(identity) | set(observed) if identity.get(field) != observed.get(field))
    if "created_at" in changed or "created" in changed:
        return ("drift", "replaced: new creation time")
    return ("drift", "changed " + ", ".join(changed))
