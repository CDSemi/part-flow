"""Offline fake Docker daemon/CLI for the PF-A1.3 tests. It never contacts anything.

``protected_fixture.install_fake_docker`` copies this file into a protected tool directory,
sets the shebang to the absolute interpreter and embeds the directory below, then registers
it as the ``docker`` tool. State lives in ``<tooldir>/docker-state.json`` (the child
environment is sanitized, so nothing is read from it except what is recorded). Every
invocation is appended to ``<tooldir>/calls.jsonl``; for Compose invocations the recorded
``POSTGRES_DB`` and a SHA-256 of ``PARTFLOW_DATABASE_URL`` stand in for values (never the
password). Unknown argv exits 64; a request for a full container object fails the test.

The module is also imported by the tests (``render_model``) to build expected models.

PF-A3.2: an optional simulated application plane (``state["plane"]``, absent in every earlier test) lets a lifecycle
command run end to end through the installed launcher: per-service ``compose ps -a -q``, ``inspect`` of one
container (Image, State, Config.Env of the db service only), ``compose stop|up|build|run`` changing containers and
images, and the database programs of the db service (``psql`` statements, ``pg_dump``/``pg_restore`` of a JSON
model, ``createdb``/``dropdb``, the Alembic upgrade setting a database's heads to the backend image's contract).
``block`` gains ``env`` and ``argv_match`` selectors and ``apply`` ("after": the call's state change is saved before
it blocks, i.e. committed then lost; "before": it is saved only if the call wakes up). Still no daemon is contacted.
"""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import tarfile
import time

STATE_DIR = None  # replaced when the tool is installed


def escape(mode):
    if mode == "doubled":
        return lambda value: value.replace("$", "$$")
    return lambda value: value


def substitute(value, env, project, mode):
    esc = escape(mode)
    if isinstance(value, dict):
        return {key: substitute(item, env, project, mode) for key, item in value.items()}
    if isinstance(value, list):
        return [substitute(item, env, project, mode) for item in value]
    if isinstance(value, str):
        value = value.replace("{{PROJECT}}", project)
        while "{{ENV:" in value:
            start = value.index("{{ENV:")
            end = value.index("}}", start)
            name = value[start + 6:end]
            value = value[:start] + esc(env.get(name, "")) + value[end + 2:]
        return value
    return value


def apply_patch(model, patch):
    path = list(patch["path"])
    target = model
    for part in path[:-1]:
        target = target[part]
    if patch["op"] == "set":
        target[path[-1]] = patch["value"]
    elif patch["op"] == "delete":
        del target[path[-1]]
    elif patch["op"] == "append":
        target[path[-1]].append(patch["value"])
    else:
        raise ValueError(patch["op"])


def render_model(fixture_path, env, project, *, mode=None, healthcheck_mode=None, override_text=None, patches=()):
    document = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
    mode = mode or document["escape_mode"]
    model = substitute(document["model"], env, project, mode)
    if (healthcheck_mode or document["escape_mode"]) == "literal":
        test = model["services"]["db"]["healthcheck"]["test"]
        model["services"]["db"]["healthcheck"]["test"] = [item.replace("$${", "${") for item in test]
    if override_text is not None:
        service = None
        for line in override_text.splitlines():
            if line.startswith("  ") and not line.startswith("    "):
                service = line.strip().rstrip(":")
            elif line.startswith("    image: ") and service in model["services"]:
                model["services"][service]["image"] = json.loads(line[len("    image: "):])
    for patch in patches:
        apply_patch(model, patch)
    return model


# ------------------------------------------------------------------------- the CLI


def state_path():
    return Path(STATE_DIR) / "docker-state.json"


def load_state():
    return json.loads(state_path().read_text(encoding="utf-8"))


def save_state(state):
    temporary = state_path().with_name("docker-state.json.tmp")
    temporary.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(str(temporary), str(state_path()))


def label_filter(argv):
    """[(key, value)] of every ``--filter label=K=V`` (value None for ``label=K``)."""
    result = []
    for index, word in enumerate(argv):
        if word == "--filter" and index + 1 < len(argv) and argv[index + 1].startswith("label="):
            key, _, value = argv[index + 1][len("label="):].partition("=")
            result.append((key, value if _ else None))
    return result


def matches(labels, filters):
    return all(key in labels and (value is None or labels[key] == value) for key, value in filters)


def positional_after(argv, start, takes_value=("--format", "--filter", "-o", "-i", "--time")):
    words = []
    index = start
    while index < len(argv):
        word = argv[index]
        if word in takes_value:
            index += 2
            continue
        if not word.startswith("-"):
            words.append(word)
        index += 1
    return words


def labels_text(labels):
    return ",".join(f"{key}={value}" for key, value in sorted((labels or {}).items()))


class Result:
    def __init__(self, code=0, out="", err="", sleep=0):
        self.code, self.out, self.err, self.sleep = code, out, err, sleep


def violation(state, text):
    state.setdefault("violations", []).append(text)
    return Result(70, "", "fake docker: test violation: " + text + "\n")


def find_image(state, reference):
    for image in state.get("images", []):
        if reference in image["repo_tags"] or reference == image["id"]:
            return image
    return None


def dispatch(state, argv, env):
    if not argv:
        return Result(64, "", "fake docker: empty argv\n")
    verb = argv[0]
    if argv == ["info", "--format", "{{json .}}"]:
        if state.get("info_exit"):
            return Result(state["info_exit"], "", state.get("info_stderr", "Cannot connect to the Docker daemon") + "\n")
        if "info_raw" in state:
            return Result(0, state["info_raw"] + "\n")
        return Result(0, json.dumps(state["info"]) + "\n")
    if verb == "version":
        return Result(0, "28.0.0\n")
    if verb == "ps":
        filters = label_filter(argv)
        rows = []
        for container in state.get("containers", []):
            if "-a" not in argv and container.get("status") != "running":
                continue
            if matches(container.get("labels") or {}, filters):
                rows.append(container["id"])
        return Result(0, "".join(row + "\n" for row in rows))
    if verb == "inspect" and "plane" in state and "--format" not in argv:
        return plane_inspect(state, positional_after(argv, 1))
    if verb == "container" and argv[1:2] == ["inspect"] or verb == "inspect":
        if "--format" not in argv:
            return violation(state, "full container object requested: " + " ".join(argv))
        template = argv[argv.index("--format") + 1]
        if "Config.Env" in template:
            return violation(state, "Config.Env requested")
        ids = positional_after(argv, 2 if verb == "container" else 1)
        lines = []
        for wanted in ids:
            found = [c for c in state.get("containers", []) if c["id"] == wanted]
            if not found:
                return Result(1, "", f"Error: No such container: {wanted}\n")
            data = {key: value for key, value in found[0].items() if key != "status_only"}
            lines.append(json.dumps(data))
        return Result(0, "\n".join(lines) + "\n")
    if verb in ("volume", "network") and argv[1:2] == ["ls"]:
        filters = label_filter(argv)
        rows = []
        for item in state.get(verb + "s", []):
            if not matches(item.get("labels") or {}, filters):
                continue
            if verb == "volume":
                rows.append({"Name": item["name"], "Driver": item["driver"], "Scope": item["scope"],
                             "Labels": labels_text(item.get("labels"))})
            else:
                rows.append({"ID": item["id"], "Name": item["name"], "Driver": item["driver"], "Scope": item["scope"],
                             "Labels": labels_text(item.get("labels"))})
        return Result(0, "".join(json.dumps(row) + "\n" for row in rows))
    if verb in ("volume", "network") and argv[1:2] == ["inspect"]:
        if "--format" not in argv:
            return violation(state, verb + " inspect without --format")
        lines = []
        for wanted in positional_after(argv, 2):
            found = [item for item in state.get(verb + "s", []) if item["name"] == wanted or item.get("id") == wanted]
            if len(found) != 1:
                return Result(1, "", f"Error: No such {verb}: {wanted}\n")
            lines.append(json.dumps(found[0]))
        return Result(0, "\n".join(lines) + "\n")
    if verb in ("volume", "network") and argv[1:2] == ["rm"]:
        if any(word.startswith("-") for word in argv[2:]):
            return violation(state, verb + " rm with an option")
        for wanted in argv[2:]:
            items = state.get(verb + "s", [])
            found = [item for item in items if item["name"] == wanted or item.get("id") == wanted]
            if len(found) != 1:
                return Result(1, "", f"Error: No such {verb}: {wanted}\n")
            name = found[0]["name"]
            for container in state.get("containers", []):
                in_use = any(mount.get("Type") == "volume" and mount.get("Name") == name
                             for mount in container.get("mounts") or []) if verb == "volume" \
                    else name in (container.get("networks") or {})
                if in_use:
                    return Result(1, "", f"Error: {verb} {name} is in use\n")
            items.remove(found[0])
            if verb == "volume" and "plane" in state and name == state["plane"].get("data_volume"):
                state["plane"]["databases"] = {}  # the cluster's data lived in this volume
        return Result(0, "".join(word + "\n" for word in argv[2:]))
    if verb == "image" and argv[1:2] == ["ls"]:
        filters = label_filter(argv)
        rows = []
        for image in state.get("images", []):
            if not matches(image.get("labels") or {}, filters):
                continue
            for tag in image["repo_tags"]:
                repository, _, name = tag.rpartition(":")
                rows.append({"Repository": repository, "Tag": name, "ID": image["id"]})
        return Result(0, "".join(json.dumps(row) + "\n" for row in rows))
    if verb == "image" and argv[1:2] == ["inspect"]:
        references = positional_after(argv, 2)
        found = []
        for reference in references:
            image = find_image(state, reference)
            if image is None:
                return Result(1, "", f"Error: No such image: {reference}\n")
            found.append(image)
        if "--format" in argv:
            return Result(0, "".join(json.dumps({"id": image["id"], "repo_tags": image["repo_tags"],
                                                 "labels": image.get("labels")}) + "\n" for image in found))
        # PF-A3.1: the platform and digest fields a $defs.image identity reads.
        return Result(0, json.dumps([{"Id": image["id"], "RepoTags": image["repo_tags"],
                                      "Os": image.get("os", "linux"), "Architecture": image.get("architecture", "amd64"),
                                      "RepoDigests": image.get("repo_digests", [])} for image in found]) + "\n")
    if verb == "image" and argv[1:2] == ["rm"]:
        if any(word in ("-f", "--force") for word in argv):
            return violation(state, "image rm with force")
        for reference in argv[2:]:
            image = find_image(state, reference)
            if image is None:
                return Result(1, "", f"Error: No such image: {reference}\n")
            if reference in image["repo_tags"]:
                image["repo_tags"].remove(reference)
            if not image["repo_tags"]:
                state["images"].remove(image)
        return Result(0, "".join("Untagged: " + reference + "\n" for reference in argv[2:]))
    if verb == "tag" or (verb == "image" and argv[1:2] == ["tag"]):
        source, target = (argv[1], argv[2]) if verb == "tag" else (argv[2], argv[3])
        image = find_image(state, source)
        if image is None:
            return Result(1, "", f"Error: No such image: {source}\n")
        for other in state.get("images", []):
            if target in other["repo_tags"]:
                other["repo_tags"].remove(target)
        image["repo_tags"].append(target)
        return Result(0)
    if verb == "image" and argv[1:2] == ["save"]:
        output = argv[argv.index("-o") + 1]
        references = positional_after(argv, 2)
        manifest = []
        for reference in references:
            image = find_image(state, reference)
            if image is None:
                return Result(1, "", f"Error: No such image: {reference}\n")
            manifest.append({"RepoTags": [reference], "Config": image["id"]})
        data = json.dumps(manifest).encode("utf-8")
        with tarfile.open(output, "w") as archive:
            info = tarfile.TarInfo("manifest.json")
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
        return Result(0)
    if verb == "image" and argv[1:2] == ["load"]:
        if "plane" in state:
            return plane_load(state, argv[argv.index("-i") + 1])
        return Result(0, "Loaded image\n")
    if verb == "rm":
        if "-f" not in argv:
            return Result(64, "", "fake docker: rm without -f\n")
        for wanted in positional_after(argv, 1):
            containers = state.get("containers", [])
            found = [c for c in containers if c["id"] == wanted]
            if not found:
                return Result(1, "", f"Error: No such container: {wanted}\n")
            containers.remove(found[0])
        return Result(0)
    if verb == "stop":
        failures = state.get("compose", {}).get("stop_fail", {})  # PF-A1.4 EH-5: a vanished --rm one-off
        for wanted in positional_after(argv, 1):
            if wanted in failures:
                return Result(1, "", failures[wanted] + "\n")
            for container in state.get("containers", []):
                if container["id"] == wanted:
                    container["status"] = "exited"
        return Result(0)
    if verb == "compose":
        return compose(state, argv[1:], env)
    return Result(64, "", "fake docker: unknown argv\n")


def compose(state, argv, env):
    settings = state.setdefault("compose", {})
    files = []
    project = None
    directory = None
    index = 0
    while index < len(argv) and argv[index].startswith("-"):
        option, value = argv[index], argv[index + 1]
        if option == "-f":
            files.append(value)
        elif option == "-p":
            project = value
        elif option == "--project-directory":
            directory = value
        index += 2
    rest = argv[index:]
    verb = rest[0] if rest else ""
    if "plane" in state and verb in ("stop", "up", "build", "run", "exec") or "plane" in state and verb == "ps" \
            and "-q" in rest:
        return plane_compose(state, verb, rest[1:], project=project, directory=directory, files=files, env=env)
    if verb == "version":
        return Result(0, settings.get("version", "Docker Compose version v2.40.2-fixture") + "\n")
    if verb == "config" and rest[1:] == ["--format", "json"]:
        if settings.get("render_exit"):
            return Result(settings["render_exit"], "", "unknown flag: --format\n")
        if settings.get("render_raw") is not None:
            return Result(0, settings["render_raw"])
        if settings.get("render_size"):
            return Result(0, "x" * settings["render_size"])
        override_text = Path(files[1]).read_text(encoding="utf-8") if len(files) > 1 else None
        model = render_model(settings["fixture"], env, project, mode=settings.get("escape_mode"),
                             healthcheck_mode=settings.get("healthcheck_mode"), override_text=override_text,
                             patches=settings.get("render_patch", ()))
        return Result(0, json.dumps(model, ensure_ascii=False))
    if verb == "config":
        return Result(0)
    if verb in settings.get("fail_verbs", []):
        return Result(1, "", f"fake compose {verb} failed\n")
    if verb == "run":
        return Result(0, settings.get("run_output", ""))
    if verb == "ps":
        return Result(0, settings.get("ps", "NAME  STATUS\n"))
    if verb == "logs":
        # PF-A1.4: `logs_sleep` keeps a followed child running after its output (bound tests).
        return Result(0, settings.get("logs", ""), sleep=settings.get("logs_sleep", 0))
    if verb == "exec":
        if "psql" in rest and "-c" in rest:
            statement = rest[rest.index("-c") + 1]
            return Result(0, settings.get("psql", {}).get(statement, "") + "\n")
        return Result(0, settings.get("exec_output", ""))
    return Result(0)


def main(argv):
    env = dict(os.environ)
    state = load_state()
    original = copy.deepcopy(state)
    calls_path = Path(STATE_DIR) / "calls.jsonl"
    entry = {"argv": argv, "DOCKER_HOST": env.get("DOCKER_HOST"), "DOCKER_CONFIG": env.get("DOCKER_CONFIG"),
             "has_DOCKER_CONTEXT": "DOCKER_CONTEXT" in env, "env_keys": sorted(env)}
    if argv[:1] == ["compose"]:
        entry["POSTGRES_DB"] = env.get("POSTGRES_DB")
        entry["database_url_sha256"] = hashlib.sha256(env.get("PARTFLOW_DATABASE_URL", "").encode("utf-8")).hexdigest()
    with calls_path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(entry) + "\n")
    number = sum(1 for _ in calls_path.open(encoding="utf-8"))
    result = dispatch(state, argv, env)
    probe_lock(state, argv)
    block = state.get("block")
    deferred = None
    if block and all(word in argv for word in block["argv_contains"]) \
            and all(env.get(key) == value for key, value in block.get("env", {}).items()) \
            and re.search(block.get("argv_match", ""), " ".join(argv)):
        # PF-A1.4 audit: one blocking child for a real-signal test; later calls answer at once.
        del state["block"]
        Path(block["marker"]).write_text(str(os.getpid()) + "\n", encoding="utf-8")
        result.sleep = block["seconds"]
        if block.get("apply", "after") == "before":
            # PF-A3.2: the call's change happens only if it wakes up (a kill while it blocks loses it).
            deferred, state = state, original
            del state["block"]
    for hook in state.get("hooks", []):
        prefix = hook.get("after_argv_prefix")
        fired = hook.get("after_call_n") == number
        if prefix is not None and argv[:len(prefix)] == prefix and not hook.get("fired"):
            hook["seen"] = hook.get("seen", 0) + 1
            fired = hook["seen"] == hook.get("nth", 1)
        if fired:
            hook["fired"] = True
            for patch in hook["mutate"]:
                apply_state_patch(state, patch)
    save_state(state)
    sys.stdout.write(result.out)
    sys.stderr.write(result.err)
    if result.sleep:
        sys.stdout.flush()
        time.sleep(result.sleep)
    if deferred is not None:
        save_state(deferred)
    return result.code


# ------------------------------------------------------------------------- PF-A3.2 plane

SERVICE_LABEL = "com.docker.compose.service"
PROJECT_LABEL = "com.docker.compose.project"
DUMP_MAGIC = "PFDUMP1 "


def service_of(container):
    return (container.get("labels") or {}).get(SERVICE_LABEL)


def plane_containers(state, project, service):
    return [item for item in state.get("containers", []) if service_of(item) == service
            and (item.get("labels") or {}).get(PROJECT_LABEL) == project
            and (item.get("labels") or {}).get("com.docker.compose.oneoff", "False") == "False"]


def plane_inspect(state, ids):
    """``docker inspect <id>`` (the controller's inspect()): Image, State and, for the db service only, Config.Env."""
    plane = state["plane"]
    objects = []
    for wanted in ids:
        found = [item for item in state.get("containers", []) if item["id"] == wanted]
        if not found:
            return Result(1, "", f"Error: No such container: {wanted}\n")
        container = found[0]
        service = service_of(container)
        running = container.get("status") == "running"
        health = "healthy"
        sequence = plane.setdefault("health", {}).get(service)
        if running and sequence:
            health = sequence.pop(0) if len(sequence) > 1 else sequence[0]
        objects.append({"Image": container["image"], "State": {"Running": running, "Health": {"Status": health}},
                        "Config": {"Env": list(plane.get("db_env", [])) if service == "db" else []}})
    return Result(0, json.dumps(objects) + "\n")


def plane_load(state, path):
    """``docker image load -i <tar>``: every manifest entry's image ID with its tags (an image save of this fake)."""
    import tarfile
    with tarfile.open(path) as archive:
        manifest = json.load(archive.extractfile("manifest.json"))
    for entry in manifest:
        image = find_image(state, entry["Config"])
        if image is None:
            image = {"id": entry["Config"], "repo_tags": [], "labels": entry.get("Labels") or {}}
            state.setdefault("images", []).append(image)
        for tag in entry.get("RepoTags") or []:
            for other in state["images"]:
                if tag in other["repo_tags"] and other is not image:
                    other["repo_tags"].remove(tag)
            if tag not in image["repo_tags"]:
                image["repo_tags"].append(tag)
    return Result(0, "Loaded image\n")


def override_images(files):
    """{service: reference} of the image override (the second -f file), as render_model reads it."""
    result = {}
    if len(files) < 2:
        return result
    service = None
    for line in Path(files[1]).read_text(encoding="utf-8").splitlines():
        if line.startswith("  ") and not line.startswith("    "):
            service = line.strip().rstrip(":")
        elif line.startswith("    image: "):
            result[service] = json.loads(line[len("    image: "):])
    return result


def contract_of(directory):
    """The image contract a backend build of ``directory`` carries: pf-admin's migration_files() digests and the
    Alembic heads of the fixture revision files (``revision='x'``/``down_revision='y'``)."""
    base = Path(directory) / "backend"
    files = [base / "alembic.ini"] + [path for path in (base / "alembic").rglob("*") if path.is_file()
                                       and "__pycache__" not in path.parts and path.suffix != ".pyc"]
    revisions, parents = set(), set()
    for path in (base / "alembic" / "versions").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        revisions.update(re.findall(r"^revision\s*=\s*['\"]([^'\"]+)['\"]", text, re.M))
        parents.update(re.findall(r"^down_revision\s*=\s*['\"]([^'\"]+)['\"]", text, re.M))
    return {"files": {str(path.relative_to(base)): hashlib.sha256(path.read_bytes()).hexdigest()
                      for path in sorted(files)},
            "heads": sorted(revisions - parents)}


def new_database(plane, owner, locale=None):
    return {"heads": [], "rows": {}, "allow": True, "owner": owner,
            "locale": list(locale or plane.get("locale", ["UTF8", "C.UTF-8", "C.UTF-8"]))}


def plane_compose(state, verb, words, *, project, directory, files, env):
    plane = state["plane"]
    if verb == "ps":
        services = [word for word in words if not word.startswith("-")]
        ids = [item["id"] for service in services for item in plane_containers(state, project, service)
               if "-a" in words or item.get("status") == "running"]
        return Result(0, "".join(item + "\n" for item in ids))
    if verb == "stop":
        for service in [word for word in words if not word.startswith("-")]:
            for item in plane_containers(state, project, service):
                item["status"] = "exited"
        return Result(0)
    if verb == "up":
        service = words[-1]
        found = plane_containers(state, project, service)
        if not found:
            template = copy.deepcopy(plane["templates"]["containers"][service])
            state.setdefault("containers", []).append(template)
            found = [template]
            for kind in ("volumes", "networks"):
                for item in plane["templates"].get(kind, []):
                    if not [other for other in state.setdefault(kind, []) if other["name"] == item["name"]]:
                        state[kind].append(copy.deepcopy(item))
        reference = override_images(files).get(service)
        if reference is not None:
            image = find_image(state, reference)
            if image is None:
                return Result(1, "", f"Error: No such image: {reference}\n")
            found[0]["image"] = image["id"]
        found[0]["status"] = "running"
        if service == "db" and not plane.get("databases"):
            plane["databases"] = {env.get("POSTGRES_DB"): new_database(plane, env.get("POSTGRES_USER"))}
        return Result(0)
    if verb == "build":
        service = words[-1]
        reference = override_images(files)[service]
        image = find_image(state, reference)
        if image is None:
            image_id = "sha256:" + hashlib.sha256((reference + "\0" + str(directory)).encode("utf-8")).hexdigest()
            image = {"id": image_id, "repo_tags": [reference], "labels": dict(plane.get("build_labels", {}))}
            state.setdefault("images", []).append(image)
        if service == "backend":
            image["contract"] = contract_of(directory)
        return Result(0)
    if verb == "run":
        index = 0
        while index < len(words) and words[index].startswith("-"):
            index += 2 if words[index] == "--label" else 1
        service, command = words[index], words[index + 1:]
        reference = override_images(files).get(service)
        if reference is not None:
            image = find_image(state, reference)
        else:
            running = plane_containers(state, project, service)
            image = find_image(state, running[0]["image"]) if running else None
        if image is None or "contract" not in image:
            return Result(1, "", "fake plane: the backend image has no contract\n")
        if "python" in command:
            return Result(0, json.dumps(image["contract"]) + "\n")
        if command[-3:] == ["alembic", "upgrade", "head"]:
            database = plane["databases"].get(env.get("POSTGRES_DB"))
            if database is None:
                return Result(1, "", "FATAL: database does not exist\n")
            database["heads"] = list(image["contract"]["heads"])
            return Result(0, "INFO  [alembic] upgrade\n")
        return Result(64, "", "fake plane: unknown run command\n")
    # exec -T <service> <program> ...
    service, program, arguments = words[1], words[2], words[3:]
    if service == "frontend":
        return Result(0, json.dumps(plane.get("api_health", {"status": "ok", "database": "connected"})) + "\n")
    databases = plane.setdefault("databases", {})

    def option(name):
        return arguments[arguments.index(name) + 1] if name in arguments else None

    if program == "psql":
        settings = state.get("compose", {}).get("psql", {})
        statement = option("-c")
        if statement in settings:
            return Result(0, settings[statement] + "\n")
        return plane_sql(state, option("-d"), statement)
    if program == "pg_dump":
        database = databases.get(option("-d"))
        if database is None:
            return Result(1, "", "pg_dump: error: database does not exist\n")
        return Result(0, DUMP_MAGIC + json.dumps({"heads": database["heads"], "rows": database["rows"]}) + "\n")
    if program == "pg_restore":
        data = sys.stdin.buffer.read().decode("utf-8", "replace")
        if "--list" in arguments:
            return Result(0, "; fake archive list\n")
        database = databases.get(option("-d"))
        if database is None:
            return Result(1, "", "pg_restore: error: database does not exist\n")
        if data.startswith(DUMP_MAGIC):
            dump = json.loads(data[len(DUMP_MAGIC):])
        else:  # a dump this plane did not write (an in-process fixture's): the configured default model
            dump = plane.get("foreign_dump", {"heads": ["r1"], "rows": {}})
        database.update(heads=list(dump["heads"]), rows=dict(dump["rows"]))
        return Result(0)
    if program == "createdb":
        name = arguments[-1]
        if name in databases:
            return Result(1, "", f'createdb: error: database "{name}" already exists\n')
        locale = [value.split("=", 1)[1] for prefix in ("--encoding=", "--lc-collate=", "--lc-ctype=")
                  for value in arguments if value.startswith(prefix)]
        databases[name] = new_database(plane, option("-U"), locale if len(locale) == 3 else None)
        return Result(0)
    if program == "dropdb":
        name = arguments[-1]
        if databases.pop(name, None) is None:
            return Result(1, "", f'dropdb: error: database "{name}" does not exist\n')
        return Result(0)
    if program == "pg_dumpall":
        return Result(0, "-- fake globals\n")
    return Result(0, state.get("compose", {}).get("exec_output", ""))


def plane_sql(state, name, statement):
    """One psql statement of the controller (the fixed set it issues) against the plane's databases."""
    plane = state["plane"]
    databases = plane["databases"]
    if name != "postgres" and name not in databases:
        return Result(2, "", f'psql: error: FATAL:  database "{name}" does not exist\n')
    database = databases.get(name)
    if statement == "SELECT 1;":
        return Result(0, "1\n")
    if statement == "SHOW server_version_num;":
        return Result(0, plane.get("server_version_num", "160004") + "\n")
    if statement.startswith("SELECT to_regclass('public.alembic_version')"):
        return Result(0, ("t" if database["heads"] else "f") + "\n")
    if statement.startswith("SELECT version_num FROM public.alembic_version"):
        return Result(0, "".join(head + "\n" for head in sorted(database["heads"])))
    if statement.startswith("SELECT d.datname, pg_get_userbyid(d.datdba)"):
        return Result(0, "".join("|".join([key, item["owner"], *item["locale"], "t" if item["allow"] else "f"]) + "\n"
                                 for key, item in sorted(databases.items())))
    if statement.startswith("SELECT extname, extversion FROM pg_extension"):
        return Result(0, "plpgsql|1.0\n")
    if statement.startswith("SELECT n.nspname || '.' || c.relname"):
        return Result(0, "".join(f"{table}|{count}\n" for table, count in sorted(database["rows"].items())))
    if statement.startswith("SELECT rolname, rolsuper"):
        return Result(0, "partflow_staging|f|f|t|t|f|f\n")
    if statement.startswith("SELECT name FROM pg_available_extensions"):
        return Result(0, "plpgsql\n")
    if statement.startswith("SELECT count(*) FROM pg_stat_activity WHERE backend_type = 'client backend'"):
        return Result(0, str(plane.get("client_backends", 0)) + "\n")
    match = re.fullmatch(r"SELECT count\(\*\) FROM pg_stat_activity WHERE datname = '([A-Za-z0-9_]+)';", statement)
    if match:
        return Result(0, str(plane.get("sessions", {}).get(match.group(1), 0)) + "\n")
    match = re.fullmatch(r'ALTER DATABASE "([A-Za-z0-9_]+)" ALLOW_CONNECTIONS (true|false);', statement)
    if match:
        databases[match.group(1)]["allow"] = match.group(2) == "true"
        return Result(0, "ALTER DATABASE\n")
    if statement.startswith("BEGIN;"):
        changed = copy.deepcopy(databases)  # one transaction: every statement applies, or none
        for part in statement.split(";"):
            part = part.strip()
            rename = re.fullmatch(r'ALTER DATABASE "([A-Za-z0-9_]+)" RENAME TO "([A-Za-z0-9_]+)"', part)
            flag = re.fullmatch(r'ALTER DATABASE "([A-Za-z0-9_]+)" ALLOW_CONNECTIONS (true|false)', part)
            if rename:
                if rename.group(1) not in changed or rename.group(2) in changed:
                    return Result(3, "", "ERROR:  rename refused\n")
                changed[rename.group(2)] = changed.pop(rename.group(1))
            elif flag:
                changed[flag.group(1)]["allow"] = flag.group(2) == "true"
        plane["databases"] = changed
        return Result(0, "COMMIT\n")
    plane.setdefault("unknown_sql", []).append(statement[:200])
    return Result(3, "", "fake plane: unknown statement\n")


def probe_lock(state, argv):
    """``lock_probe`` = {"argv_contains": [...], "lock": path, "record": path}: on a matching call, record
    whether another process holds the instance lock right now ("held") or not ("free")."""
    probe = state.get("lock_probe")
    if not probe or not all(word in argv for word in probe["argv_contains"]):
        return
    import fcntl
    descriptor = os.open(probe["lock"], os.O_RDONLY | os.O_CLOEXEC)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        held = "free"
    except BlockingIOError:
        held = "held"
    finally:
        os.close(descriptor)
    with open(probe["record"], "a", encoding="utf-8") as stream:
        stream.write(" ".join(argv[-3:]) + " " + held + "\n")


def apply_state_patch(state, patch):
    """{"op": "append"|"remove"|"update", "list": name, "match": {...}, "value"/"set": {...}}, or
    {"op": "set", "key": name, "value": ...} for a top-level state field (e.g. a new ``info``), or
    {"op": "write_file", "path": ..., "text": ...}: an editor changes a fixture file (PF-A1.4 CLI-5)."""
    if patch["op"] == "set":
        state[patch["key"]] = patch["value"]
        return
    if patch["op"] == "write_file":
        Path(patch["path"]).write_text(patch["text"], encoding="utf-8")
        return
    items = state.setdefault(patch["list"], [])
    if patch["op"] == "append":
        items.append(patch["value"])
        return
    selected = [item for item in items if all(item.get(key) == value for key, value in patch["match"].items())]
    for item in selected:
        if patch["op"] == "remove":
            items.remove(item)
        else:
            item.update(patch["set"])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
