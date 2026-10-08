#!/bin/sh
# PartFlow production smoke check (P16-S3; DEPLOYMENT §3.1, OPERATIONS_RUNBOOK §5 step 8):
#   deploy/production/smoke.sh --release TAG [--env-file .env.production] [--project NAME] [--allow-accepted-schema]
# Run from the repository root of the release checkout. Read-only loopback checks through `web`
# (http://127.0.0.1:$PARTFLOW_HTTP_PORT), one line each `PASS|FAIL <id> <what>`. The two POST probes go to a path
# with no route, so nothing is written whatever the answer. Exit 0 = every check passed, 1 = a check failed,
# 2 = the smoke could not run. It never starts, stops, builds, removes or re-tags anything.
# The authorization, designated write/read-back and scan-focus checks of the runbook stay manual.
set -eu

usage() {
    echo "Usage: deploy/production/smoke.sh --release TAG [--env-file .env.production] [--project NAME]" \
        "[--allow-accepted-schema]"
}

cannot_run() {
    echo "smoke: $1 Nothing was checked." >&2
    exit 2
}

RELEASE=
ENV_FILE=.env.production
PROJECT=
ALLOW_ACCEPTED=
while [ "$#" -gt 0 ]; do
    case "$1" in
        --release|--env-file|--project)
            [ "$#" -ge 2 ] || { usage >&2; exit 2; }
            case "$1" in
                --release) RELEASE=$2 ;;
                --env-file) ENV_FILE=$2 ;;
                --project) PROJECT=$2 ;;
            esac
            shift 2
            ;;
        --allow-accepted-schema) ALLOW_ACCEPTED=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done
[ -n "$RELEASE" ] || { usage >&2; exit 2; }
printf '%s' "$RELEASE" | grep -Eq '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$' || cannot_run "--release is not a release tag."
if [ -n "$PROJECT" ]; then
    printf '%s' "$PROJECT" | grep -Eq '^[a-z0-9][a-z0-9_-]*$' || cannot_run "--project is not a Compose project name."
fi
for tool in docker curl; do
    command -v "$tool" >/dev/null 2>&1 || cannot_run "$tool is not installed."
done
[ -f compose.production.yaml ] && [ -f compose.production.build.yaml ] \
    || cannot_run "Run it from the repository root of the release checkout."
[ -f "$ENV_FILE" ] || cannot_run "The env file $ENV_FILE does not exist."
PORT=$(sed -n 's/^PARTFLOW_HTTP_PORT=//p' "$ENV_FILE" | tail -n 1)
printf '%s' "$PORT" | grep -Eq '^[0-9]{1,5}$' || cannot_run "PARTFLOW_HTTP_PORT in $ENV_FILE is not an unquoted port."

pf() {
    if [ -n "$PROJECT" ]; then
        docker compose -p "$PROJECT" -f compose.production.yaml --env-file "$ENV_FILE" "$@"
    else
        docker compose -f compose.production.yaml --env-file "$ENV_FILE" "$@"
    fi
}

BASE="http://127.0.0.1:$PORT"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
FAILED=0

# fetch METHOD PATH [curl header arguments...]: STATUS, the headers in $WORK/headers, the body in $WORK/body.
fetch() {
    method=$1
    path=$2
    shift 2
    : >"$WORK/headers"
    : >"$WORK/body"
    STATUS=$(curl -sS -X "$method" --max-time 15 -D "$WORK/headers" -o "$WORK/body" -w '%{http_code}' "$@" \
        "$BASE$path" 2>"$WORK/curl.err") || STATUS=000
}

# header NAME: the value of the last header NAME (case-insensitive), without the line ending.
header() {
    awk -v name="$1" '
        { sub(/\r$/, ""); i = index($0, ":") }
        i > 0 && tolower(substr($0, 1, i - 1)) == tolower(name) { v = substr($0, i + 1); sub(/^[ \t]+/, "", v); last = v }
        END { if (last != "") print last }
    ' "$WORK/headers"
}

body_has() {
    grep -qF -- "$1" "$WORK/body"
}

result() {
    if [ "$1" = 0 ]; then
        echo "PASS $2 $3"
    else
        echo "FAIL $2 $3"
        FAILED=1
    fi
}

# S-1: the shell is this release's bundle.
fetch GET /
ok=1
[ "$STATUS" = 200 ] || ok=0
case "$(header content-type)" in text/html*) ;; *) ok=0 ;; esac
[ "$(header cache-control)" = no-cache ] || ok=0
body_has "<meta name=\"partflow-release\" content=\"$RELEASE\">" || ok=0
result $((1 - ok)) S-1 "GET / serves the $RELEASE shell (HTTP $STATUS, no-cache)"

# S-2: SPA fallback for a deep route.
fetch GET /management/work-orders
ok=1
[ "$STATUS" = 200 ] || ok=0
case "$(header content-type)" in text/html*) ;; *) ok=0 ;; esac
result $((1 - ok)) S-2 "GET /management/work-orders serves the shell (HTTP $STATUS)"

# S-3: readiness of this release.
fetch GET /api/health
ok=1
[ "$STATUS" = 200 ] || ok=0
body_has "\"release\":\"$RELEASE\"" || ok=0
if body_has '"schema":"current"'; then
    :
elif [ -n "$ALLOW_ACCEPTED" ] && body_has '"schema":"accepted"'; then
    :
else
    ok=0
fi
result $((1 - ok)) S-3 "GET /api/health is ready on $RELEASE (HTTP $STATUS)"

# S-4: liveness of this release.
fetch GET /api/health/live
ok=1
[ "$STATUS" = 200 ] || ok=0
body_has "\"release\":\"$RELEASE\"" || ok=0
result $((1 - ok)) S-4 "GET /api/health/live answers $RELEASE (HTTP $STATUS)"

# S-5: an unknown API path is the backend's JSON 404, never the shell.
fetch GET /api/partflow-smoke-missing
ok=1
[ "$STATUS" = 404 ] || ok=0
case "$(header content-type)" in application/json*) ;; *) ok=0 ;; esac
result $((1 - ok)) S-5 "GET /api/partflow-smoke-missing is a JSON 404 (HTTP $STATUS)"

# S-6: the release gate refuses a write without the release header (before routing; nothing written).
fetch POST /api/partflow-smoke-gate -H 'X-PartFlow-CSRF: 1' -H 'Content-Type: application/json' --data '{}'
ok=1
[ "$STATUS" = 409 ] || ok=0
body_has '"release_mismatch":true' || ok=0
result $((1 - ok)) S-6 "POST without X-PartFlow-Release is refused 409 release_mismatch (HTTP $STATUS)"

# S-7: with this release's header the gate passes; the path has no route (nothing written).
fetch POST /api/partflow-smoke-gate -H 'X-PartFlow-CSRF: 1' -H 'Content-Type: application/json' \
    -H "X-PartFlow-Release: $RELEASE" --data '{}'
ok=1
[ "$STATUS" = 404 ] || ok=0
result $((1 - ok)) S-7 "POST with X-PartFlow-Release: $RELEASE passes the gate to a 404 (HTTP $STATUS)"

# S-8: the running containers use this release's images.
ok=1
for service in backend web; do
    container=$(pf ps -q "$service" 2>/dev/null) || container=
    running=
    if [ -n "$container" ] && [ "$(printf '%s\n' "$container" | wc -l)" -eq 1 ]; then
        running=$(docker inspect --format '{{.Image}}' "$container" 2>/dev/null) || running=
    fi
    expected=$(docker image inspect --format '{{.Id}}' "partflow/$service:$RELEASE" 2>/dev/null) || expected=
    if [ -z "$running" ] || [ -z "$expected" ] || [ "$running" != "$expected" ]; then
        ok=0
    fi
done
result $((1 - ok)) S-8 "backend and web run partflow/{backend,web}:$RELEASE"

exit "$FAILED"
