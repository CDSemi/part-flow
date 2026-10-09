#!/bin/sh
# PartFlow scheduled reconciliation (P16-S6; OPERATIONS_RUNBOOK §7, §9). Run from the repository root of the RUNNING
# release's checkout, as the account that owns PARTFLOW_BACKUP_DIR, once a day off-peak after the daily backup; the
# host scheduler's failure notification is the alert channel (OD-16-11).
# `deploy/production/scheduled-reconcile.sh --help` prints the usage.
#
# Runs the full read-only `python -m app.cli reconcile` through the backend service (as the application database role),
# stores its report as <reports-dir>/<UTC stamp>-reconcile.json (mode 0600: check (j) findings may carry Worker badge
# values; keep the reports like backups, never attach them to tickets or emails) and applies the P16-S1 exit-code rule
# with monitor_report.py: an empty or unparseable report, or one whose exit_code differs from the process status, is
# "could not run", never "mismatch". stdout: `RECONCILE clean|mismatch|error|could_not_run ...` and one `FAIL ...` line
# per failing check (ids, titles, counts and reasons only, never a finding's values). Exit: 0 clean, 1 mismatch,
# 2 could not run (also while a release runs). Reports are never deleted by this script.
set -eu

# The values in the shell would override the env file for every Compose command.
unset PARTFLOW_RELEASE PARTFLOW_COMMIT PARTFLOW_ACCEPT_SCHEMA_REVISION PARTFLOW_BACKUP_DIR COMPOSE_PROJECT_NAME \
    COMPOSE_FILE COMPOSE_PROFILES
# Reports and their logs are readable by the owning account only.
umask 077

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
RELEASE_PATTERN='^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'
DOCKER_SECONDS=30

usage() {
    cat <<'EOF'
Usage: deploy/production/scheduled-reconcile.sh [--env-file .env.production] [--reports-dir DIR]
           [--records-dir DIR] [--max-findings N] [--max-runtime-minutes N] [--quiet]
           [--rehearsal --project NAME]

Run from the repository root of the running release's checkout, as the account that owns PARTFLOW_BACKUP_DIR.
  --env-file FILE              default .env.production
  --reports-dir DIR            default $HOME/partflow-monitoring/reconcile (mode 0700; reports mode 0600)
  --records-dir DIR            release records (default $HOME/partflow-deployments); refused while
                               DIR/.release.lock exists or release.sh holds the backup lock
  --max-findings N             findings listed per check in the report (1-10000, default 10000)
  --max-runtime-minutes N      1-720, default 60; a longer run is stopped and reported as could_not_run
  --quiet                      print nothing unless the exit status is non-zero (cron MAILTO)
  --rehearsal --project NAME   a throwaway Compose project (never partflow, partflow-production or partflow-staging)
Output: RECONCILE clean|mismatch|error|could_not_run <report> (...), then FAIL <check> <title>: ... lines.
Exit: 0 clean, 1 mismatch, 2 could not run. <reports-dir>/last-result.txt holds the last run's lines.
EOF
}

REPORTS_READY=
# write_last EXIT FILE: <reports-dir>/last-result.txt (the UTC time and exit status, then the output lines).
write_last() {
    {
        echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) exit $1"
        cat "$2"
    } >"$REPORTS_DIR/last-result.txt.tmp" && mv -f "$REPORTS_DIR/last-result.txt.tmp" "$REPORTS_DIR/last-result.txt"
}
could_not_run() {
    echo "RECONCILE could_not_run $1"
    if [ -n "$REPORTS_READY" ]; then
        echo "RECONCILE could_not_run $1" >"$REPORTS_DIR/.last-result.lines"
        write_last 2 "$REPORTS_DIR/.last-result.lines" || true
        rm -f "$REPORTS_DIR/.last-result.lines"
    fi
    exit 2
}
usage_error() {
    usage >&2
    could_not_run "$1"
}
check_count() {
    printf '%s' "$2" | grep -Eq '^(0|[1-9][0-9]{0,5})$' || usage_error "$1 must be a whole number."
    [ "$2" -ge "$3" ] && [ "$2" -le "$4" ] || usage_error "$1 must be between $3 and $4."
}

# ---------------------------------------------------------------------------
# Arguments (refused before any action)
# ---------------------------------------------------------------------------

ENV_FILE=.env.production
REPORTS_DIR=
RECORDS_DIR=
MAX_FINDINGS=10000
MAX_MINUTES=60
QUIET=
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
        --quiet) QUIET=1; shift; continue ;;
        --rehearsal) REHEARSAL=1; shift; continue ;;
        --env-file | --reports-dir | --records-dir | --max-findings | --max-runtime-minutes | --project)
            [ "$#" -ge 2 ] || usage_error "$option needs a value."
            value=$2
            shift 2
            ;;
        *) usage_error "unknown argument $option." ;;
    esac
    case "$option" in
        --env-file) ENV_FILE=$value ;;
        --reports-dir) REPORTS_DIR=$value ;;
        --records-dir) RECORDS_DIR=$value ;;
        --max-findings) check_count "$option" "$value" 1 10000; MAX_FINDINGS=$value ;;
        --max-runtime-minutes) check_count "$option" "$value" 1 720; MAX_MINUTES=$value ;;
        --project) PROJECT=$value ;;
    esac
done
if [ -n "$REHEARSAL" ]; then
    [ -n "$PROJECT" ] || usage_error "--rehearsal needs --project NAME (a throwaway Compose project)."
    printf '%s' "$PROJECT" | grep -Eq '^[a-z0-9][a-z0-9_-]*$' || usage_error "--project is not a Compose project name."
    case "$PROJECT" in
        partflow | partflow-production | partflow-staging)
            usage_error "--project $PROJECT is not a throwaway project: a rehearsal never runs on it." ;;
    esac
else
    [ -z "$PROJECT" ] || usage_error "--project is only for --rehearsal."
fi
if [ -z "$REPORTS_DIR" ] || [ -z "$RECORDS_DIR" ]; then
    [ -n "${HOME:-}" ] || usage_error "HOME is not set: give --reports-dir and --records-dir."
fi
REPORTS_DIR=${REPORTS_DIR:-$HOME/partflow-monitoring/reconcile}
RECORDS_DIR=${RECORDS_DIR:-$HOME/partflow-deployments}

# ---------------------------------------------------------------------------
# 0 preflight
# ---------------------------------------------------------------------------

for tool in docker python3 timeout date; do
    command -v "$tool" >/dev/null 2>&1 || could_not_run "$tool is not installed."
done
# Another `timeout` (for example Windows' timeout.exe) cannot bound a command: prove the POSIX-style tool.
timeout 5 true >/dev/null 2>&1 || could_not_run "'timeout 5 true' failed: timeout is not the coreutils/BusyBox tool."
[ -f compose.production.yaml ] \
    || could_not_run "Run it from the repository root of the running release's checkout (compose.production.yaml not found)."
[ -f "$ENV_FILE" ] || could_not_run "The env file $ENV_FILE does not exist."
RUNNING_RELEASE=$(sed -n 's/^PARTFLOW_RELEASE=//p' "$ENV_FILE" | tail -n 1)
BACKUP_DIR=$(sed -n 's/^PARTFLOW_BACKUP_DIR=//p' "$ENV_FILE" | tail -n 1)
HTTP_PORT=$(sed -n 's/^PARTFLOW_HTTP_PORT=//p' "$ENV_FILE" | tail -n 1)
printf '%s' "$RUNNING_RELEASE" | grep -Eq "$RELEASE_PATTERN" \
    || could_not_run "PARTFLOW_RELEASE in $ENV_FILE must name the running release (unquoted)."
case "$BACKUP_DIR" in
    /*) ;;
    *) could_not_run "PARTFLOW_BACKUP_DIR in $ENV_FILE must be an absolute path, written unquoted." ;;
esac
case "$BACKUP_DIR" in
    *\"* | *\'* | *[[:space:]]*) could_not_run "PARTFLOW_BACKUP_DIR in $ENV_FILE must be unquoted and without spaces." ;;
esac
BACKUP_DIR=${BACKUP_DIR%/}
printf '%s' "$HTTP_PORT" | grep -Eq '^[0-9]{1,5}$' || could_not_run "PARTFLOW_HTTP_PORT in $ENV_FILE must be a port number."
if [ -n "$PROJECT" ]; then
    PROJECT_NAME=$PROJECT
else
    PROJECT_NAME=$(sed -n 's/^name: *//p' compose.production.yaml | head -n 1)
    [ -n "$PROJECT_NAME" ] || could_not_run "compose.production.yaml names no project."
fi

# pf SECONDS ARGS...: one docker compose command of this stack, bounded by timeout (exit 124 on expiry).
pf() {
    seconds=$1
    shift
    if [ -n "$PROJECT" ]; then
        timeout "$seconds" docker compose -p "$PROJECT" -f compose.production.yaml --env-file "$ENV_FILE" "$@"
    else
        timeout "$seconds" docker compose -f compose.production.yaml --env-file "$ENV_FILE" "$@"
    fi
}
# one_off_backends: the ids of this project's one-off backend containers (`docker compose run`).
one_off_backends() {
    timeout "$DOCKER_SECONDS" docker ps -a -q --filter "label=com.docker.compose.project=$PROJECT_NAME" \
        --filter "label=com.docker.compose.service=backend" --filter "label=com.docker.compose.oneoff=True" 2>/dev/null
}

rc=0
config_error=$(pf "$DOCKER_SECONDS" config --quiet 2>&1) || rc=$?
if [ "$rc" -eq 124 ]; then
    could_not_run "('docker compose ... config --quiet' gave no answer within $DOCKER_SECONDS s)"
elif [ "$rc" -ne 0 ]; then
    printf '%s\n' "$config_error" | sed -n '$p' | tr -d '\r' >&2
    could_not_run "('docker compose ... config --quiet' failed, exit $rc)"
fi
mkdir -p "$REPORTS_DIR" 2>/dev/null && chmod 700 "$REPORTS_DIR" 2>/dev/null \
    || could_not_run "the reports directory $REPORTS_DIR cannot be created."
REPORTS_READY=1
if [ -d "$RECORDS_DIR/.release.lock" ]; then
    could_not_run "a release is running ($RECORDS_DIR/.release.lock)"
fi
if [ -f "$BACKUP_DIR/.backup.lock/owner" ] && grep -qx 'by=release.sh' "$BACKUP_DIR/.backup.lock/owner" 2>/dev/null; then
    could_not_run "a release is running ($BACKUP_DIR/.backup.lock)"
fi

# ---------------------------------------------------------------------------
# 1 reconcile, 2 evaluate (P16-S1 exit-code rule), 3 last-result.txt
# ---------------------------------------------------------------------------

REPORT=$REPORTS_DIR/$(date -u +%Y%m%dT%H%M%SZ)-reconcile.json
[ ! -e "$REPORT" ] || could_not_run "the report $REPORT already exists (another run in the same second)."
BEFORE=$(one_off_backends) || BEFORE=
rc=0
pf "$((MAX_MINUTES * 60))" run --rm --no-deps -T backend python -m app.cli reconcile --max-findings "$MAX_FINDINGS" \
    >"$REPORT" 2>"$REPORT.log" || rc=$?
OUTPUT=$REPORTS_DIR/.last-result.lines
if [ "$rc" -eq 124 ]; then
    # Remove only the one-off backend container this run created (one that was not there before the run).
    for id in $(one_off_backends || true); do
        case " $(printf '%s' "$BEFORE" | tr '\n' ' ') " in
            *" $id "*) ;;
            *) timeout "$DOCKER_SECONDS" docker rm -f "$id" >/dev/null 2>&1 || true ;;
        esac
    done
    echo "RECONCILE could_not_run $REPORT (no answer within $MAX_MINUTES min)" >"$OUTPUT"
    EXIT=2
else
    EXIT=0
    python3 "$SCRIPT_DIR/monitor_report.py" reconcile "$REPORT" "$rc" >"$OUTPUT.raw" 2>/dev/null || EXIT=$?
    tr -d '\r' <"$OUTPUT.raw" >"$OUTPUT"
    rm -f "$OUTPUT.raw"
    if [ "$EXIT" -gt 2 ] || [ ! -s "$OUTPUT" ]; then
        echo "RECONCILE could_not_run $REPORT (exit $rc; the report could not be evaluated)" >"$OUTPUT"
        EXIT=2
    fi
fi
write_last "$EXIT" "$OUTPUT"
if [ -z "$QUIET" ] || [ "$EXIT" -ne 0 ]; then
    cat "$OUTPUT"
fi
rm -f "$OUTPUT"
exit "$EXIT"
