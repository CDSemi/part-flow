#!/bin/sh
# PartFlow production backup (P16-S5; OPERATIONS_RUNBOOK §3). Run from the repository root of a release checkout: the
# running release's checkout for scheduled and manual runs, the candidate's checkout when release.sh calls it.
# `deploy/production/backup.sh --help` prints the usage.
#
# With `PF="docker compose -f compose.production.yaml --env-file <env>"` it streams
# `pg_dump --format=custom --no-owner --no-privileges` from inside `db` (client = server binary) into
# <PARTFLOW_BACKUP_DIR>/.partial/<NAME>/, lists it and extracts its alembic_version rows (both inside `db`, TZ=UTC), then
# the `backup-tools` one-shot (no network, no database) writes manifest.json and SHA256SUMS and publishes the backup
# directory <PARTFLOW_BACKUP_DIR>/<NAME>/ with one atomic rename (backup-manifest), verifies it (backup-verify) and, for
# a daily backup, applies the retention (backup-rotate). Encryption and the off-host copy belong to the platform backup
# tool (SYNOLOGY_NAS §6, VPS §8).
#
# stdout: exactly one line on success, `BACKUP <NAME> <absolute path>`. stderr: progress, every tool JSON document,
# errors. Exit status: 0 completed; 1 refused before writing (backup_running, backup_lock_stale, insufficient_space,
# name_exists); 2 could not run; 3 failed (no backup published, or the published backup failed verification);
# 4 completed, but the rotation failed or found a daily backup that fails verification.
# The backup lock <PARTFLOW_BACKUP_DIR>/.backup.lock is never broken automatically (OPERATIONS_RUNBOOK §3).
set -eu

NAME_STAMP=$(date -u +%Y%m%dT%H%M%SZ)
RELEASE_PATTERN='^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'
REVISION_PATTERN='^[A-Za-z0-9_]{1,32}$'
# The values in the shell would override the env file for every Compose command: the script sets them per command only.
unset PARTFLOW_RELEASE PARTFLOW_COMMIT PARTFLOW_ACCEPT_SCHEMA_REVISION PARTFLOW_BACKUP_DIR COMPOSE_PROJECT_NAME \
    COMPOSE_FILE COMPOSE_PROFILES

usage() {
    cat <<'EOF'
Usage: deploy/production/backup.sh --kind daily|manual|pre-release --operator NAME
           [--reason TEXT] [--label TAG] [--tools-release TAG] [--env-file .env.production]
           [--environment NAME] [--keep-daily N --keep-weekly N | --no-rotate] [--reserve-mib N]
           [--lock-held-by-release RECORD_DIR] [--rehearsal --project NAME]

Run from the repository root of a release checkout. Writes one backup directory <PARTFLOW_BACKUP_DIR>/<NAME>/
(partflow.dump, partflow.dump.list, manifest.json, SHA256SUMS); NAME = <UTC stamp>-<kind>[-<label>].
  --kind daily|manual|pre-release  daily needs --keep-daily N --keep-weekly N (the owner's retention) or --no-rotate
  --reason TEXT                    required for manual (default: scheduled daily backup / pre-release backup for TAG)
  --label TAG                      the target release tag; required for pre-release, refused otherwise
  --tools-release TAG              the image that writes and verifies the manifest (default: PARTFLOW_RELEASE)
  --reserve-mib N                  free space kept beyond 2 x the newest dump (default 1024)
  --lock-held-by-release DIR       release.sh only: it holds the backup lock for its record directory DIR
  --rehearsal --project NAME       a throwaway Compose project (never partflow-production)
Text values: 1-500 characters, no control characters, no " or \.
Exit: 0 completed, 1 refused (nothing written), 2 could not run, 3 failed (do not use a backup it names),
      4 completed but the rotation failed or found a daily backup that fails verification.
stdout on success: BACKUP <NAME> <absolute path>
EOF
}

usage_error() {
    echo "backup: $1 Nothing was written." >&2
    usage >&2
    exit 2
}

could_not_run() {
    echo "backup: $1 Nothing was written." >&2
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

check_count() {
    printf '%s' "$2" | grep -Eq '^(0|[1-9][0-9]{0,5})$' || usage_error "$1 must be a whole number."
    [ "$2" -ge "$3" ] && [ "$2" -le "$4" ] || usage_error "$1 must be between $3 and $4."
}

# ---------------------------------------------------------------------------
# Arguments (refused before any action)
# ---------------------------------------------------------------------------

KIND=
OPERATOR=
REASON=
LABEL=
TOOLS_RELEASE=
ENV_FILE=.env.production
ENVIRONMENT=
KEEP_DAILY=
KEEP_WEEKLY=
NO_ROTATE=
RESERVE_MIB=1024
HELD_BY=
REHEARSAL=
PROJECT=
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
        --no-rotate) NO_ROTATE=1; shift; continue ;;
        --rehearsal) REHEARSAL=1; shift; continue ;;
        --kind | --operator | --reason | --label | --tools-release | --env-file | --environment | --keep-daily | \
            --keep-weekly | --reserve-mib | --lock-held-by-release | --project)
            [ "$#" -ge 2 ] || usage_error "$option needs a value."
            value=$2
            shift 2
            ;;
        *) usage_error "unknown argument $option." ;;
    esac
    case "$option" in
        --kind) KIND=$value ;;
        --operator) check_text "$option" "$value"; OPERATOR=$value ;;
        --reason) check_text "$option" "$value"; REASON=$value ;;
        --label) LABEL=$value ;;
        --tools-release) TOOLS_RELEASE=$value ;;
        --env-file) ENV_FILE=$value ;;
        --environment) ENVIRONMENT=$value ;;
        --keep-daily) check_count "$option" "$value" 1 3650; KEEP_DAILY=$value ;;
        --keep-weekly) check_count "$option" "$value" 0 520; KEEP_WEEKLY=$value ;;
        --reserve-mib) check_count "$option" "$value" 0 1048576; RESERVE_MIB=$value ;;
        --lock-held-by-release) check_text "$option" "$value"; HELD_BY=$value ;;
        --project) PROJECT=$value ;;
    esac
done

case "$KIND" in
    daily | manual | pre-release) ;;
    '') usage_error "--kind is required." ;;
    *) usage_error "--kind must be daily, manual or pre-release." ;;
esac
[ -n "$OPERATOR" ] || usage_error "--operator is required."
if [ -n "$LABEL" ]; then
    printf '%s' "$LABEL" | grep -Eq "$RELEASE_PATTERN" \
        || usage_error "--label must be a release tag of at most 64 letters, digits, '.', '_' or '-'."
fi
if [ -n "$TOOLS_RELEASE" ]; then
    printf '%s' "$TOOLS_RELEASE" | grep -Eq "$RELEASE_PATTERN" \
        || usage_error "--tools-release must be a release tag of at most 64 letters, digits, '.', '_' or '-'."
fi
case "$KIND" in
    daily)
        [ -z "$LABEL" ] || usage_error "--label is only for --kind pre-release."
        if [ -n "$NO_ROTATE" ]; then
            [ -z "$KEEP_DAILY$KEEP_WEEKLY" ] || usage_error "--no-rotate excludes --keep-daily and --keep-weekly."
        else
            [ -n "$KEEP_DAILY" ] && [ -n "$KEEP_WEEKLY" ] \
                || usage_error "--kind daily needs --keep-daily N --keep-weekly N (the owner's retention) or --no-rotate."
        fi
        [ -n "$REASON" ] || REASON="scheduled daily backup"
        ;;
    manual)
        [ -z "$LABEL" ] || usage_error "--label is only for --kind pre-release."
        [ -n "$REASON" ] || usage_error "--kind manual needs --reason TEXT."
        ;;
    pre-release)
        [ -n "$LABEL" ] || usage_error "--kind pre-release needs --label TAG (the target release)."
        [ -n "$REASON" ] || REASON="pre-release backup for $LABEL"
        ;;
esac
if [ "$KIND" != daily ] && [ -n "$KEEP_DAILY$KEEP_WEEKLY$NO_ROTATE" ]; then
    usage_error "--keep-daily, --keep-weekly and --no-rotate are only for --kind daily."
fi
if [ -n "$REHEARSAL" ]; then
    [ -n "$PROJECT" ] || usage_error "--rehearsal needs --project NAME (a throwaway Compose project)."
    printf '%s' "$PROJECT" | grep -Eq '^[a-z0-9][a-z0-9_-]*$' || usage_error "--project is not a Compose project name."
    case "$PROJECT" in
        partflow | partflow-production | partflow-staging)
            usage_error "--project $PROJECT is not a throwaway project: a rehearsal never runs on it." ;;
    esac
    [ -n "$ENVIRONMENT" ] || ENVIRONMENT=rehearsal
else
    [ -z "$PROJECT" ] || usage_error "--project is only for --rehearsal."
    [ -n "$ENVIRONMENT" ] || ENVIRONMENT=production
fi
printf '%s' "$ENVIRONMENT" | grep -Eq '^[a-z][a-z0-9-]{0,31}$' \
    || usage_error "--environment must be lowercase letters, digits or '-' (at most 32)."
NAME=$NAME_STAMP-$KIND
[ -z "$LABEL" ] || NAME=$NAME-$LABEL

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

now_ms() {
    ms=$(date +%s%3N 2>/dev/null) || ms=
    case "$ms" in
        '' | *[!0-9]*) echo $(($(date +%s) * 1000)) ;;
        *) echo "$ms" ;;
    esac
}
utc() {
    date -u +%Y-%m-%dT%H:%M:%SZ
}
STEP=preflight
STEP_STARTED=$(now_ms)
step_begin() {
    STEP=$1
    STEP_STARTED=$(now_ms)
}
step_ok() {
    echo "backup: $STEP ok ($(($(now_ms) - STEP_STARTED)) ms)" >&2
}
pf() {
    if [ -n "$PROJECT" ]; then
        docker compose -p "$PROJECT" -f compose.production.yaml --env-file "$ENV_FILE" "$@"
    else
        docker compose -f compose.production.yaml --env-file "$ENV_FILE" "$@"
    fi
}
# tools ARGS...: one backup-tools command (backup-manifest, backup-verify, backup-rotate) as the invoking account.
tools() {
    if [ -n "$PROJECT" ]; then
        env PARTFLOW_RELEASE="$TOOLS_RELEASE" docker compose -p "$PROJECT" -f compose.production.yaml \
            --env-file "$ENV_FILE" --profile ops run --rm --no-deps -T --user "$USER_IDS" backup-tools "$@"
    else
        env PARTFLOW_RELEASE="$TOOLS_RELEASE" docker compose -f compose.production.yaml \
            --env-file "$ENV_FILE" --profile ops run --rm --no-deps -T --user "$USER_IDS" backup-tools "$@"
    fi
}
# json_field FILE KEY: a top-level string value of an indented app.cli report (empty for null or absent).
json_field() {
    sed -n 's/^  "'"$2"'": "\([^"]*\)",\{0,1\}$/\1/p' "$1" 2>/dev/null | head -n 1
}
# pg_dump_sessions: the number of pg_dump sessions on the database server (empty when it cannot be read).
pg_dump_sessions() {
    pf exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT count(*) FROM pg_stat_activity WHERE application_name = '"'"'pg_dump'"'"'"' \
        2>/dev/null | tr -d '\r' | head -n 1
}

# ---------------------------------------------------------------------------
# 0 preflight
# ---------------------------------------------------------------------------

for tool in docker ps id uname df; do
    command -v "$tool" >/dev/null 2>&1 || could_not_run "$tool is not installed."
done
[ -f compose.production.yaml ] \
    || could_not_run "Run it from the repository root of a release checkout (compose.production.yaml not found)."
[ -f "$ENV_FILE" ] || could_not_run "The env file $ENV_FILE does not exist."
RUNNING_RELEASE=$(sed -n 's/^PARTFLOW_RELEASE=//p' "$ENV_FILE" | tail -n 1)
DIR=$(sed -n 's/^PARTFLOW_BACKUP_DIR=//p' "$ENV_FILE" | tail -n 1)
printf '%s' "$RUNNING_RELEASE" | grep -Eq "$RELEASE_PATTERN" \
    || could_not_run "PARTFLOW_RELEASE in $ENV_FILE must name the running release (unquoted)."
case "$DIR" in
    /*) ;;
    *) could_not_run "PARTFLOW_BACKUP_DIR in $ENV_FILE must be an absolute path, written unquoted." ;;
esac
case "$DIR" in
    *\"* | *\'* | *[[:space:]]*) could_not_run "PARTFLOW_BACKUP_DIR in $ENV_FILE must be unquoted and without spaces." ;;
esac
[ -d "$DIR" ] || could_not_run "The backup directory $DIR (PARTFLOW_BACKUP_DIR) does not exist."
[ -w "$DIR" ] || could_not_run "The backup directory $DIR is not writable by this account."
DIR=${DIR%/}
[ -n "$TOOLS_RELEASE" ] || TOOLS_RELEASE=$RUNNING_RELEASE
USER_IDS="$(id -u):$(id -g)"
if ! pf config --quiet >&2; then
    could_not_run "'docker compose ... config --quiet' failed (see above)."
fi
DB_CONTAINER=$(pf ps --status running -q db 2>/dev/null) || DB_CONTAINER=
[ -n "$DB_CONTAINER" ] || could_not_run "the db service is not running."
docker image inspect --format '{{.Id}}' "partflow/backend:$TOOLS_RELEASE" >/dev/null 2>&1 \
    || could_not_run "the image partflow/backend:$TOOLS_RELEASE (the tools release) is not on this host."

LOCK_DIR=$DIR/.backup.lock
LOCK_OWNED=
PARTIAL=
PUBLISHED=
WORK=

cleanup() {
    [ -z "$WORK" ] || rm -rf "$WORK"
    if [ -n "$PARTIAL" ] && [ -d "$PARTIAL" ]; then
        rm -rf "$PARTIAL"
    fi
    if [ -n "$LOCK_OWNED" ]; then
        rm -rf "$LOCK_DIR"
        LOCK_OWNED=
    fi
}
on_signal() {
    trap - INT TERM
    echo "backup: interrupted during step $STEP." >&2
    if [ -z "$PUBLISHED" ]; then
        echo "backup: nothing was published; the work directory is removed." >&2
    else
        echo "backup: $NAME was published but not verified: do not use $NAME; remove it after review." >&2
    fi
    exit 3
}
trap cleanup EXIT
trap on_signal INT TERM

# lock_busy: the lock exists; report backup_running or backup_lock_stale (never removes or changes it).
lock_busy() {
    owner=$LOCK_DIR/owner
    o_host=$(sed -n 's/^host=//p' "$owner" 2>/dev/null | head -n 1)
    o_pid=$(sed -n 's/^pid=//p' "$owner" 2>/dev/null | head -n 1)
    o_started=$(sed -n 's/^started_at=//p' "$owner" 2>/dev/null | head -n 1)
    o_by=$(sed -n 's/^by=//p' "$owner" 2>/dev/null | head -n 1)
    o_name=$(sed -n 's/^name=//p' "$owner" 2>/dev/null | head -n 1)
    o_release=$(sed -n 's/^release=//p' "$owner" 2>/dev/null | head -n 1)
    running=
    if [ "$o_host" != "$(uname -n)" ]; then
        running=1
    elif ! ps -p "$$" >/dev/null 2>&1; then
        # This ps cannot look a process up by pid: never call the lock stale without that proof.
        running=1
    elif printf '%s' "$o_pid" | grep -Eq '^[0-9]+$' && ps -p "$o_pid" >/dev/null 2>&1; then
        running=1
    elif [ "$(pg_dump_sessions)" != 0 ]; then
        running=1
    fi
    if [ -n "$running" ]; then
        detail=
        [ -z "$o_release" ] || detail=", release record $o_release"
        echo "backup: refused — a backup is running (${o_by:-unknown} on ${o_host:-unknown}, pid ${o_pid:-unknown}, since ${o_started:-unknown}$detail). Nothing was written." >&2
    else
        detail=
        paths=
        if [ -n "$o_name" ]; then
            detail=", backup $o_name"
            paths=" and $DIR/.partial/$o_name"
        fi
        echo "backup: refused — the backup lock of ${o_by:-unknown} (host ${o_host:-unknown}, pid ${o_pid:-unknown}, since ${o_started:-unknown}$detail) was left by a run that no longer exists. Check OPERATIONS_RUNBOOK §3, then remove $DIR/.backup.lock$paths. Nothing was written." >&2
    fi
    exit 1
}

if [ -n "$HELD_BY" ]; then
    if ! { [ -f "$LOCK_DIR/owner" ] && grep -qx 'by=release.sh' "$LOCK_DIR/owner" \
        && grep -qxF "release=$HELD_BY" "$LOCK_DIR/owner"; }; then
        could_not_run "--lock-held-by-release $HELD_BY: the backup lock $LOCK_DIR is not held by that release run."
    fi
elif lock_error=$(mkdir "$LOCK_DIR" 2>&1); then
    LOCK_OWNED=1
    {
        echo "host=$(uname -n)"
        echo "pid=$$"
        echo "started_at=$(utc)"
        echo "by=backup.sh"
    } >"$LOCK_DIR/owner"
elif [ -e "$LOCK_DIR" ]; then
    lock_busy
else
    # Not busy: the lock cannot be created at all (no space or inodes left, a read-only file system, ...).
    could_not_run "the backup lock $LOCK_DIR cannot be created ($(printf '%s' "$lock_error" | tr -d '\r' | tail -n 1))."
fi
WORK=$(mktemp -d)
step_ok

# ---------------------------------------------------------------------------
# 1 identify the running release (best effort, never fatal)
# ---------------------------------------------------------------------------

step_begin identify
COMMIT=$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' \
    "partflow/backend:$RUNNING_RELEASE" 2>/dev/null) || COMMIT=
printf '%s' "$COMMIT" | grep -Eq '^[0-9a-f]{40}$' || COMMIT=
IMAGE_BACKEND=$(docker image inspect --format '{{.Id}}' "partflow/backend:$RUNNING_RELEASE" 2>/dev/null) || IMAGE_BACKEND=
IMAGE_WEB=$(docker image inspect --format '{{.Id}}' "partflow/web:$RUNNING_RELEASE" 2>/dev/null) || IMAGE_WEB=
IMAGE_DB=$(docker inspect --format '{{.Image}}' "$DB_CONTAINER" 2>/dev/null) || IMAGE_DB=
for variable in IMAGE_BACKEND IMAGE_WEB IMAGE_DB; do
    eval "value=\$$variable"
    printf '%s' "$value" | grep -Eq '^sha256:[0-9a-f]{64}$' || eval "$variable="
done
EXPECTED_REVISION=
if pf run --rm --no-deps -T backend python -m app.cli revision >"$WORK/revision.json" 2>"$WORK/revision.log" \
    || [ -s "$WORK/revision.json" ]; then
    EXPECTED_REVISION=$(json_field "$WORK/revision.json" expected_revision)
fi
printf '%s' "$EXPECTED_REVISION" | grep -Eq "$REVISION_PATTERN" || EXPECTED_REVISION=
[ -n "$EXPECTED_REVISION" ] || echo "backup: the running release's expected revision could not be read (recorded as none)." >&2
HOST=$(uname -n 2>/dev/null | tr -cd 'A-Za-z0-9._-' | cut -c 1-100) || HOST=
[ -n "$HOST" ] || HOST=unknown
step_ok

# ---------------------------------------------------------------------------
# 2 space: 2 x the newest published dump (or the database size) + the reserve
# ---------------------------------------------------------------------------

step_begin space
newest=$(ls -1 "$DIR" 2>/dev/null \
    | grep -E '^[0-9]{8}T[0-9]{6}Z-(daily|manual|pre-release)(-[A-Za-z0-9][A-Za-z0-9._-]{0,63})?$' \
    | while read -r entry; do [ -f "$DIR/$entry/partflow.dump" ] && echo "$entry"; done \
    | sort | tail -n 1) || newest=
if [ -n "$newest" ]; then
    base_bytes=$(wc -c <"$DIR/$newest/partflow.dump" | tr -d ' ')
else
    base_bytes=$(pf exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT pg_database_size(current_database())"' \
        2>/dev/null | tr -d '\r' | head -n 1) || base_bytes=
fi
printf '%s' "$base_bytes" | grep -Eq '^[0-9]+$' || could_not_run "the size of the newest backup or of the database could not be read."
avail_kib=$(df -Pk "$DIR" 2>/dev/null | awk 'NR == 2 {print $4}') || avail_kib=
printf '%s' "$avail_kib" | grep -Eq '^[0-9]+$' || could_not_run "the free space of $DIR could not be read (df -Pk)."
needed_mib=$(((2 * base_bytes + 1048575) / 1048576 + RESERVE_MIB))
avail_mib=$((avail_kib / 1024))
if [ "$avail_mib" -lt "$needed_mib" ]; then
    echo "backup: refused — $avail_mib MiB free in $DIR, $needed_mib MiB needed. Nothing was written." >&2
    exit 1
fi
step_ok

# ---------------------------------------------------------------------------
# 3 dump, 4 list, 5 revision (inside db, TZ=UTC: the archive stores local-time fields)
# ---------------------------------------------------------------------------

step_begin dump
[ -z "$LOCK_OWNED" ] || echo "name=$NAME" >>"$LOCK_DIR/owner"
umask 077
mkdir -p "$DIR/.partial" || could_not_run "$DIR/.partial cannot be created."
if [ -e "$DIR/$NAME" ] || ! mkdir "$DIR/.partial/$NAME" 2>/dev/null; then
    echo "backup: refused — a backup named $NAME already exists (or is being written). Nothing was written." >&2
    exit 1
fi
PARTIAL=$DIR/.partial/$NAME
fail() {
    echo "backup: $1 No backup was published." >&2
    exit 3
}
rc=0
pf exec -T -e TZ=UTC db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --no-owner --no-privileges --lock-wait-timeout=60s' \
    >"$PARTIAL/partflow.dump" || rc=$?
[ "$rc" -eq 0 ] || fail "pg_dump failed (exit $rc)."
[ -s "$PARTIAL/partflow.dump" ] || fail "pg_dump wrote nothing."
step_ok

step_begin list
rc=0
pf exec -T -e TZ=UTC db pg_restore --list <"$PARTIAL/partflow.dump" >"$PARTIAL/partflow.dump.list" || rc=$?
[ "$rc" -eq 0 ] || fail "pg_restore --list failed (exit $rc)."
step_ok

step_begin revision
rc=0
pf exec -T -e TZ=UTC db pg_restore --data-only --table=alembic_version --file=- <"$PARTIAL/partflow.dump" \
    >"$PARTIAL/alembic_version.sql" || rc=$?
[ "$rc" -eq 0 ] || fail "the alembic_version rows could not be extracted (exit $rc)."
step_ok

# ---------------------------------------------------------------------------
# 6 manifest (publishes), 7 verify, 8 rotate, 9 done
# ---------------------------------------------------------------------------

step_begin manifest
rc=0
tools backup-manifest --name "$NAME" --operator "$OPERATOR" --reason "$REASON" --release-tag "$RUNNING_RELEASE" \
    --release-commit "$COMMIT" --expected-revision "$EXPECTED_REVISION" --environment "$ENVIRONMENT" --host "$HOST" \
    --image-backend "$IMAGE_BACKEND" --image-web "$IMAGE_WEB" --image-db "$IMAGE_DB" >"$WORK/manifest.json" || rc=$?
cat "$WORK/manifest.json" >&2
[ "$rc" -eq 0 ] || fail "backup-manifest did not publish $NAME (exit $rc)."
[ -d "$DIR/$NAME" ] || fail "backup-manifest reported success but $DIR/$NAME does not exist."
PUBLISHED=1
awk '
    /^  "warnings": \[$/ { on = 1; next }
    on && /^  \]/ { exit }
    on { line = $0; sub(/^ +"/, "", line); sub(/",?$/, "", line); print "backup: warning — " line }
' "$WORK/manifest.json" >&2
step_ok

step_begin verify
rc=0
tools backup-verify "$NAME" >&2 || rc=$?
if [ "$rc" -ne 0 ]; then
    echo "backup: $NAME failed verification (backup-verify exit $rc). Do not use $NAME; remove it after review." >&2
    exit 3
fi
step_ok

STATUS=0
if [ "$KIND" = daily ] && [ -z "$NO_ROTATE" ]; then
    step_begin rotate
    rc=0
    tools backup-rotate --keep-daily "$KEEP_DAILY" --keep-weekly "$KEEP_WEEKLY" >&2 || rc=$?
    case "$rc" in
        0) step_ok ;;
        1)
            echo "backup: rotation found daily backups that fail verification (the names are in the backup-rotate report above); they were kept for review (OPERATIONS_RUNBOOK §9). $NAME itself is complete and verified." >&2
            STATUS=4
            ;;
        *)
            echo "backup: rotation failed (backup-rotate exit $rc). $NAME itself is complete and verified." >&2
            STATUS=4
            ;;
    esac
fi

STEP=done
echo "BACKUP $NAME $DIR/$NAME"
exit "$STATUS"
