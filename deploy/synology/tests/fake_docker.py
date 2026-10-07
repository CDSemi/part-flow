"""Offline fake Docker daemon/CLI for the PF-A1.3 tests. It never contacts anything.

``protected_fixture.install_fake_docker`` copies this file into a protected tool directory,
sets the shebang to the absolute interpreter and embeds the directory below, then registers
it as the ``docker`` tool. State lives in ``<tooldir>/docker-state.json`` (the child
environment is sanitized, so nothing is read from it except what is recorded). Every
invocation is appended to ``<tooldir>/calls.jsonl``; for Compose invocations the recorded
``POSTGRES_DB`` and a SHA-256 of ``PARTFLOW_DATABASE_URL`` stand in for values (never the
password). Unknown argv exits 64; a request for a full container object fails the test.

The module is also imported by the tests (``render_model``) to build expected models.
"""
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tarfile

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
    def __init__(self, code=0, out="", err=""):
        self.code, self.out, self.err = code, out, err


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
        return Result(0, json.dumps([{"Id": image["id"], "RepoTags": image["repo_tags"]} for image in found]) + "\n")
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
        for wanted in positional_after(argv, 1):
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
    index = 0
    while index < len(argv) and argv[index].startswith("-"):
        option, value = argv[index], argv[index + 1]
        if option == "-f":
            files.append(value)
        elif option == "-p":
            project = value
        index += 2
    rest = argv[index:]
    verb = rest[0] if rest else ""
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
    if verb == "exec":
        if "psql" in rest and "-c" in rest:
            statement = rest[rest.index("-c") + 1]
            return Result(0, settings.get("psql", {}).get(statement, "") + "\n")
        return Result(0, settings.get("exec_output", ""))
    return Result(0)


def main(argv):
    env = dict(os.environ)
    state = load_state()
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
    return result.code


def apply_state_patch(state, patch):
    """{"op": "append"|"remove"|"update", "list": name, "match": {...}, "value"/"set": {...}}, or
    {"op": "set", "key": name, "value": ...} for a top-level state field (e.g. a new ``info``)."""
    if patch["op"] == "set":
        state[patch["key"]] = patch["value"]
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
