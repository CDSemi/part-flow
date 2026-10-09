"""Instances of the run (SPEC 3.2) and their oracle shortcuts."""
import hashlib
import json
import os
from pathlib import Path
import time

from . import api
from . import observe
from . import phrases
from . import util

SPECS = {
    "alpha": {"project": "partflow", "workspace": "source-tree", "port": 15173},
    "bravo": {"project": "partflow_test", "workspace": "repo", "port": 15174},
    "charlie": {"project": "pfcharlie", "workspace": "repo", "port": 15175},
}
OTHERSITE = "othersite"
ADMIN_DIALOGUE = (
    (r"Group that can read and edit(?:(?!Choose a number)[\s\S])*Choose a number or type a group name[^\n]*: \Z",
     "pfadmin"),
    (r"Group that can view and copy backups(?:(?!Choose a number)[\s\S])*Choose a number or type a group name[^\n]*: "
     r"\Z", "pfread"),
    (r"Write [^\n]*\[y/N\]: \Z", "y"),
)
REUSE_ENV = ((r"Reuse this existing \.env for the new deployment \[Y/n\]: \Z", "y"),)


def replacement_spec(number):
    return {"project": f"pfr{number}", "workspace": "repo", "port": 15180 + number}


class Instance:
    def __init__(self, harness, slug):
        self.harness = harness
        self.slug = slug
        self.info = harness.instances.setdefault(slug, {})
        spec = SPECS.get(slug) or self.info.get("spec")
        if spec is None:
            raise util.HarnessError(f"unknown instance {slug}")
        self.info.setdefault("spec", spec)
        self.project = spec["project"]
        self.port = spec["port"]
        self.home = harness.homes / slug
        self.workspace = self.home / spec["workspace"]
        self.config_dir = self.home / "config"
        self.backups = self.home / "backups"
        self.recovery = self.home / "recovery"
        self._seeder = None

    # ----------------------------------------------------------------------------------------- lifecycle

    def pf(self, argv, **kwargs):
        return self.harness.pf(argv, instance=self.slug, **kwargs)

    def prepare_directories(self):
        for path in (self.harness.homes, self.home):
            path.mkdir(parents=True, exist_ok=True)
            os.chmod(str(path), 0o755)
        for path in (self.workspace, self.config_dir, self.backups, self.recovery):
            path.mkdir(parents=True, exist_ok=True)
            os.chmod(str(path), 0o755)

    def register(self, *, extra=()):
        self.prepare_directories()
        self.harness.pf(["config", "admin", "--configuration", str(self.config_dir), "--project", self.project],
                        dialogue=ADMIN_DIALOGUE, step_id=f"setup-{self.slug}-config-admin")
        self.harness.pf(["install", "register", "--slug", self.slug, "--project", self.project, "--workspace",
                         str(self.workspace), "--configuration", str(self.config_dir), "--backups", str(self.backups),
                         "--recovery", str(self.recovery), *extra], phrases=phrases.exact("REGISTER " + self.slug),
                        step_id=f"setup-{self.slug}-register")
        registry = self.harness.registry()
        entry = next(item for item in registry["instances"] if item["slug"] == self.slug)
        record = json.loads(Path(entry["record_path"]).read_text(encoding="utf-8"))
        lock = self.harness.pfroot / "locks" / (entry["instance_id"] + ".lock")
        self.info.update({"instance_id": entry["instance_id"], "record_path": entry["record_path"],
                          "private_state": record["paths"]["private_state"],
                          "lock": {"path": str(lock), "identity": lock_identity(lock)}})
        self.harness.save()
        app_dialogue = (
            (r"PostgreSQL user[^\n]*: \Z", ""), (r"PostgreSQL database[^\n]*: \Z", ""),
            (r"Factory IANA timezone[^\n]*: \Z", "America/Los_Angeles"),
            (r"Select access mode[^\n]*: \Z", "2"),
            (r"PartFlow HTTP port[^\n]*: \Z", str(self.port)),
            (r"Exact internal Reverse Proxy hostname: \Z", f"{self.slug}.pfa34.invalid"),
            (r"Write [^\n]*\[y/N\]: \Z", "y"),
        )
        self.pf(["config", "app"], dialogue=app_dialogue, step_id=f"setup-{self.slug}-config-app")
        self.register_secrets()

    def register_secrets(self):
        env = self.env()
        password = env.get("POSTGRES_PASSWORD")
        if password:
            self.harness.evidence.add_secret(password)
            self.harness.secrets.setdefault(self.slug, {})["postgres_password"] = password
            self.harness.save()

    def deploy(self, commit_name, *, step_id=None, expect_exit=0, **kwargs):
        sha = self.harness.commit(commit_name)
        return self.pf(["deploy", "--commit", sha, "--skip-ci"], phrases=phrases.exact("DEPLOY " + sha[:12]),
                       dialogue=REUSE_ENV, step_id=step_id, expect_exit=expect_exit, **kwargs)

    # -------------------------------------------------------------------------------------------- oracles

    def env(self):
        """The runtime .env values; while the instance is purged (no .env) the last values read, kept with the
        fixture secrets (a purge-then-restore keeps the same database name and credentials)."""
        path = self.config_dir / ".env"
        cache = self.harness.fixture_dir / f"env-{self.slug}.json"
        if path.exists():
            values = observe.env_values(path)
            if values:
                util.write_private(cache, json.dumps(values, indent=1, sort_keys=True), mode=0o600)
            return values
        return json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else {}

    def env_sha256(self):
        return observe.sha256_file(self.config_dir / ".env")

    def record_sha256(self):
        path = self.info.get("record_path")
        return observe.sha256_file(path) if path else None

    def db_container(self):
        return observe.running_container(self.project, "db")

    def backend_container(self):
        return observe.running_container(self.project, "backend")

    def snapshot(self, label, *, container=None, database=None, record=True):
        env = self.env()
        container = container or self.db_container()
        if container is None:
            raise util.HarnessError(f"{self.slug}: no running db container for snapshot {label}")
        value = observe.snapshot_container(container, env["POSTGRES_USER"], database or env["POSTGRES_DB"],
                                           label=label, instance=self.slug)
        if record:
            self.harness.evidence.write_json(f"snapshots/{label}.json", value)
        return value

    def heads(self):
        env = self.env()
        container = self.db_container()
        return observe.heads_container(container, env["POSTGRES_USER"], env["POSTGRES_DB"]) if container else None

    def reconcile(self, label):
        backend = self.backend_container()
        argv = self.harness.reconcile_argv()
        if backend is None:
            value = {"argv": list(argv), "exit": None, "result": "no-backend"}
        else:
            value = observe.reconcile_container(backend, argv)
        value.update({"label": label, "instance": self.slug, "at": util.utc()})
        self.harness.evidence.write_json(f"reconcile/{label}.json", value)
        return value

    def identities(self, label=None):
        value = observe.project_identities(self.project)
        value.update({"label": label, "scope": self.slug, "env_sha256": self.env_sha256(),
                      "record_sha256": self.record_sha256()})
        if label:
            self.harness.evidence.write_json(f"identities/{label}.json", value)
        return value

    def health(self):
        return api.Client(self.harness, self.slug, self.port).health()

    def wait_healthy(self, timeout=300):
        return api.wait_healthy(api.Client(self.harness, self.slug, self.port), timeout=timeout)

    def seeder(self):
        if self._seeder is None:
            self._seeder = api.Seeder(self.harness, self.slug, self.port, self.project)
        return self._seeder

    def lock_check(self):
        """SPEC 5.2: the registered instance lock, ``locks/registry.lock`` and the source-store locks recorded at
        install keep their inode, and no live process holds any of them (read-only: stat and /proc/locks)."""
        lock = self.info.get("lock")
        if not lock:
            return {"result": "no-lock-recorded"}
        value = check_lock(lock["path"], lock["identity"])
        others = []
        for path, identity in sorted((self.harness.fixture.get("locks") or {}).items()):
            if path == lock["path"] or "/locks/" in path and path.endswith(".lock") and \
                    not path.endswith("registry.lock"):
                continue  # the other instances' locks are checked by their own steps
            others.append(check_lock(path, identity))
        value["global"] = others
        if any(item.get("result") != "ok" for item in others):
            value["result"] = "FAIL"
        return value


def lock_identity(path):
    info = os.stat(str(path))
    return f"{info.st_dev}:{info.st_ino}"


def check_lock(path, identity):
    try:
        now = lock_identity(path)
    except OSError as exc:
        return {"path": str(path), "result": "missing", "error": str(exc)}
    info = os.stat(str(path))
    major, minor = os.major(info.st_dev), os.minor(info.st_dev)
    holders = []
    try:
        with open("/proc/locks", encoding="utf-8") as handle:
            for line in handle:
                fields = line.split()
                # e.g. "1: FLOCK  ADVISORY  WRITE 1234 fd:01:5678 0 EOF"
                for field in fields:
                    parts = field.split(":")
                    if len(parts) == 3 and parts[2] == str(info.st_ino):
                        try:
                            if int(parts[0], 16) == major and int(parts[1], 16) == minor:
                                holders.append(line.strip())
                        except ValueError:
                            continue
    except OSError:
        holders = ["/proc/locks unreadable"]
    return {"path": str(path), "identity_recorded": identity, "identity_now": now,
            "result": "ok" if now == identity and not holders else "FAIL", "holders": holders}


def wait_pf_idle(timeout=600):
    """No pf-admin process runs (SPEC 5.2 lock check precondition)."""
    def idle():
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                with open(f"/proc/{pid}/cmdline", "rb") as handle:
                    if b"pf-admin.py" in handle.read():
                        return False
            except OSError:
                continue
        return True
    return util.wait_until(idle, timeout=timeout, interval=0.5, what="no pf-admin process")


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def wait(seconds):
    time.sleep(seconds)
