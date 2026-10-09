#!/bin/sh
# PF-A3.4 host orchestrator for the isolated real-Docker integration run (SPEC sections 2 and 6.1).
#
#   sh deploy/synology/integration/run-isolated.sh up        [--run <id>]
#   sh deploy/synology/integration/run-isolated.sh harness   --run <id> -- <harness args>
#   sh deploy/synology/integration/run-isolated.sh collect   --run <id>
#   sh deploy/synology/integration/run-isolated.sh teardown  --run <id>
#   sh deploy/synology/integration/run-isolated.sh all       [-- <harness args>]
#   sh deploy/synology/integration/run-isolated.sh push      --run <id>   (development only: re-copy the inputs;
#                                                                        a run that used it is never evidence)
#
# Exit codes: 0 every requested scenario finished and the teardown diff is clean; 1 a scenario failed (evidence
# kept); 2 the environment could not be built or a host-safety check failed (BLOCKED); 3 the teardown/inventory
# diff failed.
#
# The host daemon is touched only through host_docker(), which runs host_inventory.py's allowlist guard (every
# argv checked and appended to host-commands.jsonl). Everything real happens inside the privileged dind container
# pfa34-<run>; the host /var/run/docker.sock is never mounted and no host Compose project is ever named.
set -u

native() {
    # git-bash: hand the host Python a native path (MSYS_NO_PATHCONV=1 disables the automatic conversion).
    if command -v cygpath > /dev/null 2>&1; then cygpath -m "$1"; else echo "$1"; fi
}
HERE=$(native "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)")
REPO=$(native "$(CDPATH= cd -- "$HERE/../../.." && pwd -P)")
PY=${PFA34_PYTHON:-python}
RAW_BASE=${PFA34_RAW_BASE:-D:/.claude-tmp/part-flow/ops/PF-A3.4}
INVENTORY="$HERE/host_inventory.py"
DIND_TAG=docker:28.5.1-dind

IN_UP=0
blocked() {
    echo "BLOCKED: $*" >&2
    if [ "$IN_UP" = 1 ]; then
        # Never leave a half-built environment: tear down whatever this run created, then prove the inventory.
        IN_UP=0
        do_teardown
    fi
    exit 2
}

case "$(uname -s 2> /dev/null)" in
    MINGW*|MSYS*|CYGWIN*)
        [ "${MSYS_NO_PATHCONV:-}" = 1 ] || blocked "set MSYS_NO_PATHCONV=1 under git-bash" ;;
esac
case "$RAW_BASE" in
    [Bb]:*|/b/*|/B/*) blocked "the run directory must not be on B:" ;;
esac
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2> /dev/null \
    || blocked "host Python 3.9+ not found (set PFA34_PYTHON)"

VERB=${1:-}
[ -n "$VERB" ] && shift
RUN=
while [ "$#" -gt 0 ]; do
    case "$1" in
        --run) RUN=${2:-}; shift 2 ;;
        --) shift; break ;;
        *) blocked "unknown argument $1" ;;
    esac
done

new_run() {
    stamp=$(date -u +%Y%m%dT%H%M%SZ)
    suffix=$(od -An -tx1 -N4 /dev/urandom | tr -d ' \n')
    echo "$stamp-$suffix"
}

raw_dir() {
    echo "$RAW_BASE/run-$RUN"
}

inv() {
    "$PY" "$INVENTORY" --run "$RUN" --raw "$(raw_dir)" "$@"
}

host_docker() {
    inv docker -- "$@"
}

state_get() {
    inv state-get "$1" 2> /dev/null
}

state_set() {
    inv state-set "$@"
}

NAME() {
    echo "pfa34-$RUN"
}

vhdx_size() {
    "$PY" -c 'import os, sys
path = os.path.expandvars(r"%LOCALAPPDATA%\Docker\wsl\disk\docker_data.vhdx") if os.name == "nt" else ""
print(os.path.getsize(path) if path and os.path.exists(path) else "unknown")'
}

do_up() {
    [ -n "$RUN" ] || RUN=$(new_run)
    RAW=$(raw_dir)
    mkdir -p "$RAW" || blocked "cannot create $RAW"
    echo "run: $RUN (raw: $RAW)"
    echo "$RUN" > "$RAW_BASE/last-run"
    os=$(host_docker info --format '{{.OperatingSystem}}|{{.OSType}}') || blocked "docker info failed"
    case "$os" in
        "Docker Desktop|linux") ;;
        *) blocked "the host daemon is not Docker Desktop's Linux engine ($os)" ;;
    esac
    host_docker version --format '{{json .}}' > "$RAW/host-docker-version.json" || blocked "docker version failed"
    host_docker info --format '{{json .}}' > "$RAW/host-docker-info.json" || blocked "docker info failed"
    state_set vhdx_before "$(vhdx_size)"
    inv snapshot --out "$RAW/host-inventory-before.json" || blocked "inventory snapshot failed"
    IN_UP=1

    present=$(host_docker images --no-trunc --format '{{.Repository}}:{{.Tag}}' | grep -cx "$DIND_TAG")
    if [ "$present" -eq 0 ]; then
        state_set dind_present_before false --json
        state_set pulled_by_run true --json
        host_docker pull "$DIND_TAG" > "$RAW/dind-pull.log" 2>&1 || blocked "pull $DIND_TAG failed"
    else
        state_set dind_present_before true --json
        state_set pulled_by_run false --json
    fi
    digest=$(host_docker image inspect --format '{{index .RepoDigests 0}}' "$DIND_TAG") || blocked "no dind digest"
    image_id=$(host_docker image inspect --format '{{.Id}}' "$DIND_TAG") || blocked "no dind image id"
    state_set dind_ref "$DIND_TAG@${digest#*@}"
    state_set dind_image_id "$image_id"

    head=$(git -C "$REPO" rev-parse HEAD) || blocked "git rev-parse failed"
    state_set repo_head "$head"
    if [ ! -d "$RAW/source.git" ]; then
        git clone --quiet --bare --no-local "$REPO" "$RAW/source.git" || blocked "scratch clone failed"
    fi
    pins=
    [ -f "$RAW_BASE/base-images.json" ] && pins="--base-images $RAW_BASE/base-images.json"
    # shellcheck disable=SC2086 # optional option pair
    tree=$(inv pack --repo "$REPO" --source-git "$RAW/source.git" --out "$RAW/inputs.tar" $pins) || blocked "pack failed"
    state_set inputs_tree_sha256 "$tree"

    host_docker network create --label "io.partflow.pfa34.run=$RUN" "$(NAME)" > /dev/null \
        || blocked "network create failed"
    state_set network_created true --json
    host_docker run -d --privileged --name "$(NAME)" --label "io.partflow.pfa34.run=$RUN" --network "$(NAME)" \
        --mount "type=volume,dst=/pfa34,volume-label=io.partflow.pfa34.run=$RUN" -e DOCKER_TLS_CERTDIR= \
        --entrypoint /bin/sh "$(state_get dind_ref)" -c 'trap "exit 0" TERM; while :; do sleep 3600; done' \
        > /dev/null || blocked "dind container start failed"
    state_set container_created true --json
    cid=$(host_docker inspect --format '{{.Id}}' "$(NAME)") || blocked "inspect of the dind container failed"
    anon=$(host_docker inspect --format '{{range .Mounts}}{{if eq .Destination "/var/lib/docker"}}{{.Name}}{{end}}{{end}}' "$cid")
    state_set anonymous_volumes "[\"$anon\"]" --json
    labelled=$(host_docker inspect --format '{{range .Mounts}}{{if eq .Destination "/pfa34"}}{{.Name}}{{end}}{{end}}' "$cid")
    state_set labelled_volume "$labelled"

    host_docker exec "$(NAME)" mkdir -p /pfa34/work /pfa34/evidence || blocked "exec mkdir failed"
    inv docker --stdin-file "$RAW/inputs.tar" -- cp - "$(NAME):/pfa34/work" || blocked "copy of the inputs failed"
    host_docker exec -d "$(NAME)" /pfa34/work/dind-entry.sh || blocked "supervisor start failed"
    waited=0
    while :; do
        word=$(host_docker exec "$(NAME)" cat /run/pfa34/state 2> /dev/null)
        case "$word" in
            supervising) break ;;
            blocked:*|failed:*) blocked "dind provisioning: $word" ;;
        esac
        [ "$waited" -lt 900 ] || blocked "dind provisioning did not finish in 900 s (state: $word)"
        sleep 3
        waited=$((waited + 3))
    done
    waited=0
    until host_docker exec "$(NAME)" docker info --format '{{.ID}}' > /dev/null 2>&1; do
        [ "$waited" -lt 180 ] || blocked "dind cannot start dockerd"
        sleep 2
        waited=$((waited + 2))
    done
    IN_UP=0
    echo "up: $RUN ready (dind $(state_get dind_ref))"
}

do_push() {
    [ -n "$RUN" ] || blocked "--run is required"
    RAW=$(raw_dir)
    pins=
    [ -f "$RAW_BASE/base-images.json" ] && pins="--base-images $RAW_BASE/base-images.json"
    # shellcheck disable=SC2086 # optional option pair
    inv pack --repo "$REPO" --source-git "$RAW/source.git" --out "$RAW/inputs.tar" $pins > /dev/null || blocked "pack failed"
    inv docker --stdin-file "$RAW/inputs.tar" -- cp - "$(NAME):/pfa34/work" || blocked "copy of the inputs failed"
    state_set pushed_after_up true --json
    echo "pushed (development only; this run is no longer evidence)"
}

do_harness() {
    [ -n "$RUN" ] || blocked "--run is required"
    RAW=$(raw_dir)
    log="$RAW/harness-$(date -u +%Y%m%dT%H%M%SZ).log"
    inv docker --stream -- exec "$(NAME)" env PYTHONPATH=/pfa34/work/deploy/synology/integration \
        python3 -B -m harness --root /srv/pfa34 --evidence /pfa34/evidence --run "$RUN" "$@" > "$log" 2>&1
    status=$?
    tail -n 40 "$log"
    echo "harness exit $status (log: $log)"
    [ "$status" -eq 0 ] && return 0
    [ "$status" -eq 2 ] && return 2
    return 1
}

do_collect() {
    [ -n "$RUN" ] || blocked "--run is required"
    RAW=$(raw_dir)
    rm -rf "$RAW/evidence"
    host_docker cp "$(NAME):/pfa34/evidence" "$RAW/evidence" || return 1
    echo "collected: $RAW/evidence"
}

do_teardown() {
    [ -n "$RUN" ] || blocked "--run is required"
    RAW=$(raw_dir)
    status=0
    if [ "$(state_get container_created)" = true ]; then
        host_docker exec "$(NAME)" /pfa34/work/dind-entry.sh teardown > "$RAW/inner-teardown.txt" 2>&1
        inner=$?
        cat "$RAW/inner-teardown.txt"
        [ "$inner" -eq 0 ] || { echo "inner teardown exit $inner (VM-level leak reported)"; status=3; }
        host_docker rm -f -v "$(NAME)" > /dev/null || status=3
    fi
    if [ "$(state_get network_created)" = true ]; then
        host_docker network rm "$(NAME)" > /dev/null || status=3
    fi
    if [ "$(state_get pulled_by_run)" = true ] && [ -n "$(state_get dind_image_id)" ]; then
        host_docker image rm "$(state_get dind_image_id)" > /dev/null || status=3
    fi
    state_set vhdx_after "$(vhdx_size)"
    inv snapshot --out "$RAW/host-inventory-after.json" || return 2
    inv diff --before "$RAW/host-inventory-before.json" --after "$RAW/host-inventory-after.json" \
        --out "$RAW/host-inventory-diff.json" || status=3
    echo "teardown: $RUN status $status"
    return "$status"
}

case "$VERB" in
    up) do_up ;;
    push) do_push ;;
    harness) do_harness "$@" ;;
    collect) do_collect ;;
    teardown) do_teardown ;;
    all)
        do_up
        trap 'do_teardown; exit 2' INT TERM
        do_harness "$@"
        result=$?
        do_collect
        trap - INT TERM
        do_teardown
        teardown_status=$?
        [ "$teardown_status" -eq 0 ] || exit 3
        exit "$result"
        ;;
    *) blocked "usage: run-isolated.sh up|harness|collect|teardown|all|push [--run <id>] [-- <harness args>]" ;;
esac
