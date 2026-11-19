#!/bin/sh
# PartFlow history archival host job (Movement-history archival, build steps 1 to 5; OPERATIONS_RUNBOOK §10, §11,
# DEPLOYMENT §3.1). Run from the repository root of the RUNNING release's checkout, as the account that owns
# PARTFLOW_ARCHIVE_DIR and PARTFLOW_BACKUP_DIR (root on the NAS). `deploy/production/archive.sh --help` prints the usage.
# This job DELETES, always after a pre-purge backup and with the full reconcile right after: it purges the archived
# Movement history of a run whose archive is exported, verified and copied off the host (whole flow components, in
# batches), and it carries out the permanent deletion requests recorded in Administration or by `pf delete-permanently`
# (the rows the request stored, deleted in one transaction). It also creates the automatic runs that Administration →
# History archival & purge switches on (`evaluate`), which delete nothing by themselves, and it loads the archive of a
# run that a person opened for viewing into the viewer tables of the database, and removes those rows once the view is
# closed (`load-view`, `close-view`); viewing never writes live history.
#
# A start runs the steps of `python -m app.cli history-archive` in the one-shot `maintenance` service (as the
# maintenance database role, the archive folder mounted, `--user` this account): `recover`; at the hourly work
# `evaluate`; then `next` and the step it names, again and again until `next` answers wait, running or none, a run or a
# deletion finishes, or a step fails.
# - The automatic runs (contract B4 §3.1, §3.2): `evaluate` runs at the hourly work only, after `recover` and before the
#   `next` loop; never in a start taken only for a deletion request or for viewing work, and never while the incident
#   record exists. It creates the automatic run that the settings of Administration call for now, or says why it
#   creates none: PROPOSED, which waits for a person to approve or dismiss it in Administration (`next` answers wait), or
#   APPROVED under the standing authorization of the approval setting, which this start works on like a requested run.
#   Its decision (`result` and `message`) is logged. When it is refused or cannot run (exit 1 or 2, or no valid report),
#   the error is logged and the start's exit status reports it, and the `next` loop still works on the open run.
# - A permanent deletion request comes first. `start_deletion`: start-deletion --request N (the request becomes RUNNING,
#   or FAILED when the User who requested it may no longer have it carried out). `deletion_backup`: the pre-purge backup
#   (deploy/production/backup.sh --kind pre-purge --label deletion-<id>-<UTC stamp>), then delete --request N --backup
#   NAME --backup-started-at UTC, which deletes the request's stored rows in one transaction, then the full reconcile. A
#   start that worked on a deletion does no run work and leaves the hourly slot to the next start.
# - The viewing work comes next (contract B5 §3.6), closing before loading. `close_view`: close-view --view N removes
#   the loaded rows of a view that a person closed (CLOSING), or of an OPEN view whose days have run out, and the view
#   becomes CLOSED. `load_view`: load-view --view N checks every file of the run's archive folder, then loads it into
#   the viewer tables (REQUESTED → LOADING → OPEN). Exit 0: `next` is asked again. A load that fails ends the start with
#   exit 3 (the view is FAILED, its loaded rows removed; its archive folder may be damaged); a load or a closing that a
#   database error interrupted ends it with exit 2 (the view stays LOADING, which the next start's `recover` fails, or
#   CLOSING, which the next start closes); a refusal with exit 1, except `wrong_state`: the view changed after `next`
#   handed it out (a person cancelled it before its load, or its days were raised before its closing), which changed
#   nothing and is logged before `next` is asked again. Viewing work never marks the hourly slot, and a start taken
#   only for it does no run work.
# - The run work: export --run N, verify --run N and copy --run N; for `backup`, the pre-purge backup (backup.sh --kind
#   pre-purge --label <archive name>), then purge --run N --backup NAME --backup-started-at UTC; for `purge` (a run an
#   earlier start left PURGING), purge --run N alone. After a purge that ended with exit 0, or with exit 3 once the run
#   was purging, the full reconcile runs, and the start begins no other run. A purge that ends with exit 2 (a transient
#   database error or a lost connection while PURGING: the batch in progress rolled back, and the run stays PURGING) is
#   not reconciled in that start: the next start resumes the purge and reconciles when it ends.
# A pre-purge backup waits up to --backup-wait-minutes for a backup that holds the backup lock. The full reconcile is
# `python -m app.cli reconcile` in a one-off `backend` container as the application database role, as release.sh and
# scheduled-reconcile.sh run it. Every start ends with `status`.
#
# A run or a deletion request left RUNNING (its pre-purge backup failed, the job stopped, or a deletion stopped on a
# database error that does not last) is failed by the next start's `recover`, on purpose: the pre-purge backup is taken
# while it is RUNNING, so a database restored from that backup never purges or deletes again. A deletion is one
# transaction, so a request that failed or stayed RUNNING deleted nothing. Whenever `delete` ends with exit 2 (a
# database error that does not last, a step that could not run, an outcome unknown) or gives no valid report, the start
# reads `status`: a COMPLETED request is reconciled at once, a FAILED one or one still RUNNING deleted nothing, and when
# `status` cannot tell, the reconcile runs anyway. A database restored from a backup taken while a run was PURGING
# (an hourly backup during a purge) holds that run PURGING, and the next start resumes its purge; one restored from a
# backup taken between a deletion request and its deletion holds the request APPROVED, and the next start carries it
# out: disable the task before any restore (OPERATIONS_RUNBOOK §6). A restored database holds the views of an archive
# too: one LOADING is failed by the next start's `recover` (its loaded rows removed), one REQUESTED is loaded, and one
# OPEN keeps its rows until a person closes it or its days run out.
#
# Each step's JSON report and stderr go to <records-dir>/archive/<UTC stamp>/ (`next`, then `next-2`, `next-3`, ...;
# `load-view-<view id>` and `close-view-<view id>`; backup.out and backup.log of backup.sh; reconcile.json and
# reconcile.log); that folder is kept only when a step did something or failed. A reconcile after a purge or a deletion
# that does not end clean (a finding, an error, no complete report) is an incident (design §10): archive.sh exits 4 and
# writes the incident record <records-dir>/archive/reconcile-after-purge (key=value lines: result, exit code, run,
# archive name, request, pre-purge backup, failing checks, time, report path), which check.sh reports as FAIL
# archival_proposal reconcile_after_purge.
# While the record exists, the job fails closed: a start runs `recover` and `status` only and refuses every step that
# changes data (the evaluation that creates automatic runs, export, verify, copy, the pre-purge backups, the purge, the
# resumed purge of a run left PURGING, every deletion, and the load and the closing of a view) with exit 1. Only the
# operator removes the record, once the investigation is done (OPERATIONS_RUNBOOK §8).
#
# The optional off-host copy folder (PARTFLOW_ARCHIVE_COPY_DIR) is mounted at /archive-copy only while it exists, is
# writable, and lies on a file system that is neither the archive folder's nor the root file system `/` (a disk or
# share that dropped out leaves its path on `/`); the rule is evaluated again before every step, so a copy folder that
# vanishes during a long export is not used by the copy. The mount is a long-syntax bind with create_host_path: false in
# a generated Compose override file (`docker compose run` has no --mount; its -v would create a missing host path), so
# Docker refuses a source that vanished in between. Otherwise the job finds /archive-copy unusable and the run waits for
# a copy confirmation in Administration.
#
# `--scheduled` (the host task that starts every 5 minutes, design D12): every start runs the cheap check first,
# `python -m app.cli history-archive settings` with `docker compose exec` in the running `backend` container (indexed
# reads of small tables as the application database role: whether a permanent deletion request is open, whether a view
# of an archive waits for the job, and the Start minute of Administration → History archival & purge). A start that
# finds a request open, or viewing work (a view to close, a view to load, or a load that a stopped start left), does
# that work at once, whatever the time. Otherwise the hourly work runs once per hour, at the first start at or after the
# Start minute. The hourly stamp <records-dir>/archive/hourly-slot holds the epoch minute of the slot last attempted. A
# start within an hour of it looks only for an open deletion request and for viewing work and otherwise ends at once
# (NOT_DUE); until the check finds such work, it ends quietly as NOT_DUE also when a release runs, the incident record
# exists, the configuration is incomplete or the check gives no answer (the backend is down): the next hourly start
# reports such a problem. When the check gives no answer at an hourly start, the Start minute is read in the
# `maintenance` service, and the hourly work's `next` still hands out a waiting deletion request and viewing work first.
# The first scheduled start (no stamp yet) only records the current slot, so the hourly work starts at the next Start
# minute. A slot is attempted, and its stamp written, once `next` answers anything but deletion or viewing work, or once
# the start is refused or fails before it could ask: a refusal or a failure is then reported once for that hour, never at
# every 5-minute start. A start that worked on a deletion request leaves the slot to the next start, and so does one
# whose viewing work ended it before `next` answered anything else (a viewing step that failed): the next start does the
# hourly work, `evaluate` included (a start due for the hourly work evaluates before its deletion or viewing work too; a
# second evaluation in that hour creates nothing new while the run it created is open or the days between automatic
# runs have not passed). While a request is open or viewing work waits, every start tries to do it, so a failure that
# keeps it from being done (a `maintenance` step that cannot run, a closing that a database error keeps stopping) is
# reported by each 5-minute start until it is fixed.
#
# Locks: <records-dir>/.archive.lock (owner file: host, pid, started_at, by, and the kernel boot id and the process
# start time from /proc) is taken only for the hourly work, a deletion and viewing work, never by a start that only
# checks the time and never while a run merely waits for its copy confirmation. It is held for the whole work, the wait
# for the backup lock, the purge or the deletion and the reconcile, and the load or the closing of a view included, so
# release.sh refuses during all of them. archive.sh refuses while
# <records-dir>/.release.lock exists, and release.sh refuses while the archive lock is held, so no live release holds the
# backup lock while a pre-purge backup waits for it (a backup lock left by a killed release.sh is stale, and backup.sh
# refuses it at once). A lock whose owner provably no longer exists (same host, no `maintenance` container running, and
# its pid gone, or the boot id or the pid's start time differs from the recorded one) is removed automatically: the
# job's `recover` then fails a run or a deletion request left RUNNING, and a view left LOADING. The database itself is
# guarded by the job's advisory lock.
#
# stdout on exit 0: exactly one line, `NOT_DUE`, `DEFERRED <reason>` (scheduled: the previous start still works),
# `EVALUATE <result> <run id|-> <state|->` for an hourly work that worked on no run and no view: the decision of
# `evaluate`, then the open run that `next` named and its state (for example `EVALUATE off - -`,
# `EVALUATE created 7 PROPOSED` or `EVALUATE run_open 3 WAITING_FOR_COPY`); `ARCHIVE <outcome> <run id|-> <state|->` for
# one that worked on a run; `VIEW <outcome> <view id> <state>` for a start that worked on views of an archive and on no
# run, naming the last view it worked on (for example `VIEW loaded 4 OPEN` or `VIEW closed 3 CLOSED`); or, for a start
# that worked on a deletion request, `DELETION <outcome> <request id> <state>` (for example
# `DELETION deleted 7 COMPLETED`). stderr: progress, each step's one-line summary, errors.
# Exit status: 0 done (or nothing due); 1 refused, nothing changed (a release runs, the archive lock is held, a step
# refused, the incident record exists); 2 could not run (configuration, Docker, database, an evaluation of the automatic
# runs that did not finish, an unknown outcome), or the pre-purge backup failed, or a deletion was interrupted (nothing
# was deleted; the next start fails a run or a deletion request left RUNNING), or the load or the closing of a view was
# interrupted (the next start fails a view left LOADING, or closes a view left CLOSING); 3 a run or a deletion request
# failed: it is now FAILED, and a new run, or the deletion, must be requested again (a run that failed while purging
# keeps the whole components it purged, which its purge receipt lists; a failed deletion request deleted nothing), or the
# load of a view failed, also one that a stopped start left LOADING: the view is FAILED, its loaded rows removed, and the
# run must be opened for viewing again (after its archive folder is restored from the off-host copy when the reason says
# that it is damaged); 4 the reconcile after a purge or a deletion did not end clean: an incident (OPERATIONS_RUNBOOK §8,
# Reconcile finding after a purge, or Reconcile finding after a deletion).
set -eu

# The values in the shell would override the env file for every Compose command: the script sets them per command only.
unset PARTFLOW_RELEASE PARTFLOW_COMMIT PARTFLOW_ACCEPT_SCHEMA_REVISION PARTFLOW_BACKUP_DIR PARTFLOW_ARCHIVE_DIR \
    PARTFLOW_ARCHIVE_COPY_DIR COMPOSE_PROJECT_NAME COMPOSE_FILE COMPOSE_PROFILES
# Records, logs and the stamp are readable by the owning account only.
umask 077

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
RELEASE_PATTERN='^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'
# The archive name of a run, the label of its pre-purge backup (contract B2 §4).
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
PARTFLOW_BACKUP_DIR. Runs the history archive job in the `maintenance` service: recover, at the hourly work evaluate,
then the steps that `next` names, and status. evaluate creates the automatic run that Administration → History
archival & purge calls for now (PROPOSED, which waits for a person to approve or dismiss it, or APPROVED under the
standing authorization, which this start works on) and says why it creates none otherwise; a refusal or failure of it
is reported and the work goes on. A permanent deletion request comes first: its start, its pre-purge backup and the
deletion of its stored rows in one transaction (OPERATIONS_RUNBOOK §10), then the full reconcile; a start that worked on
a deletion does no run work. The hourly work of Movement-history archival: export, verify and copy an approved run,
take its pre-purge backup and PURGE its archived Movement history from the database (or resume the purge of a run left
PURGING), then the full reconcile. Both DELETE from the database, always after their pre-purge backup. The viewing work
comes after the deletion and before the run work, at any start: close-view removes the loaded rows of a view of an
archive that a person closed or whose days ran out, and load-view checks the archive folder of a run that a person
opened for viewing and loads it into the viewer tables; neither changes live history.
  --scheduled             the host task that starts every 5 minutes: every start first runs the cheap check
                          (history-archive settings in the running backend container) and carries out an open
                          deletion request and the viewing work at once; the hourly work runs once per hour, at the
                          first start at or after the Start minute of Administration → History archival & purge;
                          every other start ends at once (NOT_DUE)
  --env-file FILE         default .env.production
  --records-dir DIR       default $HOME/partflow-deployments: the release lock .release.lock, the archive lock
                          .archive.lock, the hourly stamp archive/hourly-slot, the reports archive/<UTC stamp>/ and
                          the incident record archive/reconcile-after-purge (while it exists, a start runs recover
                          and status only and works on no run, no deletion request and no view)
  --backup-wait-minutes N how long a pre-purge backup waits for another backup that holds the backup lock, polling
                          (0-1440, default 60; 0 = refused at once)
Exit: 0 done (or nothing due), 1 refused (a release runs, the archive lock is held, a step refused, the incident record
      exists; nothing changed), 2 could not run (evaluate included), or the pre-purge backup failed, or a deletion was
      interrupted (nothing was deleted; the next start fails a run or deletion request left RUNNING), or the load or the
      closing of a view was interrupted (the next start fails a view left LOADING, or closes one left CLOSING),
      3 a run or a deletion request failed: it is FAILED; request a new run in Administration → History archival & purge,
      or the deletion again (a failed deletion request deleted nothing); or the load of a view failed: it is FAILED, and
      the run is opened for viewing again, once a damaged archive folder is restored from its off-host copy,
      4 the reconcile after a purge or a deletion did not end clean: an incident (OPERATIONS_RUNBOOK §8).
stdout on exit 0: NOT_DUE, DEFERRED <reason>, EVALUATE <result> <run id|-> <state|-> for an hourly work that worked on
no run and no view (the decision of evaluate, then the open run that next named:
EVALUATE off - -, EVALUATE created 7 PROPOSED), ARCHIVE <outcome> <run id|-> <state|-> for one that worked on a run,
VIEW <outcome> <view id> <state> for a start that worked on views and on no run (VIEW loaded 4 OPEN,
VIEW closed 3 CLOSED), or, for a start that worked on a deletion request, DELETION <outcome> <request id> <state>
(DELETION deleted 7 COMPLETED)
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
# The incident record of a reconcile after a purge or a deletion that did not end clean (check.sh reads it).
INCIDENT_FILE=$RECORDS_DIR/archive/reconcile-after-purge
# Scheduled only: set once this start may attempt the hourly slot SLOT, cleared once the stamp is written.
STAMP_PENDING=
SLOT=
# Scheduled, within the hour of the last attempted slot: until the cheap check finds an open deletion request, whatever
# keeps this start from one ends it quietly as NOT_DUE (the next hourly start reports it).
QUIET=
# mark_slot: write the hourly stamp (the slot this start attempted) so the next attempt waits for the next hour.
mark_slot() {
    [ -n "$STAMP_PENDING" ] || return 0
    STAMP_PENDING=
    echo "$SLOT" >"$STAMP_FILE.tmp" 2>/dev/null && mv -f "$STAMP_FILE.tmp" "$STAMP_FILE" 2>/dev/null \
        || echo "archive: the hourly stamp $STAMP_FILE could not be written." >&2
}
KEEP=
could_not_run() {
    if [ -n "$QUIET" ]; then
        echo "NOT_DUE"
        exit 0
    fi
    mark_slot
    KEEP=1
    say "$1 Nothing was changed."
    exit 2
}
refuse() {
    if [ -n "$QUIET" ]; then
        echo "NOT_DUE"
        exit 0
    fi
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
# json_bool FILE KEY: a top-level boolean of an indented app.cli report: true, false, or empty for null or absent.
json_bool() {
    sed -n -e 's/^  "'"$2"'": true,\{0,1\}$/true/p' -e 's/^  "'"$2"'": false,\{0,1\}$/false/p' "$1" 2>/dev/null \
        | head -n 1
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
# json_text FILE KEY: a top-level string value that may hold escaped characters, as the report writes them (empty for
# null or absent), for example the message of `evaluate`.
json_text() {
    sed -n 's/^  "'"$2"'": "\(.*\)",\{0,1\}$/\1/p' "$1" 2>/dev/null | head -n 1
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
# row_counts FILE OBJECT: the non-zero row counts of a report's top-level OBJECT, for example the rows a deletion
# deleted (`deleted`: "work_orders 1, ...") or those that the closing of a view removed (`removed`).
row_counts() {
    awk -v key="$2" '
        !on && $0 == "  \"" key "\": {" { on = 1; next }
        on && index($0, "  }") == 1 { exit }
        on {
            line = $0; sub(/^ +"/, "", line); sub(/,$/, "", line)
            split(line, part, "\": ")
            if (part[2] + 0 > 0) { printf "%s%s %s", sep, part[1], part[2]; sep = ", " }
        }
    ' "$1" 2>/dev/null
}
# pending_views FILE: the views of an archive that the cheap check found waiting for the job, from its `pending_views`
# list, each with its state and the work that it waits for ("view 3 CLOSING (close_view), view 4 REQUESTED (load_view)").
pending_views() {
    awk '
        !on && $0 == "  \"pending_views\": [" { on = 1; next }
        on && /^  \]/ { exit }
        on && /^      "id": / { id = $0; sub(/^      "id": /, "", id); sub(/,$/, "", id) }
        on && /^      "state": "/ { state = $0; sub(/^      "state": "/, "", state); sub(/",?$/, "", state) }
        on && /^      "action": "/ { action = $0; sub(/^      "action": "/, "", action); sub(/",?$/, "", action) }
        on && /^    }/ {
            printf "%sview %s %s (%s)", sep, id, state, action; sep = ", "
            id = ""; state = ""; action = ""
        }
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
# --scheduled: the hourly stamp
# ---------------------------------------------------------------------------

WITHIN_HOUR=
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
        # Within the hour of the last attempted slot: this start looks only for an open deletion request.
        WITHIN_HOUR=1
        QUIET=1
    else
        # From here on this start attempts a slot: refusals and failures are reported once for it.
        SLOT=$NOW
        STAMP_PENDING=1
    fi
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
# The step that runs now (evaluate, export, verify, copy, backup, purge, start-deletion, deletion-backup, delete,
# load-view, close-view or reconcile), for the signal handler.
CURRENT_STEP=
# Set once a purge or a deletion may have deleted something in this start, cleared once its reconcile result is recorded
# (or once the deletion is known to have deleted nothing). RECONCILE_FOR says which: purge or deletion.
RECONCILE_PENDING=
RECONCILE_FOR=
RUN_ID=
ARCHIVE_NAME=
BACKUP_NAME=
# The deletion request this start works on, and what became of it (the DELETION line).
REQUEST_ID=
DELETION_WORKED=
DEL_OUTCOME=
DEL_STATE=
# The view of an archive this start works on (load-view, close-view), its run and the state its last report named.
# VIEW_WORKED: the start worked on a view; such work, like deletion work, never marks the hourly slot. VIEW_LINE: what
# became of the last view that a step loaded or closed, "<outcome> <view id> <state>" (the VIEW line).
VIEW_ID=
VIEW_RUN=
VIEW_WORKED=
VIEW_STATE=
VIEW_LINE=
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
# record_incident RESULT EXIT_CODE CHECKS: the incident record of a reconcile after a purge or a deletion that did not
# end clean, replaced in one rename. Until the operator removes it, check.sh reports it and no start works on a run, a
# deletion request or a view.
record_incident() {
    if [ "$RECONCILE_FOR" = deletion ]; then
        incident_run=
        incident_archive=
        incident_request=$REQUEST_ID
    else
        incident_run=$RUN_ID
        incident_archive=$ARCHIVE_NAME
        incident_request=
    fi
    {
        echo "result=$1"
        echo "exit_code=$2"
        echo "run=$incident_run"
        echo "archive_name=$incident_archive"
        echo "request=$incident_request"
        echo "pre_purge_backup=$BACKUP_NAME"
        echo "failing_checks=$3"
        echo "finished_at=$(utc)"
        echo "report=$REC/reconcile.json"
    } >"$INCIDENT_FILE.tmp" 2>/dev/null && mv -f "$INCIDENT_FILE.tmp" "$INCIDENT_FILE" 2>/dev/null \
        || say "the incident record $INCIDENT_FILE could not be written, so it cannot stop the next start: disable the history archive task until the incident is closed (OPERATIONS_RUNBOOK §8)."
}
# reconcile_subject: what the pending reconcile follows ("the purge of run 3", "the deletion of request 7") and the
# runbook entry of its incident.
reconcile_subject() {
    if [ "$RECONCILE_FOR" = deletion ]; then
        SUBJECT="the deletion of request ${REQUEST_ID:-?}"
        SECTION="Reconcile finding after a deletion"
    else
        SUBJECT="the purge of run ${RUN_ID:-?}"
        SECTION="Reconcile finding after a purge"
    fi
}
REQUEST_NEW="Request a new run in Administration → History archival & purge (OPERATIONS_RUNBOOK §11)."
REQUEST_AGAIN="Request the deletion again in PartFlow, or with pf delete-permanently (OPERATIONS_RUNBOOK §10)."
VIEW_AGAIN="Open the run for viewing again in Administration → History archival & purge (OPERATIONS_RUNBOOK §11)."
on_signal() {
    trap - INT TERM
    # The trap can run while a step's output is redirected to its report and log: speak on the script's own stdout and
    # stderr (fds 8 and 9), never into the step's files.
    exec 1>&8 2>&9
    KEEP=1
    # A start that worked on a deletion or a view leaves the hourly slot to the next start (one whose `next` answered
    # anything else after its viewing work has marked it already); any other attempted it.
    [ -n "$DELETION_WORKED$VIEW_WORKED" ] || mark_slot
    case "$CURRENT_STEP" in
        load-view)
            say "interrupted during the load of view ${VIEW_ID:-?}. A load never changes live history: the next start loads a view still REQUESTED, or fails a view left LOADING (recover), which removes the rows it loaded; for a failed view, open the run for viewing again in Administration → History archival & purge (OPERATIONS_RUNBOOK §11)."
            ;;
        close-view)
            say "interrupted during the closing of view ${VIEW_ID:-?}: the next start closes it again (a view left CLOSING, or OPEN past its days) and removes the rest of its rows."
            ;;
        evaluate)
            say "interrupted during the evaluation of the automatic runs (history-archive evaluate). Nothing was deleted; an automatic run may have been created, and the next hourly work goes on with it."
            ;;
        purge)
            say "interrupted during the purge of run ${RUN_ID:-?}. Whole components only are ever deleted: the next start resumes the purge of a run left PURGING and reconciles when it ends (the purge receipt then lists what was deleted), or fails a run that was still RUNNING (then nothing was deleted)."
            ;;
        backup)
            say "interrupted during the pre-purge backup of run ${RUN_ID:-?}. Nothing was deleted: the run stays RUNNING, and the next start fails it (recover); request a new run in Administration → History archival & purge."
            ;;
        start-deletion)
            say "interrupted while deletion request ${REQUEST_ID:-?} was being started. Nothing was deleted: the next start makes the request FAILED if it was started (recover), or starts it again."
            ;;
        deletion-backup)
            say "interrupted during the pre-purge backup of deletion request ${REQUEST_ID:-?}. Nothing was deleted: the request stays RUNNING, and the next start makes it FAILED (recover). $REQUEST_AGAIN"
            ;;
        delete)
            # Its one transaction committed (the request is COMPLETED) or rolled back (it stays RUNNING): fail closed.
            record_incident interrupted 2 ""
            say "interrupted during the deletion of request ${REQUEST_ID:-?}: its one transaction either committed (the request is COMPLETED, and no reconcile ran after it) or rolled back (it stays RUNNING, nothing was deleted, and the next start makes it FAILED). Read history-archive status (OPERATIONS_RUNBOOK §11) and run the reconcile by hand (OPERATIONS_RUNBOOK §7), then follow OPERATIONS_RUNBOOK §8 (Reconcile finding after a deletion). $INCIDENT_FILE records the incident: no start works on a run, a deletion request or a view until you remove it."
            ;;
        *)
            if [ -n "$RECONCILE_PENDING" ]; then
                record_incident interrupted 2 ""
                reconcile_subject
                say "interrupted before the reconcile after $SUBJECT ended: run it by hand (OPERATIONS_RUNBOOK §7), then follow OPERATIONS_RUNBOOK §8 ($SECTION). $INCIDENT_FILE records the incident: no start works on a run, a deletion request or a view until you remove it."
            else
                say "interrupted. The next start recovers a run or a deletion request left RUNNING (it becomes FAILED); nothing was deleted."
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
# --scheduled: the cheap check (an open deletion request, viewing work), then is the hourly work due?
# ---------------------------------------------------------------------------

# The hourly work is due (a manual start always does it); a scheduled start decides below.
DUE=1
# The cheap check found an open deletion request: this start carries it out first.
DELETION_PENDING=
# The cheap check found viewing work (contract B5 §3.1, §3.6: a view to close, a view to load, or a load that a stopped
# start left LOADING, which recover fails): this start does it, whatever the time.
VIEW_PENDING=
if [ -n "$SCHEDULED" ]; then
    DUE=
    # The incident record stops every deletion and all viewing work: a start within the hour has nothing to look for
    # (the hourly start reports the record).
    if [ -n "$WITHIN_HOUR" ] && [ -e "$INCIDENT_FILE" ]; then
        echo "NOT_DUE"
        exit 0
    fi
    # The cheap check (contract B3 §3.1, B5 §3.1): history-archive settings in the running backend container, as the
    # application database role; it creates no container. No answer (the backend is down) counts as nothing open.
    START_MINUTE=
    crc=0
    pf exec -T backend python -m app.cli history-archive settings >"$WORK/check.json" 2>"$WORK/check.log" || crc=$?
    if [ "$crc" -eq 0 ] && report_valid "$WORK/check.json" 0 && [ "$(json_field "$WORK/check.json" result)" = read ]; then
        START_MINUTE=$(json_number "$WORK/check.json" start_minute)
        if [ "$(json_bool "$WORK/check.json" deletion_pending)" = true ]; then
            DELETION_PENDING=1
            QUIET=
            pending_id=$(json_nested_number "$WORK/check.json" deletion_request id)
            pending_state=$(json_nested "$WORK/check.json" deletion_request state)
            say "deletion request ${pending_id:-?} (${pending_state:-?}) is open: this start carries it out now and does no run work."
        fi
        if [ "$(json_bool "$WORK/check.json" viewing_pending)" = true ]; then
            VIEW_PENDING=1
            QUIET=
            views=$(pending_views "$WORK/check.json")
            say "viewing work waits for the job (${views:-a view of an archive}): this start does it now."
        fi
    elif [ -z "$WITHIN_HOUR" ]; then
        say "the cheap check (history-archive settings in the running backend container) gave no answer (exit $crc): the hourly work goes on, and its next step still hands out an open deletion request and viewing work first."
        tail -n 3 "$WORK/check.log" >&2 2>/dev/null || true
    fi
    if [ -n "$WITHIN_HOUR" ]; then
        # Within the hour of the last slot: an open deletion request, viewing work, or nothing to do.
        if [ -z "$DELETION_PENDING$VIEW_PENDING" ]; then
            echo "NOT_DUE"
            exit 0
        fi
    else
        if [ -z "$START_MINUTE" ] || [ "$START_MINUTE" -gt 59 ]; then
            # Without the cheap check's answer, the Start minute is read in the maintenance service.
            rc=0
            copy_rule
            job settings >"$WORK/settings.json" 2>"$WORK/settings.log" || rc=$?
            START_MINUTE=$(json_number "$WORK/settings.json" start_minute)
            if [ "$rc" -ne 0 ] || ! report_valid "$WORK/settings.json" "$rc" || [ -z "$START_MINUTE" ] \
                || [ "$START_MINUTE" -gt 59 ]; then
                tail -n 3 "$WORK/settings.log" >&2 2>/dev/null || true
                could_not_run "the Start minute could not be read (history-archive settings exit $rc; see above)."
            fi
        fi
        # The slot: the newest HH:<Start minute> at or before now, as an epoch minute.
        SLOT=$((NOW - (NOW_MINUTE - START_MINUTE + 60) % 60))
        if [ -z "$LAST" ]; then
            # The first scheduled start (or a lost stamp) records the current slot; the work starts at the next Start
            # minute. A start that works on a deletion request or a view leaves that to the next start.
            if [ -z "$DELETION_PENDING$VIEW_PENDING" ]; then
                mark_slot
                echo "NOT_DUE"
                exit 0
            fi
        elif [ "$SLOT" -le "$LAST" ]; then
            STAMP_PENDING=
            if [ -z "$DELETION_PENDING$VIEW_PENDING" ]; then
                echo "NOT_DUE"
                exit 0
            fi
        else
            DUE=1
        fi
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
        # leaves this hour to it, and an open deletion request and viewing work to a later start.
        if [ -n "$SCHEDULED" ]; then
            mark_slot
            if [ -n "$DELETION_PENDING" ] && [ -n "$VIEW_PENDING" ]; then
                waiting="the open deletion request and the viewing work wait"
            elif [ -n "$DELETION_PENDING" ]; then
                waiting="the open deletion request waits"
            else
                waiting="the viewing work waits"
            fi
            if [ -n "$WITHIN_HOUR" ]; then
                say "the previous archive.sh ($who) is still working; $waiting for a later start."
            elif [ -n "$DELETION_PENDING$VIEW_PENDING" ]; then
                say "the previous archive.sh ($who) is still working; this hour's work is skipped, and $waiting for a later start."
            else
                say "the previous archive.sh ($who) is still working; this hour's work is skipped."
            fi
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

REC=$RECORDS_DIR/archive/$(date -u +%Y%m%dT%H%M%SZ)
mkdir "$REC" 2>/dev/null || { REC=; could_not_run "the record folder $RECORDS_DIR/archive/<UTC stamp> cannot be created."; }
if [ -n "$DELETION_PENDING$VIEW_PENDING" ]; then
    # The cheap check that took this start for a deletion request or for viewing work.
    KEEP=1
    cp "$WORK/check.json" "$REC/check.json" 2>/dev/null || true
fi

# ---------------------------------------------------------------------------
# The work (contract B2 §3.1, B3 §3.1, B4 §3.2, B5 §3.6)
# ---------------------------------------------------------------------------

EXIT=0
# The run work's state and outcome (the ARCHIVE and EVALUATE lines); a view's are VIEW_STATE and VIEW_LINE.
STATE=
OUTCOME=
STEP_NAME=
STEP_CMD=
# The purge of this start reached PURGING (its report names the run's pre-purge backup): it may have deleted something.
PURGE_STARTED=
# The decision of `evaluate` at the hourly work (its result; empty when it did not run or gave none), and whether the
# `next` loop then worked on a run (a step of export, verify, copy, the pre-purge backup or the purge): a start whose
# hourly work worked on no run and no view reports the decision as its final line (EVALUATE).
EVAL_RESULT=
RUN_WORKED=
# step NAME COMMAND ARGS...: one history-archive command; report REC/NAME.json, stderr REC/NAME.log; sets RC, RESULT,
# VALID and STATE (when the report names a state). The report of a command on a deletion request (start-deletion,
# delete) must also name that request (request_id), and that of a command on a view (load-view, close-view) that view
# (view_id), or it is no valid report.
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
    case "$STEP_CMD" in
        start-deletion | delete) named=$(json_number "$REC/$STEP_NAME.json" request_id); wanted=$REQUEST_ID ;;
        load-view | close-view) named=$(json_number "$REC/$STEP_NAME.json" view_id); wanted=$VIEW_ID ;;
        *) named=; wanted= ;;
    esac
    if report_valid "$REC/$STEP_NAME.json" "$RC" && [ "$named" = "$wanted" ]; then
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
            start-deletion)
                say "the outcome of the start of deletion request ${REQUEST_ID:-?} is unknown. Nothing was deleted: the next start makes the request FAILED if it was started (recover), or starts it again." ;;
            load-view)
                say "the outcome of the load of view ${VIEW_ID:-?} is unknown. A load never changes live history: the next start loads a view still REQUESTED, or fails a view left LOADING (recover), which removes the rows it loaded." ;;
            close-view)
                say "the outcome of the closing of view ${VIEW_ID:-?} is unknown: the next start closes a view left CLOSING (or OPEN past its days) again." ;;
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
            elif [ "$STEP_CMD" = start-deletion ]; then
                # The requester re-check (contract B3 §2): the message says that nothing was deleted and who may
                # ask again.
                say "deletion request ${REQUEST_ID:-?} FAILED: $message"
            elif [ "$STEP_CMD" = load-view ]; then
                # Contract B5 §3.3: the view is FAILED with what it loaded removed; the message says why and what to
                # do (a damaged or missing archive folder, which is restored from its off-host copy; the setting of
                # runs loaded at once lowered since the request; an internal error).
                say "view ${VIEW_ID:-?} of run ${VIEW_RUN:-?} FAILED: $message Live history was not changed (OPERATIONS_RUNBOOK §11, Viewing an archive)."
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
            elif [ "$RESULT" = interrupted ] && { [ "$STEP_CMD" = load-view ] || [ "$STEP_CMD" = close-view ]; }; then
                # A database error that does not last (contract B5 §3.3, §3.4): the message says what the next start
                # does with the view (LOADING: recover fails it; CLOSING: it is closed again).
                say "view ${VIEW_ID:-?} stopped: $message"
            else
                say "history-archive $STEP_CMD could not run: $message"
                if [ "$STEP_CMD" = load-view ] && [ "$VIEW_STATE" = LOADING ]; then
                    say "View ${VIEW_ID:-?} stays LOADING: the next start fails it (recover) and removes the rows it loaded. Live history was not changed. $VIEW_AGAIN"
                elif [ "$STEP_CMD" = close-view ] && [ "$VIEW_STATE" = CLOSING ]; then
                    say "View ${VIEW_ID:-?} stays CLOSING: the next start removes the rest of its rows."
                fi
                if [ "$STEP_CMD" = purge ]; then
                    say "Nothing was deleted. Run ${RUN_ID:-?} stays RUNNING, and the next start fails it (recover). $REQUEST_NEW"
                elif [ "$STEP_CMD" = start-deletion ]; then
                    say "Nothing was deleted. The next start makes deletion request ${REQUEST_ID:-?} FAILED if it was started (recover), or starts it again."
                fi
            fi
            ;;
    esac
}

# reconcile_after: the full reconcile right after a purge or a deletion (RECONCILE_FOR; contract B2 §3.1, B3 §3.1,
# design §10), in a one-off backend container as the application database role, as release.sh and
# scheduled-reconcile.sh run it. Not clean (a finding, an error, no complete report): an incident, exit 4 and the
# incident record. No earlier record can exist here: while one exists, no start purges or deletes.
reconcile_after() {
    KEEP=1
    reconcile_subject
    if [ "$RECONCILE_FOR" = deletion ]; then
        say "deletion request ${REQUEST_ID:-?}: the full reconcile after the deletion (python -m app.cli reconcile in a one-off backend container)."
    else
        say "run ${RUN_ID:-?}: the full reconcile after the purge (python -m app.cli reconcile in a one-off backend container)."
    fi
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
        say "the reconcile after $SUBJECT is clean ($REC/reconcile.json)."
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
    if [ "$RECONCILE_FOR" = deletion ]; then
        evidence="the pre-purge backup of the deletion is ${BACKUP_NAME:-unknown}"
    else
        evidence="the purge receipt is $ARCHIVE_DIR/${ARCHIVE_NAME:-<archive name>}.purged.json${BACKUP_NAME:+ and the pre-purge backup $BACKUP_NAME}"
    fi
    say "INCIDENT — the reconcile after $SUBJECT $what: $REC/reconcile.json. Stop requesting runs and deletions and follow OPERATIONS_RUNBOOK §8 ($SECTION): $evidence; $INCIDENT_FILE records the incident, and no start works on a run, a deletion request or a view until you remove it after the investigation."
}

# take_backup LABEL REASON STEP: backup.sh --kind pre-purge --label LABEL, which publishes and verifies it like every
# backup and waits up to --backup-wait-minutes for a backup that holds the backup lock (STEP names it for the signal
# handler). Sets BACKUP_NAME and BACKUP_STARTED (its dump start, from its backup-verify report); when it failed or named
# no backup of LABEL with its dump start, sets EXIT and WHY and returns 1.
take_backup() {
    CURRENT_STEP=$3
    brc=0
    sh "$SCRIPT_DIR/backup.sh" --kind pre-purge --label "$1" --operator archive.sh \
        --reason "$2" --env-file "$ENV_FILE" --lock-wait-minutes "$BACKUP_WAIT_MINUTES" \
        >"$REC/backup.out" 2>"$REC/backup.log" || brc=$?
    CURRENT_STEP=
    line=$(sed -n '/^BACKUP /p' "$REC/backup.out" 2>/dev/null | tail -n 1)
    BACKUP_NAME=$(printf '%s\n' "$line" | awk 'NF == 4 { print $2 }')
    BACKUP_STARTED=$(printf '%s\n' "$line" | awk 'NF == 4 { print $4 }')
    if [ "$brc" -eq 0 ] && printf '%s' "$BACKUP_NAME" | grep -Eq "^[0-9]{8}T[0-9]{6}Z-pre-purge-$1\$" \
        && printf '%s' "$BACKUP_STARTED" | grep -Eq "$UTC_PATTERN"; then
        return 0
    fi
    EXIT=2
    tail -n 3 "$REC/backup.log" >&2 2>/dev/null || true
    if [ "$brc" -eq 0 ]; then
        WHY="backup.sh did not name the pre-purge backup with its dump start ($REC/backup.out)"
    else
        WHY="the pre-purge backup failed (backup.sh exit $brc; $REC/backup.log)"
    fi
    BACKUP_NAME=
    return 1
}

# pre_purge_backup RECORD: the pre-purge backup of run RUN_ID that `next` (report RECORD) asked for. On a failure it sets
# EXIT, says why and returns 1: nothing was deleted, and the run stays RUNNING for the next start's recover, which fails
# it.
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
    if take_backup "$label" "pre-purge backup for $label" backup; then
        say "run ${RUN_ID:-?}: the pre-purge backup $BACKUP_NAME is published and verified (its dump started at $BACKUP_STARTED)."
        return 0
    fi
    say "run ${RUN_ID:-?}: $WHY. Nothing was deleted. The run stays RUNNING, and the next start fails it (recover). $REQUEST_NEW"
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
        RECONCILE_FOR=purge
        receipt=$(json_nested "$REC/purge.json" receipt name)
        say "run $RUN_ID: $(json_nested_number "$REC/purge.json" purged components) components ($(json_nested_number "$REC/purge.json" purged flows) flows, $(json_nested_number "$REC/purge.json" purged movements) Movements) purged, $(json_nested_number "$REC/purge.json" skipped components) components ($(json_nested_number "$REC/purge.json" skipped flows) flows) skipped; batches this start: $(json_number "$REC/purge.json" batches); purge receipt $ARCHIVE_DIR/${receipt:-?} (SHA-256 $(json_nested "$REC/purge.json" receipt sha256))."
        problem=$(json_nested "$REC/purge.json" receipt copy_problem)
        [ -z "$problem" ] || say "$problem"
        reconcile_after
        return 0
    fi
    failed
    if [ "$RC" -eq 3 ] && [ -n "$VALID" ] && [ -n "$PURGE_STARTED" ]; then
        RECONCILE_PENDING=1
        RECONCILE_FOR=purge
        reconcile_after
    fi
    return 0
}

# deletion_backup RECORD: the pre-purge backup of deletion request REQUEST_ID that `next` (report RECORD) asked for, with
# the request's own label deletion-<id>-<UTC stamp> (contract B3 §3.2). On a failure it sets EXIT, says why and returns
# 1: nothing was deleted, and the request stays RUNNING for the next start's recover, which makes it FAILED.
deletion_backup() {
    kind=$(json_nested "$REC/$1.json" backup kind)
    label=$(json_nested "$REC/$1.json" backup label)
    if [ "$kind" != pre-purge ] \
        || ! printf '%s' "$label" | grep -Eq "^deletion-${REQUEST_ID:-x}-[0-9]{8}T[0-9]{6}Z\$"; then
        EXIT=2
        say "history-archive next asked for a backup of deletion request ${REQUEST_ID:-?} without the pre-purge kind and the request's label ($REC/$1.json). Nothing was deleted; the request stays RUNNING, and the next start makes it FAILED (recover). $REQUEST_AGAIN"
        return 1
    fi
    say "deletion request $REQUEST_ID: the pre-purge backup (backup.sh --kind pre-purge --label $label; it waits up to $BACKUP_WAIT_MINUTES min for a backup that is running)."
    if take_backup "$label" "pre-purge backup for deletion request $REQUEST_ID" deletion-backup; then
        say "deletion request $REQUEST_ID: the pre-purge backup $BACKUP_NAME is published and verified (its dump started at $BACKUP_STARTED)."
        return 0
    fi
    say "deletion request $REQUEST_ID: $WHY. Nothing was deleted. The request stays RUNNING, and the next start makes it FAILED (recover). $REQUEST_AGAIN"
    return 1
}

# carry_out_deletion: delete --request REQUEST_ID with the pre-purge backup just taken (one transaction, contract B3 §2
# and §3.1), then the full reconcile when it deleted. Exit 3: the request FAILED and nothing was deleted. Exit 1: the
# step was refused and changed nothing; the request stays RUNNING for the next start's recover. Every other end (exit
# 2: interrupted, could not run or outcome unknown; or no valid report) goes to resolve_deletion, because only `status`
# tells whether the one transaction committed: even an `interrupted` report could follow a COMMIT that succeeded.
carry_out_deletion() {
    # From here on the deletion may delete something: an interruption leaves the incident record (on_signal).
    RECONCILE_PENDING=1
    RECONCILE_FOR=deletion
    step delete delete --request "$REQUEST_ID" --backup "$BACKUP_NAME" --backup-started-at "$BACKUP_STARTED"
    message=$(error_message "$REC/delete.json")
    if [ -n "$VALID" ] && [ "$RC" -eq 0 ]; then
        DEL_OUTCOME=deleted
        DEL_STATE=${STATE:-COMPLETED}
        rows=$(row_counts "$REC/delete.json" deleted)
        say "deletion request $REQUEST_ID: deleted (${rows:-no rows}) in one transaction, after its pre-purge backup $BACKUP_NAME."
        reconcile_after
        return 0
    fi
    if [ -n "$VALID" ] && [ "$RC" -eq 3 ]; then
        RECONCILE_PENDING=
        EXIT=3
        DEL_STATE=${STATE:-FAILED}
        say "deletion request $REQUEST_ID FAILED: ${message:-see $REC/delete.json.} Nothing was deleted: the deletion is one transaction. $REQUEST_AGAIN"
        return 0
    fi
    if [ -n "$VALID" ] && [ "$RC" -eq 1 ]; then
        RECONCILE_PENDING=
        EXIT=1
        say "history-archive delete refused: ${message:-see $REC/delete.json.}"
        say "Nothing was deleted. Deletion request $REQUEST_ID stays RUNNING, and the next start makes it FAILED (recover). $REQUEST_AGAIN"
        return 0
    fi
    # Exit 2 (deletion_interrupted, a step that could not run, outcome_unknown) or no valid report.
    if [ -n "$VALID" ] && [ -n "$message" ]; then
        say "history-archive delete ended with exit $RC ($(json_nested "$REC/delete.json" error code)): $message"
    fi
    say "history-archive status tells whether the deletion of request $REQUEST_ID committed."
    resolve_deletion
}

# resolve_deletion: after a deletion that ended with exit 2 or gave no valid report, `status` tells whether its one
# transaction committed. The last finished request is this one, COMPLETED: it deleted, so the reconcile runs now. The
# request is still open (RUNNING): nothing was deleted, and the next start makes it FAILED (recover). This one FAILED:
# nothing was deleted. When `status` cannot tell, the reconcile runs anyway: a deletion that may have committed is never
# left unreconciled.
resolve_deletion() {
    step delete-status status
    finished_id=
    finished_state=
    open_id=
    if [ -n "$VALID" ]; then
        finished_id=$(json_nested_number "$REC/delete-status.json" last_finished_deletion_request id)
        finished_state=$(json_nested "$REC/delete-status.json" last_finished_deletion_request state)
        open_id=$(json_nested_number "$REC/delete-status.json" open_deletion_request id)
    fi
    if [ "$finished_id" = "$REQUEST_ID" ] && [ "$finished_state" = COMPLETED ]; then
        DEL_OUTCOME=deleted
        DEL_STATE=COMPLETED
        say "status shows deletion request $REQUEST_ID COMPLETED: its deletion committed, after its pre-purge backup $BACKUP_NAME."
        reconcile_after
        return 0
    fi
    if [ "$open_id" = "$REQUEST_ID" ]; then
        RECONCILE_PENDING=
        EXIT=2
        DEL_STATE=RUNNING
        say "status shows deletion request $REQUEST_ID still RUNNING: its deletion did not commit, so nothing was deleted, and the next start makes it FAILED (recover). $REQUEST_AGAIN"
        return 0
    fi
    if [ "$finished_id" = "$REQUEST_ID" ]; then
        RECONCILE_PENDING=
        EXIT=3
        DEL_STATE=$finished_state
        reason=$(sed -n '/^  "last_finished_deletion_request": {$/,/^  }/s/^    "failure_reason": "\(.*\)",\{0,1\}$/\1/p' \
            "$REC/delete-status.json" 2>/dev/null | head -n 1)
        say "status shows deletion request $REQUEST_ID ${finished_state:-finished}${reason:+: $reason} Nothing was deleted: the deletion is one transaction. $REQUEST_AGAIN"
        return 0
    fi
    EXIT=2
    say "history-archive status does not tell whether the deletion of request $REQUEST_ID committed: the reconcile runs now, as after a deletion. Read the request's state in Administration → History archival & purge, or with history-archive status (OPERATIONS_RUNBOOK §11)."
    reconcile_after
}

# evaluate_runs: the automatic runs of the hourly work (contract B4 §3.1, §3.2). `history-archive evaluate` creates the
# automatic run that the settings of Administration → History archival & purge call for now, PROPOSED for a person's
# approval or APPROVED under the standing authorization, or says why it creates none; it never deletes anything. Exit 0:
# its result and message are logged (with the created run and its state), and EVAL_RESULT keeps the result for the
# final line; only a created run keeps the start's reports. Exit 1 (refused, for example another history-archive
# command holds the job's lock) or 2 (it could not run or did not finish), or no valid report: the error is logged and
# the start's exit status reports it (unless recover already set one), and the `next` loop goes on with the open run.
evaluate_runs() {
    # The evaluation's own run is named by its log line; STATE stays the run work's (the ARCHIVE line).
    state_before=$STATE
    step evaluate evaluate
    STATE=$state_before
    if [ -n "$VALID" ] && [ "$RC" -eq 0 ] && [ -n "$RESULT" ]; then
        EVAL_RESULT=$RESULT
        eval_run=$(json_number "$REC/evaluate.json" run_id)
        eval_state=$(json_field "$REC/evaluate.json" state)
        message=$(json_text "$REC/evaluate.json" message)
        # A created run is the one change an evaluation makes: its reports are kept.
        [ "$RESULT" != created ] || KEEP=1
        say "evaluate: $RESULT${eval_run:+, run $eval_run ${eval_state:-?}}. ${message:-(no message: $REC/evaluate.json)}"
        return 0
    fi
    KEEP=1
    message=$(error_message "$REC/evaluate.json")
    code=2
    if [ -z "$VALID" ]; then
        # The step said that the report is not valid: the outcome is unknown.
        say "the outcome of evaluate is unknown: it may have created an automatic run, which next then names; nothing was deleted."
    elif [ "$RC" -eq 1 ]; then
        code=1
        say "history-archive evaluate refused: ${message:-see $REC/evaluate.json.}"
    elif [ "$RC" -eq 0 ]; then
        say "history-archive evaluate gave no result ($REC/evaluate.json)."
    else
        say "history-archive evaluate could not run (exit $RC): ${message:-see $REC/evaluate.json.}"
    fi
    [ "$EXIT" -ne 0 ] || EXIT=$code
    say "the work goes on (the next loop still works on an open run); the next hourly work evaluates the automatic runs again (OPERATIONS_RUNBOOK §11)."
}

# view_work COMMAND: COMMAND (close-view or load-view) --view VIEW_ID, the viewing work that `next` handed out
# (contract B5 §3.3, §3.4); it never changes live history. Its report is REC/COMMAND-VIEW_ID.json (one start may work on
# several views), and STATE stays the run work's. Exit 0: VIEW_LINE names what became of the view (the VIEW line), what
# it loaded or removed is logged, and the loop asks `next` again. A refusal with the code wrong_state means that the view
# changed after `next` handed it out (a person cancelled it before its load, or its days were raised before its
# closing; review B5 m1): nothing was changed, it is logged, and the loop asks `next` again as well (DONE_STEPS still
# stops a loop). Otherwise `failed` sets EXIT (3: the load FAILED, its rows removed; 2: interrupted, could not run or no
# valid report; 1: any other refusal) and it returns 1: the start ends.
view_work() {
    view_record=$1-$VIEW_ID
    run_state=$STATE
    step "$view_record" "$1" --view "$VIEW_ID"
    STATE=$run_state
    report=$REC/$view_record.json
    VIEW_STATE=$(json_field "$report" state)
    VIEW_RUN=$(json_nested_number "$report" view run_id)
    if [ -n "$VALID" ] && [ "$RC" -eq 1 ] && [ "$(json_nested "$report" error code)" = wrong_state ]; then
        say "history-archive $1 refused view $VIEW_ID, which changed after next handed it out: $(error_message "$report") This start asks next again."
        return 0
    fi
    if [ -z "$VALID" ] || [ "$RC" -ne 0 ]; then
        failed
        return 1
    fi
    VIEW_LINE="$RESULT $VIEW_ID ${VIEW_STATE:--}"
    if [ "$1" = load-view ]; then
        say "view $VIEW_ID of run ${VIEW_RUN:-?} is ${VIEW_STATE:-OPEN}: $(json_nested_number "$report" loaded movements) Movements, $(json_nested_number "$report" loaded flows) flows, $(json_nested_number "$report" loaded lineage_edges) lineage edges, $(json_nested_number "$report" loaded assigned_routes) Assigned Routes and $(json_nested_number "$report" loaded names) names loaded from the archive folder $(json_field "$report" archive_name); it stays open until $(json_nested "$report" view open_until)."
    else
        if [ -n "$(json_nested_number "$report" view closed_by_user_id)" ]; then
            why="a person closed it"
        else
            why="its days ran out"
        fi
        removed=$(row_counts "$report" removed)
        say "view $VIEW_ID of run ${VIEW_RUN:-?} is ${VIEW_STATE:-CLOSED} ($why): its loaded rows were removed (${removed:-no rows})."
    fi
    return 0
}

# pending_note RECORD: why the work that the cheap check found was not handed out by the first `next` (report RECORD): a
# deletion request or a view that waits while a run is RUNNING in another history-archive command (recover ran first, so
# that run is not this start's), or work that is no longer open.
pending_note() {
    note=
    if [ -n "$DELETION_PENDING" ]; then
        waiting=$(json_nested_number "$REC/$1.json" request id)
        if [ -n "$waiting" ]; then
            note="deletion request $waiting waits while run ${RUN_ID:-?} is RUNNING in another history-archive command"
        else
            note="deletion request ${pending_id:-?} is no longer open (it was cancelled or finished meanwhile)"
        fi
    fi
    if [ -n "$VIEW_PENDING" ]; then
        waiting=$(json_nested_number "$REC/$1.json" view id)
        if [ -n "$waiting" ]; then
            text="view $waiting waits while run ${RUN_ID:-?} is RUNNING in another history-archive command"
        else
            text="no view waits for the job any more (recover failed a load that a stopped start left, or a person cancelled the view meanwhile)"
        fi
        note="${note:+$note; }$text"
    fi
    printf '%s' "$note"
}

step recover recover
if [ "$RC" -ne 0 ] || [ -z "$VALID" ]; then
    # Without a recover, no run or request is worked on: a run or a deletion request left RUNNING (also one restored from
    # its pre-purge backup) must be failed before `next` could hand out its backup and purge or deletion again.
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
        stale=$(json_list "$REC/recover.json" failed_request_ids | tr '\n' ' ' | sed 's/ *$//')
        if [ -n "$stale" ]; then
            # A start stopped, its pre-purge backup failed or its deletion was interrupted while the request was
            # RUNNING, or the database was restored from that backup: the request is FAILED now, nothing deleted.
            EXIT=3
            say "deletion request(s) $stale stopped while RUNNING and are FAILED now (an interrupted job, a pre-purge backup that failed, a deletion that stopped on a database error, or a database restored from the pre-purge backup of a deletion). Nothing was deleted by them in this database. $REQUEST_AGAIN"
        fi
        stale=$(json_list "$REC/recover.json" failed_view_ids | tr '\n' ' ' | sed 's/ *$//')
        if [ -n "$stale" ]; then
            # A start stopped, or a database error that does not last stopped it, while it loaded a view, or the
            # database was restored from a backup taken during a load (contract B5 §3.5): the view is FAILED now, its
            # rows removed.
            EXIT=3
            say "view(s) $stale stopped while LOADING and are FAILED now (an interrupted job, a load that stopped on a database error, or a database restored from a backup taken during a load). The rows they had loaded were removed; live history was not changed. $VIEW_AGAIN"
        fi
    fi
    problems=$(json_list "$REC/recover.json" receipt_problems)
    if [ -n "$problems" ]; then
        KEEP=1
        printf '%s\n' "$problems" | while IFS= read -r problem; do
            say "a purge receipt was not copied: $problem (the next start tries again; status reports receipt_not_copied meanwhile, OPERATIONS_RUNBOOK §11)."
        done
    fi

    # Fail closed (design §10): while the incident record of a reconcile after a purge or a deletion exists, a start runs
    # recover and status only. Neither `evaluate` nor `next` is asked, so no step that changes data runs: no automatic
    # run is created, no export, verify, copy, pre-purge backup, purge or deletion, also no resumed purge of a run left
    # PURGING, and no view is loaded or closed (contract B5 §3.6 step 5: a view open past its days then shows as
    # view_not_closed an hour later). Exit 1, unless recover already set a code (3: it failed a run, a request or a view,
    # as after a restore of a pre-purge backup). Only the operator removes the record (OPERATIONS_RUNBOOK §8).
    INCIDENT_OPEN=
    if [ -e "$INCIDENT_FILE" ]; then
        INCIDENT_OPEN=1
        [ "$EXIT" -ne 0 ] || EXIT=1
        incident_run=$(sed -n 's/^run=//p' "$INCIDENT_FILE" 2>/dev/null | head -n 1)
        incident_request=$(sed -n 's/^request=//p' "$INCIDENT_FILE" 2>/dev/null | head -n 1)
        incident_result=$(sed -n 's/^result=//p' "$INCIDENT_FILE" 2>/dev/null | head -n 1)
        if [ -n "$incident_request" ]; then
            what="a reconcile after a deletion exists (deletion request $incident_request, result ${incident_result:-unknown})"
            section="Reconcile finding after a deletion"
        else
            what="a reconcile after a purge exists (run ${incident_run:-?}, result ${incident_result:-unknown})"
            section="Reconcile finding after a purge"
        fi
        say "refused — the incident record $INCIDENT_FILE of $what: no run, deletion request or view is worked on while it exists, no evaluation of the automatic runs, export, verification, copy, pre-purge backup, purge, deletion, load or closing of a view, not even the resumed purge of a run left PURGING. Follow OPERATIONS_RUNBOOK §8 ($section); once the investigation is done and the reconciliation is clean, remove the record (rm $INCIDENT_FILE), and the next start goes on."
    fi

    # The automatic runs (contract B4 §3.2): at the hourly work only, after recover and before the `next` loop. Never in
    # a start taken only for a deletion request or for viewing work (a scheduled start outside its hourly slot: DUE
    # unset), never while the incident record exists. A run it creates PROPOSED waits for a person (`next` answers wait);
    # one it creates APPROVED under the standing authorization is worked on by the loop below, in this start.
    if [ -z "$INCIDENT_OPEN" ] && [ -n "$DUE" ]; then
        evaluate_runs
    fi

    # The steps that `next` names, until it answers wait, running or none, a run or a deletion finishes, or a step fails
    # (contract B2 §3.1, B3 §3.1, B4 §3.2, B5 §3.6): an open deletion request first (start-deletion → pre-purge backup →
    # delete → reconcile; then the start ends: no run work after a deletion); then the viewing work, closing before
    # loading (close-view, load-view; each asks `next` again, and any exit but 0 ends the start); then, only when the
    # hourly work is due, export → verify → copy → backup → purge in one start, and no further run after a purge. An
    # APPROVED run is exported whether it was requested, approved or created under the standing authorization; a
    # PROPOSED run answers wait. Each step runs at most once per run, request or view in one start: `next` naming a step
    # again means that a step made no progress, and the start ends instead of looping. With the incident record (above),
    # the loop does not run at all.
    NEXT_COUNT=0
    DONE_STEPS=' '
    DELETION_STARTED=
    WAIT_SAID=
    VIEW_WAIT_SAID=
    while [ -z "$INCIDENT_OPEN" ]; do
        NEXT_COUNT=$((NEXT_COUNT + 1))
        if [ "$NEXT_COUNT" -eq 1 ]; then record=next; else record=next-$NEXT_COUNT; fi
        # The state that `next` names is a view's when it hands out viewing work: STATE stays the run work's then.
        run_state=$STATE
        step "$record" next
        run=$(json_number "$REC/$record.json" run_id)
        [ -z "$run" ] || RUN_ID=$run
        if [ "$RC" -ne 0 ] || [ -z "$VALID" ]; then
            # Exit 3: the re-check of the run's authority (its requester, its approver, or the User of its standing
            # authorization) failed the run before its backup (contract B2 §2, B4 §2.3).
            failed
            break
        fi
        action=$RESULT
        if [ -n "$DELETION_STARTED" ] && [ "$action" != deletion_backup ]; then
            EXIT=2
            KEEP=1
            say "history-archive next answered ${action:-(none)} after deletion request $REQUEST_ID was started, not its pre-purge backup ($REC/$record.json). Nothing was deleted; the next start makes a request left RUNNING FAILED (recover)."
            break
        fi
        case "$action" in
            start_deletion | deletion_backup)
                # Deletion work: the hourly slot is left to the next start.
                DELETION_WORKED=1
                REQUEST_ID=$(json_number "$REC/$record.json" request_id)
                if [ -z "$REQUEST_ID" ]; then
                    EXIT=2
                    KEEP=1
                    say "history-archive next answered $action without naming its deletion request ($REC/$record.json). Nothing was deleted; the next start tries again."
                    break
                fi
                subject="r$REQUEST_ID"
                ;;
            close_view | load_view)
                # Viewing work (contract B5 §3.2): at any start, after the deletion work and before the run work; it
                # leaves the hourly slot to the hourly work.
                STATE=$run_state
                VIEW_ID=$(json_number "$REC/$record.json" view_id)
                if [ -z "$VIEW_ID" ]; then
                    EXIT=2
                    KEEP=1
                    say "history-archive next answered $action without naming its view ($REC/$record.json). Nothing was changed; the next start tries again."
                    break
                fi
                subject="v$VIEW_ID"
                ;;
            *)
                # The hourly work begins (or there is nothing to do): this start attempted the hourly slot, unless it was
                # taken only for viewing work and has worked on a view (as after deletion work, the slot is left to the
                # hourly work).
                if [ -n "$DUE" ] || [ -z "$VIEW_WORKED" ]; then
                    mark_slot
                fi
                if [ -z "$DUE" ]; then
                    # A start taken only for a deletion request or for viewing work does no run work (contract B3 §3.1,
                    # B5 §3.6). When the first `next` handed out none of that work, the note says why.
                    if [ "$NEXT_COUNT" -eq 1 ]; then
                        say "$(pending_note "$record"), and the hourly work is not due: this start ends."
                    fi
                    break
                fi
                if [ "$NEXT_COUNT" -eq 1 ] && [ -n "$DELETION_PENDING$VIEW_PENDING" ]; then
                    say "$(pending_note "$record"): the hourly work goes on."
                fi
                subject=${RUN_ID:-}
                ;;
        esac
        case "$DONE_STEPS" in
            *" $action:$subject "*)
                EXIT=2
                KEEP=1
                if [ "${subject#r}" != "$subject" ]; then
                    say "history-archive next answered $action again for deletion request $REQUEST_ID: a step of this start made no progress ($REC/$record.json). The next start tries again."
                elif [ "${subject#v}" != "$subject" ]; then
                    say "history-archive next answered $action again for view $VIEW_ID: a step of this start made no progress ($REC/$record.json). The next start tries again."
                else
                    say "history-archive next answered $action again for run ${RUN_ID:-?}: a step of this start made no progress ($REC/$record.json). The next start tries again."
                fi
                break
                ;;
        esac
        DONE_STEPS="$DONE_STEPS$action:$subject "
        case "$action" in
            close_view | load_view)
                # close-view or load-view (contract B5 §3.3, §3.4): exit 0 asks `next` again; any other exit ends the
                # start.
                KEEP=1
                VIEW_WORKED=1
                view_work "${action%_view}-view" || break
                continue
                ;;
            export | verify | copy | backup | purge)
                # The hourly work works on a run (its final line is ARCHIVE, not EVALUATE or VIEW).
                RUN_WORKED=1
                # An APPROVED request waits while this start's run is RUNNING (contract B3 §3): a later start takes it.
                waiting=$(json_nested_number "$REC/$record.json" request id)
                if [ -n "$waiting" ] && [ -z "$WAIT_SAID" ]; then
                    WAIT_SAID=1
                    say "deletion request $waiting waits while run ${RUN_ID:-?} is RUNNING in this start: a later start carries it out."
                fi
                # So does viewing work (contract B5 §3.2).
                waiting=$(json_nested_number "$REC/$record.json" view id)
                if [ -n "$waiting" ] && [ -z "$VIEW_WAIT_SAID" ]; then
                    VIEW_WAIT_SAID=1
                    say "view $waiting waits while run ${RUN_ID:-?} is RUNNING in this start: a later start does its viewing work."
                fi
                ;;
        esac
        OUTCOME=$action
        case "$action" in
            start_deletion)
                KEEP=1
                step start-deletion start-deletion --request "$REQUEST_ID"
                if [ "$RC" -ne 0 ] || [ -z "$VALID" ]; then
                    failed
                    break
                fi
                DELETION_STARTED=1
                ;;
            deletion_backup)
                KEEP=1
                DELETION_STARTED=
                if deletion_backup "$record"; then
                    carry_out_deletion
                fi
                break
                ;;
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
                if [ "$STATE" = PROPOSED ]; then
                    # An automatic run waiting for a person (contract B4 §2.1): the job never touches it.
                    say "run $RUN_ID is PROPOSED: it waits for a holder of Run history archival and purge to approve or dismiss it in Administration → History archival & purge (OPERATIONS_RUNBOOK §11)."
                else
                    say "run $RUN_ID waits for its off-host copy to be confirmed in Administration → History archival & purge."
                fi
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

# A start that worked on a deletion request or on a view leaves the hourly slot to the next start (one whose `next`
# answered anything else after its viewing work has marked it already); any other attempted it.
[ -n "$DELETION_WORKED$VIEW_WORKED" ] || mark_slot
# The final line names the most consequential work of the start: a deletion, then a run, then the views, then the
# decision of `evaluate` alone.
if [ "$EXIT" -eq 0 ]; then
    if [ -n "$DELETION_WORKED" ]; then
        echo "DELETION ${DEL_OUTCOME:-none} ${REQUEST_ID:--} ${DEL_STATE:--}"
    elif [ -n "$VIEW_LINE" ] && [ -z "$RUN_WORKED" ]; then
        # Viewing work and no run: the last view that a step loaded or closed (a view that changed meanwhile is none).
        echo "VIEW $VIEW_LINE"
    elif [ -z "$DUE" ]; then
        echo "NOT_DUE"
    elif [ -n "$EVAL_RESULT" ] && [ -z "$RUN_WORKED" ]; then
        # The hourly work worked on no run and no view: the decision of `evaluate`, with the open run that `next` named.
        echo "EVALUATE $EVAL_RESULT ${RUN_ID:--} ${STATE:--}"
    else
        echo "ARCHIVE ${OUTCOME:-none} ${RUN_ID:--} ${STATE:--}"
    fi
fi
[ -z "$KEEP" ] || say "reports: $REC"
exit "$EXIT"
