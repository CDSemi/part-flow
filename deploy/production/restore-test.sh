#!/bin/sh
# PartFlow isolated restore drill (P16-S5; OPERATIONS_RUNBOOK §4). Run from the repository root of a release checkout
# (at least P16-S5) on the production host; `deploy/production/restore-test.sh --help` prints the usage.
#
# It restores one published backup (backup.sh) into its own throwaway Compose project partflow-restore-<suffix>: its
# own volume and networks, its own edge subnet and loopback port, generated throwaway database-role passwords, the
# database partflow_restore_test, and the backup directory mounted READ-ONLY. Order: verify -> images -> db ->
# pg_restore --single-transaction -> provision-roles -> apply-grants -> revision -> backend and web -> readiness ->
# full reconcile -> smoke.sh -> evidence.json -> teardown (`down -v` of the drill project only, after the evidence
# exists, and only when this run created the project). It never runs migrate and never writes to the backup directory
# or to the production project. --db-image restores onto a candidate PostgreSQL image (the glibc/PostgreSQL-image half
# of reconcile check (j)).
#
# Evidence and step outputs: <records-dir>/<UTC>-restore-test-<NAME>/ (mode 0700). Exit status: 0 passed or
# passed_with_preexisting_findings; 1 failed or insufficient_space; 2 could_not_run or interrupted; 3 the teardown
# failed (the drill project remains: `docker compose -p <project> down -v`).
set -eu

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
NAME_PATTERN='^[0-9]{8}T[0-9]{6}Z-(daily|manual|pre-release)(-[A-Za-z0-9][A-Za-z0-9._-]{0,63})?$'
RELEASE_PATTERN='^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'
PROJECT_PATTERN='^partflow-restore-[a-z0-9][a-z0-9-]{0,40}$'
DRILL_DATABASE=partflow_restore_test
HEALTH_ATTEMPTS=90
HEALTH_INTERVAL=2
# The values in the shell would override the generated env file for every Compose command.
unset PARTFLOW_RELEASE PARTFLOW_COMMIT PARTFLOW_ACCEPT_SCHEMA_REVISION PARTFLOW_BACKUP_DIR COMPOSE_PROJECT_NAME \
    COMPOSE_FILE COMPOSE_PROFILES

usage() {
    cat <<'EOF'
Usage: deploy/production/restore-test.sh --backup NAME --operator NAME
           [--env-file .env.production] [--release TAG] [--tools-release TAG] [--db-image IMAGE]
           [--project partflow-restore-<suffix>] [--http-port PORT] [--edge-subnet CIDR]
           [--baseline-report FILE] [--records-dir DIR] [--space-factor N] [--keep]

Run from the repository root of a release checkout. Restores the backup <PARTFLOW_BACKUP_DIR>/NAME into a throwaway
Compose project (never the production project or database) and tears it down after writing the evidence.
  --release TAG          the release to run on the restored database (default: the release in the manifest)
  --tools-release TAG    the image that verifies the backup (default: PARTFLOW_RELEASE of the env file)
  --db-image IMAGE       restore onto this PostgreSQL image (a candidate server; default: compose.production.yaml's)
  --project NAME         partflow-restore-<suffix> (default: partflow-restore-<UTC stamp>)
  --http-port PORT       the drill's loopback port (default 18090; never the production port)
  --edge-subnet CIDR     the drill's edge subnet (default 172.30.254.0/24; never the production subnet)
  --baseline-report FILE a reconcile report of the source database (for example the release's pre-reconcile.json):
                         findings already in it do not fail the drill (passed_with_preexisting_findings)
  --records-dir DIR      where the evidence directory is written (default $HOME/partflow-deployments)
  --space-factor N       free space needed on the Docker root: N x the dump + 1 GiB (2-20, default 5)
  --keep                 keep the drill project running (for a manual read-back); prints the teardown command
Exit: 0 passed, 1 failed or insufficient space, 2 could not run or interrupted, 3 teardown failed.
EOF
}

usage_error() {
    echo "restore-test: $1 Nothing was created." >&2
    usage >&2
    exit 2
}

could_not_run_early() {
    echo "restore-test: $1 Nothing was created." >&2
    exit 2
}

check_text() {
    case "$2" in
        *[![:space:]]*) ;;
        *) usage_error "$1 must not be empty." ;;
    esac
    [ "${#2}" -le 500 ] || usage_error "$1 must be at most 500 characters."
    case "$2" in
        *\"* | *\\*) usage_error "$1 must not contain \" or \\." ;;
    esac
    [ "$(printf '%s' "$2" | tr -d '[:cntrl:]')" = "$2" ] || usage_error "$1 must not contain control characters."
}

# ---------------------------------------------------------------------------
# 0 preflight: arguments first (the backup name before any path is built)
# ---------------------------------------------------------------------------

BACKUP=
OPERATOR=
ENV_FILE=.env.production
RELEASE=
TOOLS_RELEASE=
DB_IMAGE=
PROJECT=
HTTP_PORT=18090
EDGE_SUBNET=172.30.254.0/24
BASELINE=
RECORDS_DIR=
SPACE_FACTOR=5
KEEP=
SEEN=' '
while [ "$#" -gt 0 ]; do
    option=$1
    case "$SEEN" in
        *" $option "*) usage_error "$option is given twice." ;;
    esac
    SEEN="$SEEN$option "
    case "$option" in
        -h | --help)
            usage
            exit 0
            ;;
        --keep) KEEP=1; shift; continue ;;
        --backup | --operator | --env-file | --release | --tools-release | --db-image | --project | --http-port | \
            --edge-subnet | --baseline-report | --records-dir | --space-factor)
            [ "$#" -ge 2 ] || usage_error "$option needs a value."
            value=$2
            shift 2
            ;;
        *) usage_error "unknown argument $option." ;;
    esac
    case "$option" in
        --backup) BACKUP=$value ;;
        --operator) check_text "$option" "$value"; OPERATOR=$value ;;
        --env-file) ENV_FILE=$value ;;
        --release) RELEASE=$value ;;
        --tools-release) TOOLS_RELEASE=$value ;;
        --db-image) DB_IMAGE=$value ;;
        --project) PROJECT=$value ;;
        --http-port) HTTP_PORT=$value ;;
        --edge-subnet) EDGE_SUBNET=$value ;;
        --baseline-report) BASELINE=$value ;;
        --records-dir) RECORDS_DIR=$value ;;
        --space-factor) SPACE_FACTOR=$value ;;
    esac
done
[ -n "$BACKUP" ] || usage_error "--backup NAME is required."
printf '%s' "$BACKUP" | grep -Eq "$NAME_PATTERN" \
    || usage_error "--backup must name a backup directory created by backup.sh (for example 20261008T020000Z-daily)."
[ -n "$OPERATOR" ] || usage_error "--operator is required."
for pair in "--release:$RELEASE" "--tools-release:$TOOLS_RELEASE"; do
    value=${pair#*:}
    [ -z "$value" ] || printf '%s' "$value" | grep -Eq "$RELEASE_PATTERN" \
        || usage_error "${pair%%:*} must be a release tag of at most 64 letters, digits, '.', '_' or '-'."
done
if [ -n "$DB_IMAGE" ]; then
    printf '%s' "$DB_IMAGE" | grep -Eq '^[A-Za-z0-9][A-Za-z0-9._/:@-]{0,254}$' || usage_error "--db-image is not an image reference."
fi
[ -n "$PROJECT" ] || PROJECT=partflow-restore-$(printf '%s' "$STAMP" | tr 'A-Z' 'a-z')
printf '%s' "$PROJECT" | grep -Eq "$PROJECT_PATTERN" \
    || usage_error "--project must be partflow-restore-<suffix> (lowercase letters, digits, '-'): a drill never runs on another project."
printf '%s' "$HTTP_PORT" | grep -Eq '^[1-9][0-9]{0,4}$' && [ "$HTTP_PORT" -le 65535 ] || usage_error "--http-port is not a port."
printf '%s' "$EDGE_SUBNET" | grep -Eq '^[0-9]{1,3}(\.[0-9]{1,3}){3}/[0-9]{1,2}$' || usage_error "--edge-subnet is not an IPv4 CIDR."
printf '%s' "$SPACE_FACTOR" | grep -Eq '^[0-9]{1,2}$' && [ "$SPACE_FACTOR" -ge 2 ] && [ "$SPACE_FACTOR" -le 20 ] \
    || usage_error "--space-factor must be between 2 and 20."
if [ -n "$BASELINE" ]; then
    [ -f "$BASELINE" ] || usage_error "--baseline-report $BASELINE is not a file."
fi
if [ -z "$RECORDS_DIR" ]; then
    [ -n "${HOME:-}" ] || usage_error "HOME is not set: give --records-dir DIR."
    RECORDS_DIR=$HOME/partflow-deployments
fi
case "$RECORDS_DIR$ENV_FILE$BASELINE" in
    *\"* | *\\*) usage_error "paths must not contain \" or \\." ;;
esac
# Until the evidence directory exists an interruption only stops (nothing was created, no project is touched).
trap 'echo "restore-test: interrupted during the preflight. Nothing was created." >&2; exit 2' INT TERM

for tool in docker curl python3 df id uname od; do
    command -v "$tool" >/dev/null 2>&1 || could_not_run_early "$tool is not installed."
done
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' 2>/dev/null \
    || could_not_run_early "python3 3.8 or newer is required (it reads the reconcile check statuses)."
[ -f compose.production.yaml ] && [ -f compose.production.build.yaml ] \
    || could_not_run_early "Run it from the repository root of a release checkout (compose.production.yaml not found)."
[ -f "$ENV_FILE" ] || could_not_run_early "The env file $ENV_FILE does not exist."
env_value() {
    sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1
}
PROD_RELEASE=$(env_value PARTFLOW_RELEASE)
BACKUP_DIR=$(env_value PARTFLOW_BACKUP_DIR)
PROD_DB=$(env_value POSTGRES_DB)
PROD_USER=$(env_value POSTGRES_USER)
PROD_PORT=$(env_value PARTFLOW_HTTP_PORT)
PROD_SUBNET=$(env_value PARTFLOW_EDGE_SUBNET)
[ -n "$PROD_SUBNET" ] || PROD_SUBNET=172.30.250.0/24
SITE_TIMEZONE=$(env_value PARTFLOW_SITE_TIMEZONE)
printf '%s' "$PROD_RELEASE" | grep -Eq "$RELEASE_PATTERN" \
    || could_not_run_early "PARTFLOW_RELEASE in $ENV_FILE must name the running release (unquoted)."
case "$BACKUP_DIR" in
    /*) ;;
    *) could_not_run_early "PARTFLOW_BACKUP_DIR in $ENV_FILE must be an absolute path, written unquoted." ;;
esac
case "$BACKUP_DIR" in
    *\"* | *\'* | *\\* | *[[:space:]]*) could_not_run_early "PARTFLOW_BACKUP_DIR in $ENV_FILE must be unquoted and without spaces." ;;
esac
BACKUP_DIR=${BACKUP_DIR%/}
[ -n "$PROD_DB" ] && [ -n "$PROD_USER" ] && [ -n "$SITE_TIMEZONE" ] \
    || could_not_run_early "POSTGRES_DB, POSTGRES_USER and PARTFLOW_SITE_TIMEZONE must be set in $ENV_FILE."
[ -n "$TOOLS_RELEASE" ] || TOOLS_RELEASE=$PROD_RELEASE
BACKUP_PATH=$BACKUP_DIR/$BACKUP
[ -d "$BACKUP_PATH" ] || could_not_run_early "The backup $BACKUP_PATH does not exist."
[ "$PROD_DB" != "$DRILL_DATABASE" ] \
    || could_not_run_early "the production POSTGRES_DB is $DRILL_DATABASE, the drill's database name: the drill never runs where the names could be confused."
[ "$HTTP_PORT" != "$PROD_PORT" ] || could_not_run_early "--http-port $HTTP_PORT is the production port."
[ "$EDGE_SUBNET" != "$PROD_SUBNET" ] || could_not_run_early "--edge-subnet $EDGE_SUBNET is the production edge subnet."
label="label=com.docker.compose.project=$PROJECT"
existing=$(docker ps -a -q --filter "$label" 2>/dev/null) || could_not_run_early "'docker ps -a' failed."
[ -z "$existing" ] || could_not_run_early "the project $PROJECT already has containers (an earlier --keep drill?). Remove it first: docker compose -p $PROJECT down -v"
existing=$(docker volume ls -q --filter "$label" 2>/dev/null) || could_not_run_early "'docker volume ls' failed."
[ -z "$existing" ] || could_not_run_early "the project $PROJECT already has volumes. Remove them first: docker compose -p $PROJECT down -v"
[ -f "$BACKUP_PATH/partflow.dump" ] || could_not_run_early "$BACKUP_PATH/partflow.dump does not exist."
DUMP_BYTES=$(wc -c <"$BACKUP_PATH/partflow.dump" | tr -d ' ')
DOCKER_ROOT=$(docker info --format '{{.DockerRootDir}}' 2>/dev/null) || DOCKER_ROOT=
[ -n "$DOCKER_ROOT" ] || could_not_run_early "'docker info' did not name the Docker root directory."
avail_kib=$(df -Pk "$DOCKER_ROOT" 2>/dev/null | awk 'NR == 2 {print $4}') || avail_kib=
printf '%s' "$avail_kib" | grep -Eq '^[0-9]+$' \
    || could_not_run_early "the free space of the Docker root $DOCKER_ROOT could not be read (df -Pk); the drill needs that check."
needed_mib=$((SPACE_FACTOR * ((DUMP_BYTES + 1048575) / 1048576) + 1024))
avail_mib=$((avail_kib / 1024))
if [ "$avail_mib" -lt "$needed_mib" ]; then
    echo "restore-test: refused — $avail_mib MiB free on the Docker root $DOCKER_ROOT, $needed_mib MiB needed ($SPACE_FACTOR x the dump + 1 GiB). Nothing was created." >&2
    exit 1
fi

umask 077
RD=$RECORDS_DIR/$STAMP-restore-test-$BACKUP
W=$RD/work
mkdir -p "$RECORDS_DIR" 2>/dev/null || could_not_run_early "The records directory $RECORDS_DIR cannot be created."
mkdir "$RD" 2>/dev/null || could_not_run_early "The evidence directory $RD cannot be created."
mkdir "$W" "$W/secrets"
chmod 700 "$RD" "$W" "$W/secrets"

# ---------------------------------------------------------------------------
# State, evidence and teardown (from here on every exit writes the evidence)
# ---------------------------------------------------------------------------

utc() {
    date -u +%Y-%m-%dT%H:%M:%SZ
}
now_ms() {
    ms=$(date +%s%3N 2>/dev/null) || ms=
    case "$ms" in
        '' | *[!0-9]*) echo $(($(date +%s) * 1000)) ;;
        *) echo "$ms" ;;
    esac
}
js() {
    if [ -n "$1" ]; then printf '"%s"' "$1"; else printf 'null'; fi
}
num() {
    if [ -n "$1" ]; then printf '%s' "$1"; else printf 'null'; fi
}
# json_field FILE KEY: a top-level string value of an indented app.cli report (empty for null or absent).
json_field() {
    sed -n 's/^  "'"$2"'": "\([^"]*\)",\{0,1\}$/\1/p' "$1" 2>/dev/null | head -n 1
}
# json_raw FILE KEY: a top-level number, boolean or null value of an indented app.cli report.
json_raw() {
    sed -n 's/^  "'"$2"'": \([^"{[][^,]*\),\{0,1\}$/\1/p' "$1" 2>/dev/null | head -n 1
}
report_valid() {
    grep -qx "  \"exit_code\": $2," "$1" 2>/dev/null
}

STARTED_AT=$(utc)
HOST=$(uname -n 2>/dev/null | tr -cd 'A-Za-z0-9._-') || HOST=
OUTCOME=
FAILED_STEP=
FINALIZED=
CREATED=
STEP_NAME=
STEP_OUTPUT=
STEP_STARTED=
STEP_MS=
B_REVISION=
B_RELEASE=
B_STARTED=
B_SHA=
DB_IMAGE_ID=
S_VERSION=
S_COLLATION=
S_CTYPE=
S_RECORDED=
S_ACTUAL=
T_VERIFY=
T_DB=
T_RESTORE=
T_ROLES=
T_APP=
T_RECONCILE=
T_SMOKE=
T_RESTORE_TO_READY=
T_TOTAL=
TOTAL_START=
RESTORE_START=
RECONCILE_RC=
CHECK_H=
CHECK_J=
REGRESSION_RC=
SMOKE_RC=
DR_TEXT="docker compose -f compose.production.yaml -f $W/restore-test.override.yaml --env-file $W/restore-test.env -p $PROJECT"
: >"$RD/steps.part"

dr() {
    docker compose -f compose.production.yaml -f "$W/restore-test.override.yaml" --env-file "$W/restore-test.env" \
        -p "$PROJECT" "$@"
}

step_begin() {
    STEP_NAME=$1
    STEP_OUTPUT=${2:-}
    STEP_STARTED=$(utc)
    STEP_START_MS=$(now_ms)
    echo "restore-test: [$STEP_NAME] $3"
}
step_end() {
    STEP_MS=$(($(now_ms) - STEP_START_MS))
    printf '    {"name": "%s", "exit_code": %s, "started_at": "%s", "finished_at": "%s", "output": %s}\n' \
        "$STEP_NAME" "$1" "$STEP_STARTED" "$(utc)" "$(js "$STEP_OUTPUT")" >>"$RD/steps.part"
    STEP_NAME=
}

write_evidence() {
    db_requested=$(js "$DB_IMAGE")
    baseline=$(js "$BASELINE")
    if [ -n "$REGRESSION_RC" ]; then
        regression="{\"file\": \"regression.txt\", \"exit_code\": $REGRESSION_RC}"
    else
        regression=null
    fi
    kept=false
    [ -z "$KEEP" ] || [ -z "$CREATED" ] || kept=true
    {
        printf '{\n'
        printf '  "evidence_version": 1,\n'
        printf '  "command": "restore-test",\n'
        printf '  "outcome": "%s",\n' "$OUTCOME"
        printf '  "failed_step": %s,\n' "$(js "$FAILED_STEP")"
        printf '  "exit_code": %s,\n' "$EXIT_CODE"
        printf '  "operator": "%s",\n' "$OPERATOR"
        printf '  "host": %s,\n' "$(js "$HOST")"
        printf '  "started_at": "%s",\n' "$STARTED_AT"
        printf '  "finished_at": "%s",\n' "$(utc)"
        printf '  "backup": {"name": "%s", "path": "%s", "dump_bytes": %s, "alembic_revision": %s, "release": %s,' \
            "$BACKUP" "$BACKUP_PATH" "$DUMP_BYTES" "$(js "$B_REVISION")" "$(js "$B_RELEASE")"
        printf ' "dump_started_at": %s, "manifest_sha256": %s},\n' "$(js "$B_STARTED")" "$(js "$B_SHA")"
        printf '  "drill": {"project": "%s", "release": %s, "tools_release": "%s", "db_image_requested": %s,' \
            "$PROJECT" "$(js "$RELEASE")" "$TOOLS_RELEASE" "$db_requested"
        printf ' "db_image_id": %s, "database": "%s", "http_port": %s, "edge_subnet": "%s"},\n' \
            "$(js "$DB_IMAGE_ID")" "$DRILL_DATABASE" "$HTTP_PORT" "$EDGE_SUBNET"
        printf '  "server": {"server_version": %s, "collation": %s, "ctype": %s, "collation_version_recorded": %s,' \
            "$(js "$S_VERSION")" "$(js "$S_COLLATION")" "$(js "$S_CTYPE")" "$(js "$S_RECORDED")"
        printf ' "collation_version_actual": %s},\n' "$(js "$S_ACTUAL")"
        printf '  "timings_ms": {"verify": %s, "db_start": %s, "restore": %s, "roles_and_grants": %s,' \
            "$(num "$T_VERIFY")" "$(num "$T_DB")" "$(num "$T_RESTORE")" "$(num "$T_ROLES")"
        printf ' "app_start_to_ready": %s, "reconcile": %s, "smoke": %s, "restore_to_ready": %s, "total": %s},\n' \
            "$(num "$T_APP")" "$(num "$T_RECONCILE")" "$(num "$T_SMOKE")" "$(num "$T_RESTORE_TO_READY")" "$(num "$T_TOTAL")"
        printf '  "steps": [\n'
        sed '$!s/$/,/' "$RD/steps.part"
        printf '  ],\n'
        printf '  "reconcile": {"file": %s, "exit_code": %s, "checks": {"h": %s, "j": %s}, "baseline": %s,' \
            "$( [ -n "$RECONCILE_RC" ] && printf '"reconcile.json"' || printf 'null')" "$(num "$RECONCILE_RC")" \
            "$(js "$CHECK_H")" "$(js "$CHECK_J")" "$baseline"
        printf ' "regression": %s},\n' "$regression"
        printf '  "smoke": {"file": %s, "exit_code": %s},\n' \
            "$( [ -n "$SMOKE_RC" ] && printf '"smoke.txt"' || printf 'null')" "$(num "$SMOKE_RC")"
        printf '  "limitations": {"read_model_readback": "not_automated"},\n'
        printf '  "kept": %s\n' "$kept"
        printf '}\n'
    } >"$RD/evidence.json.tmp" && mv "$RD/evidence.json.tmp" "$RD/evidence.json" && fsync_evidence
}
# fsync_evidence: the evidence file and its directory reach the disk before anything is torn down (a file-level fsync,
# not a global `sync`, which can block on unrelated file systems).
fsync_evidence() {
    python3 -c '
import os, sys
for path in sys.argv[1:]:
    try:
        fd = os.open(path, os.O_RDONLY if os.path.isdir(path) else os.O_RDWR)
    except OSError:
        if os.path.isdir(path):
            continue  # this platform cannot open a directory; the file itself was synced
        raise
    try:
        os.fsync(fd)
    except OSError:
        if not os.path.isdir(path):
            raise
    finally:
        os.close(fd)
' "$RD/evidence.json" "$RD"
}

teardown() {
    if [ -z "$CREATED" ]; then
        state=not_created
        command=
        tear_rc=0
    elif [ -n "$KEEP" ]; then
        state=kept
        command="docker compose -p $PROJECT down -v --remove-orphans"
        tear_rc=0
        echo "restore-test: the drill project $PROJECT is kept (--keep). Remove it after the manual check: $command" >&2
    else
        # Never anything but this run's drill project.
        printf '%s' "$PROJECT" | grep -Eq "$PROJECT_PATTERN" || { echo "restore-test: refusing to tear down $PROJECT." >&2; exit 3; }
        command="$DR_TEXT down -v --remove-orphans"
        tear_rc=0
        dr down -v --remove-orphans >"$RD/teardown.log" 2>&1 || tear_rc=$?
        if [ "$tear_rc" -eq 0 ]; then state=removed; else state=failed; fi
    fi
    rm -rf "$W/secrets"
    printf '{"teardown": "%s", "command": %s, "exit_code": %s}\n' "$state" "$(js "$command")" "$tear_rc" >"$RD/teardown.json"
    if [ "$state" = failed ]; then
        echo "restore-test: the teardown FAILED ($RD/teardown.log): the drill project remains. Remove it: docker compose -p $PROJECT down -v" >&2
        return 1
    fi
    return 0
}

finish() {
    OUTCOME=$1
    EXIT_CODE=$2
    FINALIZED=1
    trap - INT TERM
    rm -f "$RD/evidence.json.tmp"
    if ! write_evidence; then
        echo "restore-test: the evidence file $RD/evidence.json could not be written: the drill project is NOT torn down. Remove it after review: docker compose -p $PROJECT down -v" >&2
        rm -rf "$W/secrets" 2>/dev/null || true
        exit 2
    fi
    rm -f "$RD/steps.part"
    if ! teardown; then
        echo "restore-test: $OUTCOME (exit 3: teardown failed); evidence: $RD/evidence.json"
        exit 3
    fi
    echo "restore-test: $OUTCOME (exit $EXIT_CODE); evidence: $RD/evidence.json"
    exit "$EXIT_CODE"
}

failed() {
    FAILED_STEP=$1
    echo "restore-test: $2" >&2
    finish failed 1
}
could_not_run() {
    FAILED_STEP=$1
    echo "restore-test: $2" >&2
    finish could_not_run 2
}

on_signal() {
    trap - INT TERM
    exec 1>&8 2>&9
    echo "restore-test: interrupted during step ${STEP_NAME:-preflight}." >&2
    if [ -n "$STEP_NAME" ]; then
        FAILED_STEP=$STEP_NAME
        step_end 2
    fi
    finish interrupted 2
}
on_exit() {
    status=$?
    [ -z "$FINALIZED" ] || return 0
    echo "restore-test: stopped unexpectedly during step ${STEP_NAME:-preflight} (exit $status)." >&2
    FAILED_STEP=${STEP_NAME:-preflight}
    finish failed 1
}
exec 8>&1 9>&2
trap 'on_signal' INT TERM
trap on_exit EXIT

# ---------------------------------------------------------------------------
# 0 (continued): generated env file, throwaway secrets, override file, config
# ---------------------------------------------------------------------------

step_begin preflight preflight.log "drill project $PROJECT for backup $BACKUP"
write_env() {
    {
        echo "PARTFLOW_RELEASE=$1"
        echo "PARTFLOW_COMMIT="
        echo "PARTFLOW_ACCEPT_SCHEMA_REVISION="
        echo "PARTFLOW_SECRETS_DIR=$W/secrets"
        echo "PARTFLOW_BACKUP_DIR=$BACKUP_DIR"
        echo "PARTFLOW_SITE_TIMEZONE=$SITE_TIMEZONE"
        echo "PARTFLOW_HTTP_PORT=$HTTP_PORT"
        echo "POSTGRES_USER=$PROD_USER"
        echo "POSTGRES_DB=$DRILL_DATABASE"
        echo "PARTFLOW_BACKEND_WORKERS=1"
        echo "PARTFLOW_EDGE_SUBNET=$EDGE_SUBNET"
        echo "PARTFLOW_TRUSTED_PROXY="
        for key in PARTFLOW_DB_MEMORY PARTFLOW_DB_CPUS PARTFLOW_BACKEND_MEMORY PARTFLOW_BACKEND_CPUS PARTFLOW_WEB_MEMORY \
            PARTFLOW_WEB_CPUS PARTFLOW_OPS_MEMORY PARTFLOW_OPS_CPUS; do
            echo "$key=$(env_value "$key")"
        done
    } >"$W/restore-test.env.tmp"
    mv "$W/restore-test.env.tmp" "$W/restore-test.env"
}
# Until the backup is verified the release is not known: the tools release stands in (backup-tools has its own image).
write_env "${RELEASE:-$TOOLS_RELEASE}"
for secret in postgres_password partflow_app_password partflow_maintenance_password; do
    value=$(od -An -tx1 -N32 /dev/urandom | tr -d ' \n')
    printf '%s' "$value" | grep -Eq '^[0-9a-f]{64}$' || could_not_run preflight "a throwaway password could not be generated."
    for other in "$W"/secrets/*; do
        [ -f "$other" ] || continue
        [ "$(cat "$other")" != "$value" ] || could_not_run preflight "two generated passwords are equal."
    done
    printf '%s\n' "$value" >"$W/secrets/$secret"
    chmod 444 "$W/secrets/$secret"
done
{
    echo "# Generated by deploy/production/restore-test.sh for the drill project $PROJECT; removed with its evidence directory."
    echo "services:"
    if [ -n "$DB_IMAGE" ]; then
        echo "    db:"
        echo "        image: \"$DB_IMAGE\""
    fi
    echo "    backup-tools:"
    echo "        image: \"partflow/backend:$TOOLS_RELEASE\""
    for service in backup-tools migrate; do
        [ "$service" = backup-tools ] || echo "    $service:"
        echo "        volumes:"
        echo "            - type: bind"
        echo "              source: \"$BACKUP_DIR\""
        echo "              target: /backups"
        echo "              read_only: true"
        echo "              bind:"
        echo "                  create_host_path: false"
    done
} >"$W/restore-test.override.yaml"
rc=0
dr config --quiet >"$RD/preflight.log" 2>&1 || rc=$?
step_end "$rc"
[ "$rc" -eq 0 ] || could_not_run preflight "'$DR_TEXT config --quiet' failed ($RD/preflight.log)."

# ---------------------------------------------------------------------------
# 1 verify, 2 images
# ---------------------------------------------------------------------------

TOTAL_START=$(now_ms)
step_begin verify verify.json "backup-verify $BACKUP (tools release $TOOLS_RELEASE)"
rc=0
dr --profile ops run --rm --no-deps -T --user "$(id -u):$(id -g)" backup-tools backup-verify "$BACKUP" \
    >"$RD/verify.json" 2>"$RD/verify.log" || rc=$?
step_end "$rc"
T_VERIFY=$STEP_MS
B_REVISION=$(json_field "$RD/verify.json" alembic_revision)
B_RELEASE=$(json_field "$RD/verify.json" release_tag)
B_STARTED=$(json_field "$RD/verify.json" dump_started_at)
B_SHA=$(json_field "$RD/verify.json" manifest_sha256)
[ "$rc" -eq 0 ] && report_valid "$RD/verify.json" 0 \
    || failed verify "the backup $BACKUP failed verification (exit $rc; $RD/verify.json). Do not use it."
if [ -z "$RELEASE" ]; then
    if [ "$(json_raw "$RD/verify.json" release_matches_revision)" = false ]; then
        could_not_run verify "The backup $BACKUP was taken at revision ${B_REVISION:-none} while release ${B_RELEASE:-unknown} was recorded; name the matching release with --release."
    fi
    printf '%s' "$B_RELEASE" | grep -Eq "$RELEASE_PATTERN" \
        || could_not_run verify "The manifest of $BACKUP names no release; name the matching release with --release."
    RELEASE=$B_RELEASE
fi
write_env "$RELEASE"

step_begin images images.log "images of release $RELEASE and tools release $TOOLS_RELEASE"
rc=0
docker image inspect --format '{{.Id}}' "partflow/backend:$RELEASE" "partflow/web:$RELEASE" \
    "partflow/backend:$TOOLS_RELEASE" >"$RD/images.log" 2>&1 || rc=$?
step_end "$rc"
[ "$rc" -eq 0 ] || could_not_run images "The images are missing ($RD/images.log): keep or build the images of release $RELEASE."

# ---------------------------------------------------------------------------
# 3 db, 4 restore (one transaction), 5 roles, 6 grants, 7 revision
# ---------------------------------------------------------------------------

CREATED=1
step_begin db_start db-start.log "up -d --wait db (${DB_IMAGE:-the production PostgreSQL image})"
rc=0
dr up -d --wait db >"$RD/db-start.log" 2>&1 || rc=$?
step_end "$rc"
T_DB=$STEP_MS
container=$(dr ps -q db 2>/dev/null) || container=
if [ -n "$container" ]; then
    DB_IMAGE_ID=$(docker inspect --format '{{.Image}}' "$container" 2>/dev/null) || DB_IMAGE_ID=
    printf '%s' "$DB_IMAGE_ID" | grep -Eq '^sha256:[0-9a-f]{64}$' || DB_IMAGE_ID=
fi
[ "$rc" -eq 0 ] || failed db_start "the drill database did not start ($RD/db-start.log)."

RESTORE_START=$(now_ms)
step_begin restore restore.log "pg_restore --single-transaction into $DRILL_DATABASE"
rc=0
dr exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --single-transaction --exit-on-error --no-owner --no-privileges' \
    <"$BACKUP_PATH/partflow.dump" >"$RD/restore.log" 2>&1 || rc=$?
step_end "$rc"
T_RESTORE=$STEP_MS
[ "$rc" -eq 0 ] || failed restore "pg_restore failed (exit $rc; $RD/restore.log); the drill database holds nothing of the backup."

roles_start=$(now_ms)
step_begin roles roles.json "provision-roles (throwaway passwords)"
rc=0
dr --profile ops run --rm -T db-roles >"$RD/roles.json" 2>"$RD/roles.log" || rc=$?
step_end "$rc"
[ "$rc" -eq 0 ] || failed roles "provision-roles failed (exit $rc; $RD/roles.json)."

step_begin grants grants.json "apply-grants"
rc=0
dr --profile ops run --rm -T db-roles apply-grants >"$RD/grants.json" 2>"$RD/grants.log" || rc=$?
step_end "$rc"
T_ROLES=$(($(now_ms) - roles_start))
if [ "$rc" -ne 0 ]; then
    if grep -q '^    "code": "revision_mismatch",\{0,1\}$' "$RD/grants.json" 2>/dev/null; then
        failed grants "apply-grants refused: the image of release $RELEASE is not at the backup's revision ${B_REVISION:-none} ($RD/grants.json). Name the matching release with --release."
    fi
    failed grants "apply-grants failed (exit $rc; $RD/grants.json)."
fi

step_begin revision revision.json "revision with release $RELEASE"
rc=0
dr run --rm --no-deps -T backend python -m app.cli revision >"$RD/revision.json" 2>"$RD/revision.log" || rc=$?
step_end "$rc"
state=$(json_field "$RD/revision.json" state)
database_revision=$(json_field "$RD/revision.json" database_revision)
if [ "$rc" -ne 0 ] || [ "$state" != current ] || [ "$database_revision" != "$B_REVISION" ]; then
    failed revision "release $RELEASE does not serve the restored database (state ${state:-unknown}, database revision ${database_revision:-none}, backup revision ${B_REVISION:-none}; $RD/revision.json)."
fi

# ---------------------------------------------------------------------------
# 8 backend and web, readiness; 9 reconcile; 10 smoke
# ---------------------------------------------------------------------------

step_begin app_start health.txt "up -d backend web; GET /api/health until release $RELEASE and schema current (up to 180 s)"
rc=0
dr up -d backend web >"$RD/app-start.log" 2>&1 || rc=$?
healthy=
if [ "$rc" -eq 0 ]; then
    attempt=0
    while [ "$attempt" -lt "$HEALTH_ATTEMPTS" ]; do
        attempt=$((attempt + 1))
        body=$(curl -fsS --max-time 10 "http://127.0.0.1:$HTTP_PORT/api/health" 2>/dev/null) || body=
        printf '%s %s\n' "$(utc)" "$body" >>"$RD/health.txt"
        case "$body" in
            *"\"release\":\"$RELEASE\""*)
                case "$body" in
                    *'"schema":"current"'*) healthy=1; break ;;
                esac
                ;;
        esac
        sleep "$HEALTH_INTERVAL"
    done
fi
if [ -n "$healthy" ]; then step_end 0; else step_end 1; fi
T_APP=$STEP_MS
T_RESTORE_TO_READY=$(($(now_ms) - RESTORE_START))
[ "$rc" -eq 0 ] || failed app_start "'up -d backend web' failed ($RD/app-start.log)."
[ -n "$healthy" ] || failed app_start "/api/health did not report release $RELEASE with schema current within 180 s ($RD/health.txt)."

step_begin reconcile reconcile.json "full reconcile (checks (h) and (j) must pass)"
rc=0
dr run --rm --no-deps -T backend python -m app.cli reconcile --max-findings 10000 \
    >"$RD/reconcile.json" 2>"$RD/reconcile.log" || rc=$?
step_end "$rc"
T_RECONCILE=$STEP_MS
RECONCILE_RC=$rc
report_valid "$RD/reconcile.json" "$rc" || failed reconcile "the reconcile report is not valid (exit $rc; $RD/reconcile.json)."
statuses=$(python3 -c '
import json, sys
report = json.load(open(sys.argv[1], encoding="utf-8"))
status = {check.get("id"): check.get("status") for check in report.get("checks") or []}
database = report.get("database") or {}
for value in (status.get("h"), status.get("j"), database.get("server_version"), database.get("collation"),
              database.get("ctype"), database.get("collation_version_recorded"), database.get("collation_version_actual")):
    text = "" if value is None else str(value)
    print("".join(c for c in text if c.isprintable() and c not in "\"\\"))
' "$RD/reconcile.json" 2>>"$RD/reconcile.log") || failed reconcile "the reconcile report cannot be read ($RD/reconcile.json)."
CHECK_H=$(printf '%s\n' "$statuses" | sed -n 1p)
CHECK_J=$(printf '%s\n' "$statuses" | sed -n 2p)
S_VERSION=$(printf '%s\n' "$statuses" | sed -n 3p)
S_COLLATION=$(printf '%s\n' "$statuses" | sed -n 4p)
S_CTYPE=$(printf '%s\n' "$statuses" | sed -n 5p)
S_RECORDED=$(printf '%s\n' "$statuses" | sed -n 6p)
S_ACTUAL=$(printf '%s\n' "$statuses" | sed -n 7p)
RESULT=
if [ "$rc" -eq 0 ] && [ "$CHECK_H" = pass ] && [ "$CHECK_J" = pass ]; then
    RESULT=passed
elif [ "$rc" -eq 1 ] && [ -n "$BASELINE" ] && [ "$CHECK_H" = pass ] && { [ "$CHECK_J" = pass ] || [ "$CHECK_J" = fail ]; }; then
    step_begin regression regression.txt "compare with the baseline report (reconcile_regression.py)"
    REGRESSION_RC=0
    python3 "$SCRIPT_DIR/reconcile_regression.py" "$BASELINE" "$RD/reconcile.json" >"$RD/regression.txt" 2>&1 \
        || REGRESSION_RC=$?
    step_end "$REGRESSION_RC"
    [ "$REGRESSION_RC" -ne 0 ] || RESULT=passed_with_preexisting_findings
fi
if [ -z "$RESULT" ]; then
    failed reconcile "the restored database does not reconcile (exit $rc, check (h) ${CHECK_H:-absent}, check (j) ${CHECK_J:-absent}; $RD/reconcile.json${REGRESSION_RC:+, $RD/regression.txt})."
fi

step_begin smoke smoke.txt "deploy/production/smoke.sh --release $RELEASE on the drill project"
rc=0
sh "$SCRIPT_DIR/smoke.sh" --release "$RELEASE" --env-file "$W/restore-test.env" --project "$PROJECT" \
    >"$RD/smoke.txt" 2>&1 || rc=$?
step_end "$rc"
T_SMOKE=$STEP_MS
SMOKE_RC=$rc
T_TOTAL=$(($(now_ms) - TOTAL_START))
[ "$rc" -eq 0 ] || failed smoke "the smoke check failed on the drill ($RD/smoke.txt)."

finish "$RESULT" 0
