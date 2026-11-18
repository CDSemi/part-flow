#!/bin/sh
# PartFlow history archival host job (Movement-history archival, build step 1; OPERATIONS_RUNBOOK §11, DEPLOYMENT §3.1).
# Run from the repository root of the RUNNING release's checkout, as the account that owns PARTFLOW_ARCHIVE_DIR (root on
# the NAS). `deploy/production/archive.sh --help` prints the usage. Step 1 exports and copies archives and NEVER deletes
# anything: every run ends as COMPLETED with the result EXPORT_ONLY, or as NOTHING_ELIGIBLE, CANCELLED or FAILED.
#
# The hourly work runs the steps of `python -m app.cli history-archive` in the one-shot `maintenance` service (as the
# maintenance database role, the archive folder mounted, `--user` this account): recover; next; then, as `next` says,
# export --run N, verify --run N and copy --run N (or copy --run N alone); and status at the end. Each step's JSON
# report and stderr go to <records-dir>/archive/<UTC stamp>/; that folder is kept only when a step did something or
# failed. The optional off-host copy folder (PARTFLOW_ARCHIVE_COPY_DIR) is mounted at /archive-copy only while it
# exists, is writable, and lies on a file system that is neither the archive folder's nor the root file system `/` (a
# disk or share that dropped out leaves its path on `/`); the rule is evaluated again before every step, so a copy
# folder that vanishes during a long export is not used by the copy. The mount is a long-syntax bind with
# create_host_path: false in a generated Compose override file (`docker compose run` has no --mount; its -v would
# create a missing host path), so Docker refuses a source that vanished in between. Otherwise the job finds
# /archive-copy unusable and the run waits for a copy confirmation in Administration.
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
# a run merely waits for its copy confirmation. archive.sh refuses while <records-dir>/.release.lock exists, and
# release.sh refuses while the archive lock is held. A lock whose owner provably no longer exists (same host, no
# `maintenance` container running, and its pid gone, or the boot id or the pid's start time differs from the recorded
# one) is removed automatically: the job's `recover` then fails a run left RUNNING. The database itself is guarded by
# the job's advisory lock.
#
# stdout on exit 0: exactly one line, `NOT_DUE`, `DEFERRED <reason>` (scheduled: the previous start still works on
# its hourly work), or `ARCHIVE <outcome> <run id|-> <state|->`. stderr: progress, each step's one-line summary, errors.
# Exit status: 0 done (or nothing due); 1 refused, nothing changed (a release runs, the archive lock is held, a step
# refused); 2 could not run (configuration, Docker, database, an unknown outcome); 3 a run failed: it is now FAILED and
# a new run must be requested in Administration → History archival & purge.
set -eu

# The values in the shell would override the env file for every Compose command: the script sets them per command only.
unset PARTFLOW_RELEASE PARTFLOW_COMMIT PARTFLOW_ACCEPT_SCHEMA_REVISION PARTFLOW_BACKUP_DIR PARTFLOW_ARCHIVE_DIR \
    PARTFLOW_ARCHIVE_COPY_DIR COMPOSE_PROJECT_NAME COMPOSE_FILE COMPOSE_PROFILES
# Records, logs and the stamp are readable by the owning account only.
umask 077

RELEASE_PATTERN='^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'
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

Run from the repository root of the running release's checkout, as the account that owns PARTFLOW_ARCHIVE_DIR.
Runs the hourly work of Movement-history archival in the `maintenance` service: recover, next, then export, verify
and copy as `next` says, and status. Build step 1: it exports and copies archives and never deletes anything.
  --scheduled           the host task that starts every 5 minutes: the hourly work runs once per hour, at the first
                        start at or after the Start minute of Administration → History archival & purge; every other
                        start ends at once (NOT_DUE)
  --env-file FILE       default .env.production
  --records-dir DIR     default $HOME/partflow-deployments: the release lock .release.lock, the archive lock
                        .archive.lock, the hourly stamp archive/hourly-slot and the reports archive/<UTC stamp>/
Exit: 0 done (or nothing due), 1 refused (a release runs, the archive lock is held, a step refused; nothing changed),
      2 could not run, 3 a run failed: it is FAILED; request a new run in Administration → History archival & purge.
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
        --env-file | --records-dir)
            [ "$#" -ge 2 ] || usage_error "$option needs a value."
            value=$2
            shift 2
            ;;
        *) usage_error "unknown argument $option." ;;
    esac
    case "$option" in
        --env-file) ENV_FILE=$value ;;
        --records-dir) RECORDS_DIR=$value ;;
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
on_signal() {
    trap - INT TERM
    KEEP=1
    say "interrupted. The next start recovers a run left RUNNING (it becomes FAILED); nothing was deleted."
    exit 2
}
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
        # A step runs, or the owner is between two steps: a scheduled start leaves this hour to it.
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
# The hourly work (contract §2.4)
# ---------------------------------------------------------------------------

EXIT=0
RUN_ID=
STATE=
OUTCOME=
# step NAME ARGS...: one history-archive command; report REC/NAME.json, stderr REC/NAME.log; sets RC, RESULT, VALID.
step() {
    name=$1
    shift
    RC=0
    # The copy folder may have dropped out during a long step: the rule is evaluated before every step.
    copy_rule
    job "$@" >"$REC/$name.json" 2>"$REC/$name.log" || RC=$?
    VALID=
    if report_valid "$REC/$name.json" "$RC"; then
        VALID=1
        RESULT=$(json_field "$REC/$name.json" result)
        state=$(json_field "$REC/$name.json" state)
        [ -z "$state" ] || STATE=$state
        summary=$(tail -n 1 "$REC/$name.log" 2>/dev/null) || summary=
        say "${summary:-history-archive $name: $RESULT}"
    else
        RESULT=
        say "history-archive $name gave no valid report (exit $RC; $REC/$name.json, $REC/$name.log)."
        tail -n 3 "$REC/$name.log" >&2 2>/dev/null || true
    fi
}
# failed NAME: the step NAME did not end with exit 0: set EXIT and say why (the run's state is in the report).
failed() {
    KEEP=1
    message=$(error_message "$REC/$1.json")
    if [ -z "$VALID" ]; then
        EXIT=2
        case "$1" in
            export | verify | copy)
                say "the outcome of $1 is unknown. The next start recovers a run left RUNNING (it becomes FAILED); nothing was deleted." ;;
        esac
        return 0
    fi
    case "$RC" in
        1)
            EXIT=1
            say "history-archive $1 refused: ${message:-see $REC/$1.json}"
            ;;
        3)
            EXIT=3
            say "run ${RUN_ID:-?} FAILED: ${message:-see $REC/$1.json} Nothing was deleted. Request a new run in Administration → History archival & purge (OPERATIONS_RUNBOOK §11)."
            ;;
        *)
            EXIT=2
            say "history-archive $1 could not run: ${message:-see $REC/$1.json}"
            ;;
    esac
}

step recover recover
if [ "$RC" -ne 0 ] || [ -z "$VALID" ]; then
    failed recover
else
    if [ "$RESULT" = recovered ]; then
        KEEP=1
        for path in $(json_list "$REC/recover.json" kept_partial); do
            say "kept the unfinished folder $path: its name belongs to no finished run (OPERATIONS_RUNBOOK §11)."
        done
        stale=$(json_list "$REC/recover.json" failed_run_ids | tr '\n' ' ' | sed 's/ *$//')
        if [ -n "$stale" ]; then
            # An earlier start stopped while its run was RUNNING: that run is FAILED now.
            EXIT=3
            say "run(s) $stale stopped while RUNNING and are FAILED now. Nothing was deleted. Request a new run in Administration → History archival & purge (OPERATIONS_RUNBOOK §11)."
        fi
    fi
    step next next
    if [ "$RC" -ne 0 ] || [ -z "$VALID" ]; then
        failed next
    else
        RUN_ID=$(json_number "$REC/next.json" run_id)
        OUTCOME=$RESULT
        case "$RESULT" in
            export)
                KEEP=1
                step export export --run "$RUN_ID"
                if [ "$RC" -ne 0 ] || [ -z "$VALID" ]; then
                    failed export
                elif [ "$RESULT" = nothing_eligible ]; then
                    OUTCOME=nothing_eligible
                else
                    step verify verify --run "$RUN_ID"
                    if [ "$RC" -ne 0 ] || [ -z "$VALID" ]; then
                        failed verify
                    else
                        step copy copy --run "$RUN_ID"
                        if [ "$RC" -ne 0 ] || [ -z "$VALID" ]; then failed copy; else OUTCOME=$RESULT; fi
                    fi
                fi
                ;;
            copy)
                step copy copy --run "$RUN_ID"
                if [ "$RC" -ne 0 ] || [ -z "$VALID" ]; then
                    failed copy
                else
                    OUTCOME=$RESULT
                    # A copy that still waits (the copy folder is unavailable) changed nothing: no record each hour.
                    [ "$RESULT" != completed ] || KEEP=1
                fi
                ;;
            wait) say "run $RUN_ID waits for its off-host copy to be confirmed in Administration → History archival & purge." ;;
            running) say "run $RUN_ID is RUNNING in another history-archive command." ;;
            none) ;;
            *)
                EXIT=2
                KEEP=1
                say "history-archive next answered an unknown result ${RESULT:-(none)} ($REC/next.json)."
                ;;
        esac
        if [ "$OUTCOME" = waiting_for_copy ]; then
            problem=$(json_field "$REC/copy.json" copy_problem)
            [ -z "$problem" ] || say "$problem"
            say "run $RUN_ID waits for its off-host copy: copy the archive folder off the host and confirm it in Administration (OPERATIONS_RUNBOOK §11)."
        fi
    fi
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
