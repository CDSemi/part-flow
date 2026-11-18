#!/bin/sh
# PartFlow history archival host job (Movement-history archival, build steps 1 and 2; OPERATIONS_RUNBOOK §11,
# DEPLOYMENT §3.1). Run from the repository root of the RUNNING release's checkout, as the account that owns
# PARTFLOW_ARCHIVE_DIR and PARTFLOW_BACKUP_DIR (root on the NAS). `deploy/production/archive.sh --help` prints the
# usage. This job DELETES: once a run's archive is exported, verified and copied off the host, it takes the run's
# pre-purge backup and purges the archived Movement history from the database (whole flow components, in batches), and
# the full reconcile checks the database right after the purge.
#
# The hourly work runs the steps of `python -m app.cli history-archive` in the one-shot `maintenance` service (as the
# maintenance database role, the archive folder mounted, `--user` this account): `recover`; then `next` and the step it
# names, again and again until `next` answers wait, running or none, a run finishes, or a step fails: export --run N,
# verify --run N and copy --run N; for `backup`, the pre-purge backup (deploy/production/backup.sh --kind pre-purge
# --label <archive name>, which waits up to --backup-wait-minutes for a backup that is running) and then purge --run N
# --backup NAME --backup-started-at UTC; for `purge` (a run an earlier start left PURGING), purge --run N alone. After a
# purge that ended with exit 0, or with exit 3 once the run was purging, the full `python -m app.cli reconcile` runs in a
# one-off `backend` container as the application database role, as release.sh and scheduled-reconcile.sh run it; then
# `status`. A start never begins another run after a purge: the reconcile's verdict comes first. A purge that ends with
# exit 2 (a transient database error or a lost connection while PURGING: the batch in progress rolled back, and the run
# stays PURGING) is not reconciled in that start: the next start resumes the purge and reconciles when it ends.
#
# A run left RUNNING after its copy (its pre-purge backup failed, or the job stopped) is failed by the next start's
# `recover`, on purpose: the pre-purge backup is taken while the run is RUNNING, so a database restored from that backup
# never purges again. A database restored from a backup taken while a run was PURGING (an hourly backup during a purge)
# holds that run PURGING, and the next start resumes its purge: disable the task before any restore (OPERATIONS_RUNBOOK
# §6).
#
# Each step's JSON report and stderr go to <records-dir>/archive/<UTC stamp>/ (`next`, then `next-2`, `next-3`, ...;
# backup.out and backup.log of backup.sh; reconcile.json and reconcile.log); that folder is kept only when a step did
# something or failed. A reconcile after a purge that does not end clean (a finding, an error, no complete report) is an
# incident (design §10): archive.sh exits 4 and writes the incident record <records-dir>/archive/reconcile-after-purge
# (key=value lines: result, exit code, run, archive name, pre-purge backup, failing checks, time, report path), which
# check.sh reports as FAIL archival_proposal reconcile_after_purge. While the record exists, the job fails closed: a
# start runs `recover` and `status` only and refuses every step that changes data (export, verify, copy, the pre-purge
# backup and the purge, the resumed purge of a run left PURGING included) with exit 1. Only the operator removes the
# record, once the investigation is done (OPERATIONS_RUNBOOK §8).
#
# The optional off-host copy folder (PARTFLOW_ARCHIVE_COPY_DIR) is mounted at /archive-copy only while it exists, is
# writable, and lies on a file system that is neither the archive folder's nor the root file system `/` (a disk or
# share that dropped out leaves its path on `/`); the rule is evaluated again before every step, so a copy folder that
# vanishes during a long export is not used by the copy. The mount is a long-syntax bind with create_host_path: false in
# a generated Compose override file (`docker compose run` has no --mount; its -v would create a missing host path), so
# Docker refuses a source that vanished in between. Otherwise the job finds /archive-copy unusable and the run waits for
# a copy confirmation in Administration.
#
# `--scheduled` (the host task that starts every 5 minutes, design D12): the hourly work runs once per hour, at the first
# start at or after the Start minute of Administration → History archival & purge (`history-archive settings`). The
# hourly stamp <records-dir>/archive/hourly-slot holds the epoch minute of the slot last attempted; a start within an
# hour of it ends at once without Docker (NOT_DUE). The first scheduled start (no stamp yet) only records the current
# slot, so the work starts at the next Start minute. Once a slot is attempted, a refusal or a failure is reported once
# for that hour, never at every 5-minute start.
#
# Locks: <records-dir>/.archive.lock (owner file: host, pid, started_at, by, and the kernel boot id and the process
# start time from /proc) is taken only for the hourly work, never by a start that only checks the time and never while
# a run merely waits for its copy confirmation. It is held for the whole hourly work, the wait for the backup lock, the
# purge and the reconcile included. archive.sh refuses while <records-dir>/.release.lock exists, and release.sh refuses
# while the archive lock is held, so no live release holds the backup lock while the pre-purge backup waits for it (a
# backup lock left by a killed release.sh is stale, and backup.sh refuses it at once). A lock whose owner provably no
# longer exists (same host, no `maintenance` container running, and its pid gone, or the boot id or the pid's start time
# differs from the recorded one) is removed automatically: the job's `recover` then fails a run left RUNNING. The
# database itself is guarded by the job's advisory lock.
#
# stdout on exit 0: exactly one line, `NOT_DUE`, `DEFERRED <reason>` (scheduled: the previous start still works on
# its hourly work), or `ARCHIVE <outcome> <run id|-> <state|->`. stderr: progress, each step's one-line summary, errors.
# Exit status: 0 done (or nothing due); 1 refused, nothing changed (a release runs, the archive lock is held, a step
# refused, the incident record exists); 2 could not run (configuration, Docker, database, an unknown outcome), or the
# pre-purge backup failed (nothing was deleted; the next start fails the run); 3 a run failed: it is now FAILED and a
# new run must be requested in Administration → History archival & purge (a run that failed while purging keeps the
# whole components it purged, which its purge receipt lists); 4 the reconcile after a purge did not end clean: an
# incident (OPERATIONS_RUNBOOK §8, Reconcile finding after a purge).
set -eu

# The values in the shell would override the env file for every Compose command: the script sets them per command only.
unset PARTFLOW_RELEASE PARTFLOW_COMMIT PARTFLOW_ACCEPT_SCHEMA_REVISION PARTFLOW_BACKUP_DIR PARTFLOW_ARCHIVE_DIR \
    PARTFLOW_ARCHIVE_COPY_DIR COMPOSE_PROJECT_NAME COMPOSE_FILE COMPOSE_PROFILES
# Records, logs and the stamp are readable by the owning account only.
umask 077

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
RELEASE_PATTERN='^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'
# The archive name of a run, the label of its pre-purge backup (contract §4).
ARCHIVE_NAME_PATTERN='^run-[0-9]+-[0-9]{8}T[0-9]{6}Z$'
UTC_PATTERN='^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$'
# The container path of the copy folder (compose.production.yaml sets PARTFLOW_ARCHIVE_COPY_DIR to it when configured).
COPY_TARGET=/archive-copy
# The kernel's process information. A constant: the tests point it at a fake tree.
PROC=/proc
# boot_id: this boot's id (empty without /proc).
boot_id() {
    sed -n '1p' "$PROC/sys/kernel/random/boot_id" 2>/dev/null
}
# start_time PID: when PID started, field 22 of /proc/PID/stat in clock ticks since boot (empty when unreadable). The
# command name (field 2) is in parentheses and may hold spaces, so the fields are counted after its last ')'.
start_time() {
    sed -n '1s/^.*) //p' "$PROC/$1/stat" 2>/dev/null | awk '{ print $20 }'
}
# owner_alive PID BOOT START: the archive lock's owner still runs. A recorded boot id other than this boot's, or a
# recorded start time other than PID's, proves that PID is now another process (a reboot, or a pid given again); when
# a value is missing or unreadable (an older owner file, no /proc), `ps -p` alone decides.
owner_alive() {
    printf '%s' "$1" | grep -Eq '^[0-9]+$' || return 1
    ps -p "$1" >/dev/null 2>&1 || return 1
    alive_boot=$(boot_id)
    if [ -n "$2" ] && [ -n "$alive_boot" ] && [ "$2" != "$alive_boot" ]; then
        return 1
    fi
    alive_start=$(start_time "$1")
    if [ -n "$3" ] && [ -n "$alive_start" ] && [ "$3" != "$alive_start" ]; then
        return 1
    fi
    return 0
}

usage() {
    cat <<'EOF'
Usage: deploy/production/archive.sh [--scheduled] [--env-file .env.production] [--records-dir DIR]
           [--backup-wait-minutes N]

Run from the repository root of the running release's checkout, as the account that owns PARTFLOW_ARCHIVE_DIR and
PARTFLOW_BACKUP_DIR. Runs the hourly work of Movement-history archival in the `maintenance` service: recover, then the
steps that `next` names (export, verify and copy; the pre-purge backup and the purge; or the purge of a run left
PURGING), the full reconcile after a purge, and status. The purge DELETES the archived Movement history from the
database, after the archive's off-host copy and the pre-purge backup.
  --scheduled             the host task that starts every 5 minutes: the hourly work runs once per hour, at the first
                          start at or after the Start minute of Administration → History archival & purge; every
                          other start ends at once (NOT_DUE)
  --env-file FILE         default .env.production
  --records-dir DIR       default $HOME/partflow-deployments: the release lock .release.lock, the archive lock
                          .archive.lock, the hourly stamp archive/hourly-slot, the reports archive/<UTC stamp>/ and
                          the incident record archive/reconcile-after-purge (while it exists, a start runs recover
                          and status only and works on no run)
  --backup-wait-minutes N how long the pre-purge backup waits for another backup that holds the backup lock, polling
                          (0-1440, default 60; 0 = refused at once)
Exit: 0 done (or nothing due), 1 refused (a release runs, the archive lock is held, a step refused, the incident record
      exists; nothing changed), 2 could not run, or the pre-purge backup failed (nothing was deleted; the next start
      fails the run), 3 a run failed: it is FAILED; request a new run in Administration → History archival & purge,
      4 the reconcile after a purge did not end clean: an incident (OPERATIONS_RUNBOOK §8).
stdout on exit 0: NOT_DUE, DEFERRED <reason>, or ARCHIVE <outcome> <run id|-> <state|->
EOF
}

usage_error() {
    echo "archive: $1 Nothing was changed." >&2
    usage >&2
    exit 2
}

# ---------------------------------------------------------------------------
# Arguments (refused before any action)
# ---------------------------------------------------------------------------

SCHEDULED=
ENV_FILE=.env.production
RECORDS_DIR=
# The default of --backup-wait-minutes: long enough for an hourly backup that still runs at the Start minute to end.
BACKUP_WAIT_MINUTES=60
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
        --scheduled) SCHEDULED=1; shift; continue ;;
        --env-file | --records-dir | --backup-wait-minutes)
            [ "$#" -ge 2 ] || usage_error "$option needs a value."
            value=$2
            shift 2
            ;;
        *) usage_error "unknown argument $option." ;;
    esac
    case "$option" in
        --env-file) ENV_FILE=$value ;;
        --records-dir) RECORDS_DIR=$value ;;
        --backup-wait-minutes)
            { printf '%s' "$value" | grep -Eq '^(0|[1-9][0-9]{0,3})$' && [ "$value" -le 1440 ]; } \
                || usage_error "--backup-wait-minutes must be a whole number from 0 to 1440."
            BACKUP_WAIT_MINUTES=$value
            ;;
    esac
done
if [ -z "$RECORDS_DIR" ]; then
    [ -n "${HOME:-}" ] || usage_error "HOME is not set: give --records-dir DIR."
    RECORDS_DIR=$HOME/partflow-deployments
fi
RECORDS_DIR=${RECORDS_DIR%/}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

utc() {
    date -u +%Y-%m-%dT%H:%M:%SZ
}
REC=
# say TEXT: one progress line on stderr (and in the record folder's messages.log once it exists).
say() {
    echo "archive: $1" >&2
    if [ -n "$REC" ]; then
        echo "$(utc) $1" >>"$REC/messages.log" 2>/dev/null || true
    fi
}
STAMP_FILE=$RECORDS_DIR/archive/hourly-slot
# The incident record of a reconcile after a purge that did not end clean (check.sh reads it).
INCIDENT_FILE=$RECORDS_DIR/archive/reconcile-after-purge
# Scheduled only: set once the hourly work is due (or its due check started), cleared once the stamp is written.
STAMP_PENDING=
SLOT=
# mark_slot: write the hourly stamp (the slot this start attempted) so the next attempt waits for the next hour.
mark_slot() {
    [ -n "$STAMP_PENDING" ] || return 0
    STAMP_PENDING=
    echo "$SLOT" >"$STAMP_FILE.tmp" 2>/dev/null && mv -f "$STAMP_FILE.tmp" "$STAMP_FILE" 2>/dev/null \
        || echo "archive: the hourly stamp $STAMP_FILE could not be written." >&2
}
KEEP=
could_not_run() {
    mark_slot
    KEEP=1
    say "$1 Nothing was changed."
    exit 2
}
refuse() {
    mark_slot
    KEEP=1
    say "refused — $1 Nothing was changed."
    exit 1
}
# json_field FILE KEY: a top-level string value of an indented app.cli report (empty for null or absent).
json_field() {
    sed -n 's/^  "'"$2"'": "\([^"]*\)",\{0,1\}$/\1/p' "$1" 2>/dev/null | head -n 1
}
# json_number FILE KEY: a top-level whole-number value of an indented app.cli report (empty for null or absent).
json_number() {
    sed -n 's/^  "'"$2"'": \([0-9][0-9]*\),\{0,1\}$/\1/p' "$1" 2>/dev/null | head -n 1
}
# json_nested FILE OBJECT KEY: the string value KEY of the top-level object OBJECT (empty for null or absent).
json_nested() {
    sed -n '/^  "'"$2"'": {$/,/^  }/s/^    "'"$3"'": "\([^"]*\)",\{0,1\}$/\1/p' "$1" 2>/dev/null | head -n 1
}
# json_nested_number FILE OBJECT KEY: the whole-number value KEY of the top-level object OBJECT.
json_nested_number() {
    sed -n '/^  "'"$2"'": {$/,/^  }/s/^    "'"$3"'": \([0-9][0-9]*\),\{0,1\}$/\1/p' "$1" 2>/dev/null | head -n 1
}
# json_list FILE KEY: the items of a top-level list of strings or numbers, one per line.
json_list() {
    awk -v key="$2" '
        !on && index($0, "  \"" key "\": [") == 1 { if ($0 ~ /\[\],?$/) exit; on = 1; next }
        on && /^  \]/ { exit }
        on { item = $0; sub(/^ +/, "", item); sub(/,$/, "", item); gsub(/"/, "", item); print item }
    ' "$1" 2>/dev/null
}
# error_message FILE: the message of the report's error object (empty when there is none).
error_message() {
    sed -n 's/^    "message": "\(.*\)"$/\1/p' "$1" 2>/dev/null | tail -n 1
}
# report_valid FILE RC: the report holds exactly the line of its exit code (OPERATIONS_RUNBOOK §7 rule).
report_valid() {
    grep -qx "  \"exit_code\": $2," "$1" 2>/dev/null
}
# failing_checks FILE: the ids of the checks of a reconcile report whose status is fail or error, space-separated.
failing_checks() {
    awk '
        /^      "id": "/ { id = $0; sub(/^      "id": "/, "", id); sub(/",?$/, "", id) }
        /^      "status": "(fail|error)",?$/ { printf "%s%s", sep, id; sep = " " }
    ' "$1" 2>/dev/null
}
# mount_point PATH: the mount point of the file system holding PATH (empty when df cannot tell).
mount_point() {
    df -P "$1" 2>/dev/null | awk 'NR == 2 { print $6 }'
}

for tool in docker ps id uname df date awk sed grep mkdir mv; do
    command -v "$tool" >/dev/null 2>&1 || could_not_run "$tool is not installed."
done
mkdir -p "$RECORDS_DIR/archive" 2>/dev/null || could_not_run "The records directory $RECORDS_DIR/archive cannot be created."

# ---------------------------------------------------------------------------
# --scheduled: the hourly stamp (no Docker for a start within an hour of the last attempted slot)
# ---------------------------------------------------------------------------

if [ -n "$SCHEDULED" ]; then
    clock=$(date '+%s %M %S') || clock=
    NOW_EPOCH=$(printf '%s' "$clock" | awk '{ print $1 }')
    NOW_MINUTE=$(printf '%s' "$clock" | awk '{ print $2 + 0 }')
    NOW_SECOND=$(printf '%s' "$clock" | awk '{ print $3 + 0 }')
    printf '%s' "$NOW_EPOCH" | grep -Eq '^[0-9]+$' || could_not_run "the clock could not be read (date '+%s %M %S')."
    # Epoch minutes: the local minute and second are the UTC ones in every time zone that has whole-minute offsets.
    NOW=$(((NOW_EPOCH - NOW_SECOND) / 60))
    LAST=$(sed -n '1p' "$STAMP_FILE" 2>/dev/null) || LAST=
    printf '%s' "$LAST" | grep -Eq '^[0-9]+$' || LAST=
    # A stamp in the future (the clock was set back) is ignored.
    if [ -n "$LAST" ] && [ "$LAST" -gt "$NOW" ]; then
        LAST=
    fi
    if [ -n "$LAST" ] && [ "$NOW" -lt $((LAST + 60)) ]; then
        echo "NOT_DUE"
        exit 0
    fi
    # From here on this start attempts a slot: refusals and failures are reported once for it.
    SLOT=$NOW
    STAMP_PENDING=1
fi

# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------

[ -f compose.production.yaml ] \
    || could_not_run "Run it from the repository root of the running release's checkout (compose.production.yaml not found)."
[ -f "$ENV_FILE" ] || could_not_run "The env file $ENV_FILE does not exist."
env_value() {
    sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1
}
RUNNING_RELEASE=$(env_value PARTFLOW_RELEASE)
printf '%s' "$RUNNING_RELEASE" | grep -Eq "$RELEASE_PATTERN" \
    || could_not_run "PARTFLOW_RELEASE in $ENV_FILE must name the running release (unquoted)."
ARCHIVE_DIR=$(env_value PARTFLOW_ARCHIVE_DIR)
COPY_DIR=$(env_value PARTFLOW_ARCHIVE_COPY_DIR)
[ -n "$ARCHIVE_DIR" ] \
    || could_not_run "PARTFLOW_ARCHIVE_DIR in $ENV_FILE is empty: create the archive folder and set it first (DEPLOYMENT §3.1, SYNOLOGY_NAS §11.8)."
for variable in PARTFLOW_ARCHIVE_DIR PARTFLOW_ARCHIVE_COPY_DIR; do
    if [ "$variable" = PARTFLOW_ARCHIVE_DIR ]; then value=$ARCHIVE_DIR; else value=$COPY_DIR; fi
    [ -n "$value" ] || continue
    case "$value" in
        /*) ;;
        *) could_not_run "$variable in $ENV_FILE must be an absolute path, written unquoted." ;;
    esac
    case "$value" in
        *\"* | *\'* | *[[:space:]]* | *:*) could_not_run "$variable in $ENV_FILE must be unquoted, without spaces or ':'." ;;
    esac
done
ARCHIVE_DIR=${ARCHIVE_DIR%/}
[ -d "$ARCHIVE_DIR" ] || could_not_run "The archive folder $ARCHIVE_DIR (PARTFLOW_ARCHIVE_DIR) does not exist (SYNOLOGY_NAS §11.8)."
[ -w "$ARCHIVE_DIR" ] || could_not_run "The archive folder $ARCHIVE_DIR is not writable by this account (it must own it, mode 0700)."
USER_IDS="$(id -u):$(id -g)"
PROJECT_NAME=$(sed -n 's/^name: *//p' compose.production.yaml | head -n 1)
[ -n "$PROJECT_NAME" ] || could_not_run "compose.production.yaml names no project."

pf() {
    docker compose -f compose.production.yaml --env-file "$ENV_FILE" "$@"
}

[ -z "$COPY_DIR" ] || COPY_DIR=${COPY_DIR%/}
COPY_MOUNT=
COPY_NOTE=
SAID_NOTE=
# copy_rule: is the copy folder usable now? Sets COPY_MOUNT, or COPY_NOTE with the reason (and says a new reason once).
copy_rule() {
    COPY_MOUNT=
    COPY_NOTE=
    [ -n "$COPY_DIR" ] || return 0
    if [ ! -d "$COPY_DIR" ]; then
        COPY_NOTE="the copy folder $COPY_DIR (PARTFLOW_ARCHIVE_COPY_DIR) does not exist (is the share or disk mounted?)"
    elif [ ! -w "$COPY_DIR" ]; then
        COPY_NOTE="the copy folder $COPY_DIR is not writable by this account"
    else
        archive_mount=$(mount_point "$ARCHIVE_DIR")
        copy_mount=$(mount_point "$COPY_DIR")
        if [ -z "$archive_mount" ] || [ -z "$copy_mount" ]; then
            COPY_NOTE="the file systems of the archive and copy folders could not be read (df -P)"
        elif [ "$copy_mount" = / ]; then
            COPY_NOTE="the copy folder $COPY_DIR is on the host's root file system (/), so it is no off-host copy (is the share or disk mounted?)"
        elif [ "$archive_mount" = "$copy_mount" ]; then
            COPY_NOTE="the copy folder $COPY_DIR is on the same file system as the archive folder ($copy_mount), so it is no off-host copy (is the share or disk mounted?)"
        else
            COPY_MOUNT=1
        fi
    fi
    if [ -n "$COPY_NOTE" ] && [ "$COPY_NOTE" != "$SAID_NOTE" ]; then
        SAID_NOTE=$COPY_NOTE
        say "$COPY_NOTE: no copy is made; a run waits for its copy confirmation in Administration."
    fi
}

# job ARGS...: one history-archive command in the maintenance service (its JSON report on stdout). The caller runs
# copy_rule first; the copy folder comes from the override file, never from `-v`.
job() {
    if [ -n "$COPY_MOUNT" ]; then
        docker compose -f compose.production.yaml -f "$WORK/copy-mount.yaml" --env-file "$ENV_FILE" \
            --profile ops run --rm --no-deps -T --user "$USER_IDS" maintenance "$@"
    else
        pf --profile ops run --rm --no-deps -T --user "$USER_IDS" maintenance "$@"
    fi
}

WORK=
LOCK=$RECORDS_DIR/.archive.lock
RELEASE_LOCK=$RECORDS_DIR/.release.lock
LOCK_OWNED=
# The step that runs now (export, verify, copy, backup, purge or reconcile), for the signal handler.
CURRENT_STEP=
# Set once a purge may have deleted something in this start, cleared once its reconcile result is recorded.
RECONCILE_PENDING=
RUN_ID=
ARCHIVE_NAME=
BACKUP_NAME=
cleanup() {
    [ -z "$WORK" ] || rm -rf "$WORK"
    if [ -n "$LOCK_OWNED" ]; then
        rm -rf "$LOCK"
        LOCK_OWNED=
    fi
    if [ -n "$REC" ] && [ -z "$KEEP" ]; then
        rm -rf "$REC"
    fi
}
# record_incident RESULT EXIT_CODE CHECKS: the incident record of a reconcile after a purge that did not end clean,
# replaced in one rename. Until the operator removes it, check.sh reports it and no start works on a run.
record_incident() {
    {
        echo "result=$1"
        echo "exit_code=$2"
        echo "run=$RUN_ID"
        echo "archive_name=$ARCHIVE_NAME"
        echo "pre_purge_backup=$BACKUP_NAME"
        echo "failing_checks=$3"
        echo "finished_at=$(utc)"
        echo "report=$REC/reconcile.json"
    } >"$INCIDENT_FILE.tmp" 2>/dev/null && mv -f "$INCIDENT_FILE.tmp" "$INCIDENT_FILE" 2>/dev/null \
        || say "the incident record $INCIDENT_FILE could not be written, so it cannot stop the next start: disable the history archive task until the incident is closed (OPERATIONS_RUNBOOK §8)."
}
on_signal() {
    trap - INT TERM
    # The trap can run while a step's output is redirected to its report and log: speak on the script's own stdout and
    # stderr (fds 8 and 9), never into the step's files.
    exec 1>&8 2>&9
    KEEP=1
    case "$CURRENT_STEP" in
        purge)
            say "interrupted during the purge of run ${RUN_ID:-?}. Whole components only are ever deleted: the next start resumes the purge of a run left PURGING and reconciles when it ends (the purge receipt then lists what was deleted), or fails a run that was still RUNNING (then nothing was deleted)."
            ;;
        backup)
            say "interrupted during the pre-purge backup of run ${RUN_ID:-?}. Nothing was deleted: the run stays RUNNING, and the next start fails it (recover); request a new run in Administration → History archival & purge."
            ;;
        *)
            if [ -n "$RECONCILE_PENDING" ]; then
                record_incident interrupted 2 ""
                say "interrupted before the reconcile after the purge of run ${RUN_ID:-?} ended: run it by hand (OPERATIONS_RUNBOOK §7), then follow OPERATIONS_RUNBOOK §8 (Reconcile finding after a purge). $INCIDENT_FILE records the incident: no start works on a run until you remove it."
            else
                say "interrupted. The next start recovers a run left RUNNING (it becomes FAILED); nothing was deleted."
            fi
            ;;
    esac
    exit 2
}
# fds 8 and 9: the script's own stdout and stderr, for on_signal.
exec 8>&1 9>&2
trap cleanup EXIT
trap on_signal INT TERM
WORK=$(mktemp -d) || could_not_run "a temporary directory cannot be created."
if [ -n "$COPY_DIR" ]; then
    # Long syntax: Docker refuses a missing source instead of creating it on the host's own disk.
    cat >"$WORK/copy-mount.yaml" <<EOF
services:
    maintenance:
        volumes:
            - type: bind
              source: '$COPY_DIR'
              target: $COPY_TARGET
              bind:
                  create_host_path: false
EOF
fi

# ---------------------------------------------------------------------------
# A release runs: refused (checked again once the archive lock is held)
# ---------------------------------------------------------------------------

if [ -d "$RELEASE_LOCK" ]; then
    refuse "a release is running ($RELEASE_LOCK). The archive work runs at the next hourly start after it (OPERATIONS_RUNBOOK §11)."
fi

# ---------------------------------------------------------------------------
# --scheduled: is the hourly work due? (the Start minute from Administration)
# ---------------------------------------------------------------------------

if [ -n "$SCHEDULED" ]; then
    rc=0
    copy_rule
    job settings >"$WORK/settings.json" 2>"$WORK/settings.log" || rc=$?
    START_MINUTE=$(json_number "$WORK/settings.json" start_minute)
    if [ "$rc" -ne 0 ] || ! report_valid "$WORK/settings.json" "$rc" || [ -z "$START_MINUTE" ] \
        || [ "$START_MINUTE" -gt 59 ]; then
        tail -n 3 "$WORK/settings.log" >&2 2>/dev/null || true
        could_not_run "the Start minute could not be read (history-archive settings exit $rc; see above)."
    fi
    # The slot: the newest HH:<Start minute> at or before now, as an epoch minute.
    SLOT=$((NOW - (NOW_MINUTE - START_MINUTE + 60) % 60))
    if [ -z "$LAST" ]; then
        # The first scheduled start (or a lost stamp): record the current slot; the work starts at the next Start minute.
        mark_slot
        echo "NOT_DUE"
        exit 0
    fi
    if [ "$SLOT" -le "$LAST" ]; then
        STAMP_PENDING=
        echo "NOT_DUE"
        exit 0
    fi
fi

# ---------------------------------------------------------------------------
# The archive lock
# ---------------------------------------------------------------------------

# maintenance_running: the ids of this project's running `maintenance` containers.
maintenance_running() {
    docker ps -q --filter "label=com.docker.compose.project=$PROJECT_NAME" \
        --filter "label=com.docker.compose.service=maintenance" 2>/dev/null
}
# lock_busy: the lock exists. A provably stale lock is removed (return 0); otherwise the start is refused or deferred.
lock_busy() {
    owner=$LOCK/owner
    o_host=$(sed -n 's/^host=//p' "$owner" 2>/dev/null | head -n 1)
    o_pid=$(sed -n 's/^pid=//p' "$owner" 2>/dev/null | head -n 1)
    o_started=$(sed -n 's/^started_at=//p' "$owner" 2>/dev/null | head -n 1)
    o_by=$(sed -n 's/^by=//p' "$owner" 2>/dev/null | head -n 1)
    o_boot=$(sed -n 's/^boot=//p' "$owner" 2>/dev/null | head -n 1)
    o_start=$(sed -n 's/^start=//p' "$owner" 2>/dev/null | head -n 1)
    who="${o_by:-unknown} on ${o_host:-unknown}, pid ${o_pid:-unknown}, since ${o_started:-unknown}"
    if [ ! -f "$owner" ] || [ "$o_host" != "$(uname -n)" ] || ! ps -p "$$" >/dev/null 2>&1; then
        # Without an owner on this host, or without a ps that looks a pid up, the lock is never called stale.
        refuse "the archive lock $LOCK ($who) cannot be checked. If no archive.sh runs, remove $LOCK (OPERATIONS_RUNBOOK §11)."
    fi
    owner_text=$(cat "$owner" 2>/dev/null) || owner_text=
    containers=$(maintenance_running) || containers=
    alive=
    if owner_alive "$o_pid" "$o_boot" "$o_start"; then
        alive=1
    fi
    if [ -n "$containers" ] || [ -n "$alive" ]; then
        # A step runs, or the owner is between two steps (its pre-purge backup or reconcile included): a scheduled start
        # leaves this hour to it.
        if [ -n "$SCHEDULED" ]; then
            mark_slot
            say "the previous archive.sh ($who) is still working; this hour's work is skipped."
            echo "DEFERRED archive_running"
            exit 0
        fi
        [ -z "$containers" ] || refuse "the history archive job is running ($who)."
        refuse "archive.sh holds the archive lock ($who). If that process is no archive.sh, remove $LOCK (OPERATIONS_RUNBOOK §11)."
    fi
    # Stale: take it out of the way with one rename, so that of two starts that judged it stale only one goes on.
    stale=$LOCK.stale-$$
    [ ! -e "$stale" ] || rm -rf "$stale"
    mv "$LOCK" "$stale" 2>/dev/null || refuse "another archive.sh took over the stale archive lock $LOCK meanwhile."
    if [ "$(cat "$stale/owner" 2>/dev/null)" != "$owner_text" ]; then
        # The renamed lock is another start's new lock, not the stale one: give it back.
        mv "$stale" "$LOCK" 2>/dev/null || true
        refuse "another archive.sh took over the stale archive lock $LOCK meanwhile."
    fi
    say "removed the archive lock of a run that no longer exists ($who)."
    rm -rf "$stale"
}

take_lock() {
    if lock_error=$(mkdir "$LOCK" 2>&1); then
        LOCK_OWNED=1
        {
            echo "host=$(uname -n)"
            echo "pid=$$"
            echo "boot=$(boot_id)"
            echo "start=$(start_time "$$")"
            echo "started_at=$(utc)"
            echo "by=archive.sh"
        } >"$LOCK/owner"
        return 0
    fi
    if [ ! -e "$LOCK" ]; then
        could_not_run "the archive lock $LOCK cannot be created ($(printf '%s' "$lock_error" | tr -d '\r' | tail -n 1))."
    fi
    return 1
}
if ! take_lock; then
    lock_busy
    take_lock || refuse "another archive.sh took the archive lock $LOCK meanwhile."
fi
# release.sh takes its lock first and then checks this one: one of two concurrent starts always refuses.
if [ -d "$RELEASE_LOCK" ]; then
    refuse "a release is running ($RELEASE_LOCK). The archive work runs at the next hourly start after it (OPERATIONS_RUNBOOK §11)."
fi
mark_slot

REC=$RECORDS_DIR/archive/$(date -u +%Y%m%dT%H%M%SZ)
mkdir "$REC" 2>/dev/null || { REC=; could_not_run "the record folder $RECORDS_DIR/archive/<UTC stamp> cannot be created."; }

# ---------------------------------------------------------------------------
# The hourly work (contract §3.1)
# ---------------------------------------------------------------------------

EXIT=0
STATE=
OUTCOME=
STEP_NAME=
STEP_CMD=
# The purge of this start reached PURGING (its report names the run's pre-purge backup): it may have deleted something.
PURGE_STARTED=
# step NAME COMMAND ARGS...: one history-archive command; report REC/NAME.json, stderr REC/NAME.log; sets RC, RESULT,
# VALID and STATE (when the report names a state).
step() {
    STEP_NAME=$1
    STEP_CMD=$2
    shift
    RC=0
    # The copy folder may have dropped out during a long step: the rule is evaluated before every step.
    copy_rule
    CURRENT_STEP=$STEP_CMD
    job "$@" >"$REC/$STEP_NAME.json" 2>"$REC/$STEP_NAME.log" || RC=$?
    CURRENT_STEP=
    VALID=
    if report_valid "$REC/$STEP_NAME.json" "$RC"; then
        VALID=1
        RESULT=$(json_field "$REC/$STEP_NAME.json" result)
        state=$(json_field "$REC/$STEP_NAME.json" state)
        [ -z "$state" ] || STATE=$state
        summary=$(tail -n 1 "$REC/$STEP_NAME.log" 2>/dev/null) || summary=
        say "${summary:-history-archive $STEP_CMD: $RESULT}"
    else
        RESULT=
        say "history-archive $STEP_CMD gave no valid report (exit $RC; $REC/$STEP_NAME.json, $REC/$STEP_NAME.log)."
        tail -n 3 "$REC/$STEP_NAME.log" >&2 2>/dev/null || true
    fi
}
REQUEST_NEW="Request a new run in Administration → History archival & purge (OPERATIONS_RUNBOOK §11)."
# failed: the step STEP_CMD (record STEP_NAME) did not end with exit 0: set EXIT and say why (the run's state is in the
# report).
failed() {
    KEEP=1
    message=$(error_message "$REC/$STEP_NAME.json")
    [ -n "$message" ] || message="see $REC/$STEP_NAME.json."
    if [ -z "$VALID" ]; then
        EXIT=2
        case "$STEP_CMD" in
            export | verify | copy)
                say "the outcome of $STEP_CMD is unknown. The next start recovers a run left RUNNING (it becomes FAILED); nothing was deleted." ;;
            purge)
                say "the outcome of the purge of run ${RUN_ID:-?} is unknown. Whole components only are ever deleted: the next start resumes a run left PURGING and reconciles when its purge ends, or fails a run that is still RUNNING (then nothing was deleted)." ;;
        esac
        return 0
    fi
    case "$RC" in
        1)
            EXIT=1
            say "history-archive $STEP_CMD refused: $message"
            if [ "$STEP_CMD" = purge ] && [ "$STATE" = PURGING ]; then
                say "run ${RUN_ID:-?} stays PURGING: the next start resumes its purge and reconciles when it ends."
            elif [ "$STEP_CMD" = purge ]; then
                say "Nothing was deleted. Run ${RUN_ID:-?} stays RUNNING, and the next start fails it (recover). $REQUEST_NEW"
            fi
            ;;
        3)
            EXIT=3
            if [ "$STEP_CMD" = purge ] && [ -n "$PURGE_STARTED" ]; then
                say "run ${RUN_ID:-?} FAILED while purging: $message Whole components only were deleted; the purge receipt $ARCHIVE_DIR/${ARCHIVE_NAME:-<archive name>}.purged.json lists what was deleted and what stays in the database. Request a new run for the rest in Administration → History archival & purge (OPERATIONS_RUNBOOK §11)."
            else
                say "run ${RUN_ID:-?} FAILED: $message Nothing was deleted. $REQUEST_NEW"
            fi
            ;;
        *)
            EXIT=2
            if [ "$STEP_CMD" = purge ] && [ "$STATE" = PURGING ]; then
                # A transient database error or a lost connection while purging (design §7.5): the run stays
                # PURGING, and the reconcile waits for the resumed purge to end.
                say "the purge of run ${RUN_ID:-?} stopped: $message"
                say "run ${RUN_ID:-?} stays PURGING: the batch in progress was rolled back (whole components only are ever deleted), and the next start resumes the purge and reconciles when it ends."
            else
                say "history-archive $STEP_CMD could not run: $message"
                [ "$STEP_CMD" != purge ] \
                    || say "Nothing was deleted. Run ${RUN_ID:-?} stays RUNNING, and the next start fails it (recover). $REQUEST_NEW"
            fi
            ;;
    esac
}

# reconcile_after_purge: the full reconcile right after a purge (contract §3.1, design §10), in a one-off backend
# container as the application database role, as release.sh and scheduled-reconcile.sh run it. Not clean (a finding,
# an error, no complete report): an incident, exit 4 and the incident record. No earlier record can exist here: while
# one exists, no start purges.
reconcile_after_purge() {
    KEEP=1
    say "run ${RUN_ID:-?}: the full reconcile after the purge (python -m app.cli reconcile in a one-off backend container)."
    CURRENT_STEP=reconcile
    rrc=0
    pf run --rm --no-deps -T backend python -m app.cli reconcile --max-findings 10000 \
        >"$REC/reconcile.json" 2>"$REC/reconcile.log" || rrc=$?
    CURRENT_STEP=
    result=
    if report_valid "$REC/reconcile.json" "$rrc"; then
        result=$(json_field "$REC/reconcile.json" result)
    fi
    if [ "$rrc" -eq 0 ] && [ "$result" = clean ]; then
        RECONCILE_PENDING=
        say "the reconcile after the purge of run ${RUN_ID:-?} is clean ($REC/reconcile.json)."
        return 0
    fi
    EXIT=4
    checks=$(failing_checks "$REC/reconcile.json")
    case "$rrc:$result" in
        1:mismatch) what="found a mismatch${checks:+ (checks $checks)}" ;;
        2:error) what="ended in error${checks:+ (checks $checks)}" ;;
        *)
            result=could_not_run
            what="could not run (exit $rrc, no complete report)"
            ;;
    esac
    record_incident "$result" "$rrc" "$checks"
    RECONCILE_PENDING=
    tail -n 3 "$REC/reconcile.log" >&2 2>/dev/null || true
    say "INCIDENT — the reconcile after the purge of run ${RUN_ID:-?} $what: $REC/reconcile.json. Stop requesting runs and follow OPERATIONS_RUNBOOK §8 (Reconcile finding after a purge): the purge receipt is $ARCHIVE_DIR/${ARCHIVE_NAME:-<archive name>}.purged.json${BACKUP_NAME:+ and the pre-purge backup $BACKUP_NAME}; $INCIDENT_FILE records the incident, and no start works on a run until you remove it after the investigation."
}

# pre_purge_backup RECORD: the pre-purge backup of run RUN_ID that `next` (report RECORD) asked for: backup.sh --kind
# pre-purge, which publishes and verifies it like every backup and waits up to --backup-wait-minutes for a backup that
# holds the backup lock. Sets BACKUP_NAME and BACKUP_STARTED (its dump start, from its backup-verify report). On a
# failure it sets EXIT, says why and returns 1: nothing was deleted, and the run stays RUNNING for the next start's
# recover, which fails it.
pre_purge_backup() {
    kind=$(json_nested "$REC/$1.json" backup kind)
    label=$(json_nested "$REC/$1.json" backup label)
    if [ "$kind" != pre-purge ] || ! printf '%s' "$label" | grep -Eq "$ARCHIVE_NAME_PATTERN"; then
        EXIT=2
        say "history-archive next asked for a backup of run ${RUN_ID:-?} without the pre-purge kind and archive name ($REC/$1.json). Nothing was deleted; the run stays RUNNING, and the next start fails it (recover). $REQUEST_NEW"
        return 1
    fi
    ARCHIVE_NAME=$label
    say "run ${RUN_ID:-?}: the pre-purge backup (backup.sh --kind pre-purge --label $label; it waits up to $BACKUP_WAIT_MINUTES min for a backup that is running)."
    CURRENT_STEP=backup
    brc=0
    sh "$SCRIPT_DIR/backup.sh" --kind pre-purge --label "$label" --operator archive.sh \
        --reason "pre-purge backup for $label" --env-file "$ENV_FILE" --lock-wait-minutes "$BACKUP_WAIT_MINUTES" \
        >"$REC/backup.out" 2>"$REC/backup.log" || brc=$?
    CURRENT_STEP=
    line=$(sed -n '/^BACKUP /p' "$REC/backup.out" 2>/dev/null | tail -n 1)
    BACKUP_NAME=$(printf '%s\n' "$line" | awk 'NF == 4 { print $2 }')
    BACKUP_STARTED=$(printf '%s\n' "$line" | awk 'NF == 4 { print $4 }')
    if [ "$brc" -eq 0 ] && printf '%s' "$BACKUP_NAME" | grep -Eq "^[0-9]{8}T[0-9]{6}Z-pre-purge-$label\$" \
        && printf '%s' "$BACKUP_STARTED" | grep -Eq "$UTC_PATTERN"; then
        say "run ${RUN_ID:-?}: the pre-purge backup $BACKUP_NAME is published and verified (its dump started at $BACKUP_STARTED)."
        return 0
    fi
    EXIT=2
    tail -n 3 "$REC/backup.log" >&2 2>/dev/null || true
    if [ "$brc" -eq 0 ]; then
        why="backup.sh did not name the pre-purge backup with its dump start ($REC/backup.out)"
    else
        why="the pre-purge backup failed (backup.sh exit $brc; $REC/backup.log)"
    fi
    BACKUP_NAME=
    say "run ${RUN_ID:-?}: $why. Nothing was deleted. The run stays RUNNING, and the next start fails it (recover). $REQUEST_NEW"
    return 1
}

# purge_run [--backup NAME --backup-started-at UTC]: purge --run RUN_ID (with the pre-purge backup just taken, or alone
# to resume a run left PURGING), then the reconcile when the purge ended after deleting something: exit 0, or exit 3
# once the run was purging (the report names its pre-purge backup). Exit 2 while PURGING (a transient database error)
# leaves the run PURGING: the next start resumes it and reconciles then, so no reconcile runs now.
purge_run() {
    step purge purge --run "$RUN_ID" "$@"
    name=$(json_field "$REC/purge.json" archive_name)
    [ -z "$name" ] || ARCHIVE_NAME=$name
    PURGE_STARTED=
    if [ -n "$VALID" ]; then
        recorded=$(json_nested "$REC/purge.json" backup name)
        if [ -n "$recorded" ]; then
            PURGE_STARTED=1
            BACKUP_NAME=$recorded
        fi
    fi
    if [ "$RC" -eq 0 ] && [ -n "$VALID" ]; then
        OUTCOME=$RESULT
        RECONCILE_PENDING=1
        receipt=$(json_nested "$REC/purge.json" receipt name)
        say "run $RUN_ID: $(json_nested_number "$REC/purge.json" purged components) components ($(json_nested_number "$REC/purge.json" purged flows) flows, $(json_nested_number "$REC/purge.json" purged movements) Movements) purged, $(json_nested_number "$REC/purge.json" skipped components) components ($(json_nested_number "$REC/purge.json" skipped flows) flows) skipped; batches this start: $(json_number "$REC/purge.json" batches); purge receipt $ARCHIVE_DIR/${receipt:-?} (SHA-256 $(json_nested "$REC/purge.json" receipt sha256))."
        problem=$(json_nested "$REC/purge.json" receipt copy_problem)
        [ -z "$problem" ] || say "$problem"
        reconcile_after_purge
        return 0
    fi
    failed
    if [ "$RC" -eq 3 ] && [ -n "$VALID" ] && [ -n "$PURGE_STARTED" ]; then
        RECONCILE_PENDING=1
        reconcile_after_purge
    fi
    return 0
}

step recover recover
if [ "$RC" -ne 0 ] || [ -z "$VALID" ]; then
    # Without a recover, no run is worked on: a run left RUNNING (also one restored from its pre-purge backup) must be
    # failed before `next` could hand out its backup and purge again.
    failed
else
    if [ "$RESULT" = recovered ]; then
        KEEP=1
        for path in $(json_list "$REC/recover.json" kept_partial); do
            say "kept the unfinished folder $path: its name belongs to no finished run (OPERATIONS_RUNBOOK §11)."
        done
        for path in $(json_list "$REC/recover.json" copied_receipts); do
            say "copied the purge receipt $path into the copy folder (it was missing there or differed)."
        done
        stale=$(json_list "$REC/recover.json" failed_run_ids | tr '\n' ' ' | sed 's/ *$//')
        if [ -n "$stale" ]; then
            # A start stopped, or its pre-purge backup failed, while its run was RUNNING: that run is FAILED now.
            EXIT=3
            say "run(s) $stale stopped while RUNNING and are FAILED now (an interrupted job, a pre-purge backup that failed, or a database restored from a pre-purge backup). Nothing was deleted in this database. $REQUEST_NEW"
        fi
    fi
    problems=$(json_list "$REC/recover.json" receipt_problems)
    if [ -n "$problems" ]; then
        KEEP=1
        printf '%s\n' "$problems" | while IFS= read -r problem; do
            say "a purge receipt was not copied: $problem (the next start tries again; status reports receipt_not_copied meanwhile, OPERATIONS_RUNBOOK §11)."
        done
    fi

    # Fail closed (design §10): while the incident record of a reconcile after a purge exists, a start runs recover and
    # status only. `next` is not asked, so no step that changes data runs: no export, verify, copy, pre-purge backup or
    # purge, also no resumed purge of a run left PURGING. Exit 1, unless recover already set a code (3: it failed a run,
    # as after a restore of a pre-purge backup). Only the operator removes the record (OPERATIONS_RUNBOOK §8).
    INCIDENT_OPEN=
    if [ -e "$INCIDENT_FILE" ]; then
        INCIDENT_OPEN=1
        [ "$EXIT" -ne 0 ] || EXIT=1
        incident_run=$(sed -n 's/^run=//p' "$INCIDENT_FILE" 2>/dev/null | head -n 1)
        incident_result=$(sed -n 's/^result=//p' "$INCIDENT_FILE" 2>/dev/null | head -n 1)
        say "refused — the incident record $INCIDENT_FILE of a reconcile after a purge exists (run ${incident_run:-?}, result ${incident_result:-unknown}): no run is worked on while it exists, no export, verification, copy, pre-purge backup or purge, not even the resumed purge of a run left PURGING. Follow OPERATIONS_RUNBOOK §8 (Reconcile finding after a purge); once the investigation is done and the reconciliation is clean, remove the record (rm $INCIDENT_FILE), and the next start goes on."
    fi

    # The steps that `next` names, until it answers wait, running or none, a run finishes, or a step fails (contract
    # §3.1): export → verify → copy → backup → purge in one start. After a purge no further run starts in this start.
    # Each step runs at most once per run in one start: `next` naming a step again means that a step made no progress,
    # and the start ends instead of looping. With the incident record (above), the loop does not run at all.
    NEXT_COUNT=0
    DONE_STEPS=' '
    while [ -z "$INCIDENT_OPEN" ]; do
        NEXT_COUNT=$((NEXT_COUNT + 1))
        if [ "$NEXT_COUNT" -eq 1 ]; then record=next; else record=next-$NEXT_COUNT; fi
        step "$record" next
        run=$(json_number "$REC/$record.json" run_id)
        [ -z "$run" ] || RUN_ID=$run
        if [ "$RC" -ne 0 ] || [ -z "$VALID" ]; then
            # Exit 3: the requester re-check failed the run before its backup (contract §2).
            failed
            break
        fi
        action=$RESULT
        case "$DONE_STEPS" in
            *" $action:${RUN_ID:-} "*)
                EXIT=2
                KEEP=1
                say "history-archive next answered $action again for run ${RUN_ID:-?}: a step of this start made no progress ($REC/$record.json). The next start tries again."
                break
                ;;
        esac
        DONE_STEPS="$DONE_STEPS$action:${RUN_ID:-} "
        OUTCOME=$action
        case "$action" in
            export)
                KEEP=1
                step export export --run "$RUN_ID"
                if [ "$RC" -ne 0 ] || [ -z "$VALID" ]; then failed; break; fi
                if [ "$RESULT" = nothing_eligible ]; then
                    OUTCOME=nothing_eligible
                    break
                fi
                ;;
            verify)
                KEEP=1
                step verify verify --run "$RUN_ID"
                if [ "$RC" -ne 0 ] || [ -z "$VALID" ]; then failed; break; fi
                if [ "$RESULT" = nothing_to_purge ]; then
                    OUTCOME=nothing_to_purge
                    say "run $RUN_ID: every component changed after its export, so nothing is left to purge: the run is COMPLETED_WITH_SKIPS (NOTHING_PURGED), with its receipt, and needs no copy and no pre-purge backup."
                    break
                fi
                ;;
            copy)
                # A waiting run whose copy still waits (no usable copy folder) changes nothing: no record each hour.
                [ "$STATE" = WAITING_FOR_COPY ] || KEEP=1
                step copy copy --run "$RUN_ID"
                if [ "$RC" -ne 0 ] || [ -z "$VALID" ]; then failed; break; fi
                if [ "$RESULT" = waiting_for_copy ]; then
                    OUTCOME=waiting_for_copy
                    problem=$(json_field "$REC/copy.json" copy_problem)
                    [ -z "$problem" ] || say "$problem"
                    say "run $RUN_ID waits for its off-host copy: copy the archive folder off the host and confirm it in Administration (OPERATIONS_RUNBOOK §11)."
                    break
                fi
                KEEP=1
                ;;
            backup)
                KEEP=1
                if pre_purge_backup "$record"; then
                    purge_run --backup "$BACKUP_NAME" --backup-started-at "$BACKUP_STARTED"
                fi
                break
                ;;
            purge)
                KEEP=1
                say "run $RUN_ID was left PURGING by an earlier start: its purge resumes without a new backup (whole components only were deleted)."
                purge_run
                break
                ;;
            wait)
                say "run $RUN_ID waits for its off-host copy to be confirmed in Administration → History archival & purge."
                break
                ;;
            running)
                say "run $RUN_ID is RUNNING in another history-archive command."
                break
                ;;
            none) break ;;
            *)
                EXIT=2
                KEEP=1
                say "history-archive next answered an unknown result ${action:-(none)} ($REC/$record.json)."
                break
                ;;
        esac
    done
fi

# status for the monitoring line (its findings never change the exit status; check.sh reports them)
step status status
if [ -n "$VALID" ]; then
    findings=$(json_list "$REC/status.json" findings | tr '\n' ' ' | sed 's/ *$//')
    [ -z "$findings" ] || say "status findings: $findings (OPERATIONS_RUNBOOK §11)."
fi

if [ "$EXIT" -eq 0 ]; then
    echo "ARCHIVE ${OUTCOME:-none} ${RUN_ID:--} ${STATE:--}"
fi
[ -z "$KEEP" ] || say "reports: $REC"
exit "$EXIT"
