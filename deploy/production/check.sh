#!/bin/sh
# PartFlow host checks (P16-S6; OPERATIONS_RUNBOOK §2, §9). Run from the repository root of the RUNNING release's
# checkout, as the account that owns PARTFLOW_BACKUP_DIR; scheduled every 15 minutes by the host scheduler, whose
# failure notification is the alert channel (OD-16-11). `deploy/production/check.sh --help` prints the usage.
#
# One run evaluates 13 checks in a fixed order and prints one line per check, `PASS|FAIL|SKIP <id> <reason>`, then
# optional `NOTE` lines: https, certificate, containers, restarts, errors, disk_data, disk_backup, disk_docker,
# disk_archive (PARTFLOW_ARCHIVE_DIR), then database, schema and backup_age from one read-only `status` run (the `status`
# ops service, as the application database role, the backup directory mounted read-only), and archival_proposal from
# one read-only `history-archive status` run (the `maintenance` ops service, as the maintenance database role, with the
# archive folder and, when archive.sh would mount it, the copy folder; OPERATIONS_RUNBOOK §11), which also fails when a
# permanent deletion request, APPROVED or RUNNING, was requested longer ago than --deletion-wait-minutes (the history
# archive task has not carried it out, or has not finished it) and while archive.sh's incident record
# <records-dir>/archive/reconcile-after-purge exists (a reconcile after a purge or a deletion that did not end clean;
# OPERATIONS_RUNBOOK §8). While archival is not set up (PARTFLOW_ARCHIVE_DIR empty), archival_proposal is SKIP, but the
# cheap check of archive.sh (`history-archive settings` with `docker compose exec` in the running backend container)
# still looks for an open permanent deletion request, which nothing carries out then: one requested longer ago than
# --deletion-wait-minutes fails the line. The newest backup of any kind counts for backup_age, a pre-purge backup
# included. It never starts, stops or changes a service, never writes in the backup or archive directories and never
# repairs; after a `status` run that hit its time bound it removes the one-off container that run left (one that was not
# there before it), and after a `maintenance` run that hit its bound only the container of that run (its own --name),
# never a step of archive.sh.
#
# Exit status: 0 nothing to notify (every line PASS/SKIP, or the failing set was already reported less than
# --renotify-hours ago); 1 at least one FAIL to notify; 2 could not run (one line `ERROR check could not run: ...`).
# State (<state-dir>, mode 0700): restarts (restart baseline), errors-since (log cursor), alert-state (re-notification),
# written only by a full run (no --only, no --no-state); last-check.txt (every run); status.json and status.err (a copy
# of the status run's output, which the run evaluates in its own work directory) and growth.tsv (a full run's status).
set -eu

# The values in the shell would override the env file for every Compose command.
unset PARTFLOW_RELEASE PARTFLOW_COMMIT PARTFLOW_ACCEPT_SCHEMA_REVISION PARTFLOW_BACKUP_DIR PARTFLOW_ARCHIVE_DIR \
    PARTFLOW_ARCHIVE_COPY_DIR COMPOSE_PROJECT_NAME COMPOSE_FILE COMPOSE_PROFILES
umask 077

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
CHECK_IDS='https certificate containers restarts errors disk_data disk_backup disk_docker disk_archive database schema backup_age archival_proposal'
RELEASE_PATTERN='^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'
DOCKER_SECONDS=30
OPENSSL_SECONDS=15
STATUS_SECONDS=120
# A release lock older than this is reported as stale (FAIL database) instead of skipping the status checks.
RELEASE_LOCK_HOURS=4
# The default of --deletion-wait-minutes: three 5-minute starts of the history archive task, plus the time a deletion
# takes (its pre-purge backup and the reconcile after it).
DELETION_WAIT_MINUTES=15

usage() {
    cat <<'EOF'
Usage: deploy/production/check.sh --url URL [--env-file .env.production] [--state-dir DIR]
           [--cacert FILE] [--resolve-to ADDRESS]
           [--max-backup-age-hours N] [--min-free-percent N] [--min-cert-days N]
           [--renotify-hours N] [--deletion-wait-minutes N] [--allow-accepted-schema] [--only ID]... [--quiet]
           [--records-dir DIR] [--no-state] [--rehearsal --project NAME]

Run from the repository root of the running release's checkout, as the account that owns PARTFLOW_BACKUP_DIR.
  --url URL                  https://HOST[:PORT] as users reach PartFlow (http://127.0.0.1:PORT only with --rehearsal)
  --env-file FILE            default .env.production
  --state-dir DIR            default $HOME/partflow-monitoring (created with mode 0700)
  --cacert FILE              CA certificate for curl (a private CA)
  --resolve-to ADDRESS       connect to this IPv4 address for HOST (the host cannot resolve its own name)
  --max-backup-age-hours N   1-720, default: the interval of Administration → Backups plus 2
  --min-free-percent N       0-100, default 15 (100 fails every measured disk: the notification test)
  --min-cert-days N          1-365, default 21
  --renotify-hours N         0-168, default 6; an unchanged failing set is notified again after N hours (0 = always)
  --deletion-wait-minutes N  1-1440, default 15: archival_proposal fails when a permanent deletion request is still open
                             (APPROVED or RUNNING) N minutes after it was requested (the history archive task has not
                             carried it out, or archival is not set up)
  --allow-accepted-schema    an `accepted` schema (rollback path 2 override) passes https
  --only ID                  evaluate only this check (repeatable); state files are not changed
  --quiet                    print nothing unless the exit status is non-zero
  --records-dir DIR          release records (default $HOME/partflow-deployments); the release lock is
                             DIR/.release.lock: while it exists the status checks are skipped; archive.sh's incident
                             record DIR/archive/reconcile-after-purge (after a purge or a deletion) fails
                             archival_proposal
  --no-state                 evaluate against the stored state without changing it (manual diagnosis)
  --rehearsal --project NAME a throwaway Compose project (never partflow, partflow-production or partflow-staging)
Check ids: https certificate containers restarts errors disk_data disk_backup disk_docker disk_archive
           database schema backup_age archival_proposal
disk_archive and archival_proposal use PARTFLOW_ARCHIVE_DIR and PARTFLOW_ARCHIVE_COPY_DIR of the env file; both are
SKIP while PARTFLOW_ARCHIVE_DIR is empty (DEPLOYMENT §3.1), except that archival_proposal then fails for a permanent
deletion request that waits, since nothing carries it out (history-archive settings in the running backend container).
Output: one line per check, PASS|FAIL|SKIP <id> <reason>, then NOTE lines.
Exit: 0 nothing to notify, 1 notify (a FAIL), 2 could not run.
EOF
}

STATE_READY=
STATE_DIR=
WORK=
cleanup() {
    [ -z "$WORK" ] || rm -rf "$WORK"
}
trap cleanup EXIT

utc() {
    date -u +%Y-%m-%dT%H:%M:%SZ
}
# utc_of EPOCH: the UTC time of EPOCH seconds.
utc_of() {
    python3 -c 'import sys, time; print(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(sys.argv[1]))))' "$1"
}
# shift_utc TIME SECONDS: TIME (YYYY-mm-ddTHH:MM:SSZ) moved by SECONDS.
shift_utc() {
    python3 -c 'import calendar, sys, time
t = calendar.timegm(time.strptime(sys.argv[1], "%Y-%m-%dT%H:%M:%SZ")) + int(sys.argv[2])
print(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t)))' "$1" "$2"
}
# replace FILE: move FILE.tmp (written by the caller) over FILE (atomic within the state directory).
replace() {
    mv -f "$1.tmp" "$1"
}
write_last() {
    {
        echo "$(utc) exit $1"
        shift
        for text in "$@"; do
            [ ! -f "$text" ] || cat "$text"
        done
    } >"$STATE_DIR/last-check.txt.tmp" && replace "$STATE_DIR/last-check.txt"
}
could_not_run() {
    echo "ERROR check could not run: $1"
    if [ -n "$STATE_READY" ]; then
        printf '%s\n' "ERROR check could not run: $1" >"$WORK/error"
        write_last 2 "$WORK/error" || true
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

URL=
ENV_FILE=.env.production
STATE_DIR_OPTION=
CACERT=
RESOLVE_TO=
MAX_AGE=
MIN_FREE=15
MIN_CERT_DAYS=21
RENOTIFY_HOURS=6
ALLOW_ACCEPTED=
ONLY=
QUIET=
RECORDS_DIR=
NO_STATE=
REHEARSAL=
PROJECT=
SEEN=' '
while [ "$#" -gt 0 ]; do
    option=$1
    if [ "$option" != --only ]; then
        case "$SEEN" in
            *" $option "*) usage_error "$option is given twice." ;;
        esac
        SEEN="$SEEN$option "
    fi
    case "$option" in
        -h | --help)
            usage
            exit 0
            ;;
        --allow-accepted-schema) ALLOW_ACCEPTED=1; shift; continue ;;
        --quiet) QUIET=1; shift; continue ;;
        --no-state) NO_STATE=1; shift; continue ;;
        --rehearsal) REHEARSAL=1; shift; continue ;;
        --archive-dir)
            # Accepted for compatibility with commands written before archival step 1, then ignored.
            [ "$#" -ge 2 ] || usage_error "$option needs a value."
            echo "check: --archive-dir is ignored: disk_archive measures PARTFLOW_ARCHIVE_DIR of the env file" \
                "(DEPLOYMENT §3.1); remove the option." >&2
            shift 2
            continue
            ;;
        --url | --env-file | --state-dir | --cacert | --resolve-to | --max-backup-age-hours | \
            --min-free-percent | --min-cert-days | --renotify-hours | --deletion-wait-minutes | --only | --records-dir | \
            --project)
            [ "$#" -ge 2 ] || usage_error "$option needs a value."
            value=$2
            shift 2
            ;;
        *) usage_error "unknown argument $option." ;;
    esac
    case "$option" in
        --url) URL=$value ;;
        --env-file) ENV_FILE=$value ;;
        --state-dir) STATE_DIR_OPTION=$value ;;
        --cacert) CACERT=$value ;;
        --resolve-to) RESOLVE_TO=$value ;;
        --max-backup-age-hours) check_count "$option" "$value" 1 720; MAX_AGE=$value ;;
        --min-free-percent) check_count "$option" "$value" 0 100; MIN_FREE=$value ;;
        --min-cert-days) check_count "$option" "$value" 1 365; MIN_CERT_DAYS=$value ;;
        --renotify-hours) check_count "$option" "$value" 0 168; RENOTIFY_HOURS=$value ;;
        --deletion-wait-minutes) check_count "$option" "$value" 1 1440; DELETION_WAIT_MINUTES=$value ;;
        --only)
            case " $CHECK_IDS " in
                *" $value "*) ;;
                *) usage_error "--only $value is not a check id ($CHECK_IDS)." ;;
            esac
            ONLY="$ONLY $value"
            ;;
        --records-dir) RECORDS_DIR=$value ;;
        --project) PROJECT=$value ;;
    esac
done

[ -n "$URL" ] || usage_error "--url is required."
if printf '%s' "$URL" | grep -Eq '^https://[A-Za-z0-9.-]+(:[0-9]{1,5})?$'; then
    SCHEME=https
elif [ -n "$REHEARSAL" ] && printf '%s' "$URL" | grep -Eq '^http://127\.0\.0\.1:[0-9]{1,5}$'; then
    SCHEME=http
else
    usage_error "--url must be https://HOST[:PORT] (http://127.0.0.1:PORT only with --rehearsal)."
fi
HOST_PORT=${URL#*://}
HOST=${HOST_PORT%%:*}
case "$HOST_PORT" in
    *:*) PORT=${HOST_PORT##*:} ;;
    *) PORT=443 ;;
esac
if [ -n "$RESOLVE_TO" ]; then
    printf '%s' "$RESOLVE_TO" | grep -Eq '^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$' \
        || usage_error "--resolve-to must be an IPv4 address."
fi
if [ -n "$CACERT" ]; then
    [ -f "$CACERT" ] || usage_error "--cacert $CACERT is not a file."
fi
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
if [ -z "$STATE_DIR_OPTION" ] || [ -z "$RECORDS_DIR" ]; then
    [ -n "${HOME:-}" ] || usage_error "HOME is not set: give --state-dir and --records-dir."
fi
STATE_DIR=${STATE_DIR_OPTION:-$HOME/partflow-monitoring}
RECORDS_DIR=${RECORDS_DIR:-$HOME/partflow-deployments}
FULL=1
[ -z "$ONLY$NO_STATE" ] || FULL=

selected() {
    [ -z "$ONLY" ] && return 0
    case "$ONLY " in
        *" $1 "*) return 0 ;;
    esac
    return 1
}
any_selected() {
    for id in "$@"; do
        if selected "$id"; then
            return 0
        fi
    done
    return 1
}

# ---------------------------------------------------------------------------
# 0 preflight
# ---------------------------------------------------------------------------

for tool in docker curl python3 df timeout date id mv; do
    command -v "$tool" >/dev/null 2>&1 || could_not_run "$tool is not installed."
done
if [ "$SCHEME" = https ] && selected certificate; then
    command -v openssl >/dev/null 2>&1 || could_not_run "openssl is not installed."
fi
# Another `timeout` (for example Windows' timeout.exe) cannot bound a command: prove the POSIX-style tool.
timeout 5 true >/dev/null 2>&1 || could_not_run "'timeout 5 true' failed: timeout is not the coreutils/BusyBox tool."
[ -f compose.production.yaml ] \
    || could_not_run "Run it from the repository root of the running release's checkout (compose.production.yaml not found)."
mkdir -p "$STATE_DIR" 2>/dev/null && chmod 700 "$STATE_DIR" 2>/dev/null \
    || could_not_run "the state directory $STATE_DIR cannot be created."
WORK=$(mktemp -d) || could_not_run "a temporary directory cannot be created."
STATE_READY=1
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
# Movement-history archival (OPERATIONS_RUNBOOK §11): optional until it is set up; a malformed value is reported by
# disk_archive and archival_proposal instead of stopping every other check.
ARCHIVE_DIR=$(sed -n 's/^PARTFLOW_ARCHIVE_DIR=//p' "$ENV_FILE" | tail -n 1)
COPY_DIR=$(sed -n 's/^PARTFLOW_ARCHIVE_COPY_DIR=//p' "$ENV_FILE" | tail -n 1)
ARCHIVE_PROBLEM=
for value in "$ARCHIVE_DIR" "$COPY_DIR"; do
    [ -n "$value" ] || continue
    case "$value" in
        /*) ;;
        *) ARCHIVE_PROBLEM="PARTFLOW_ARCHIVE_DIR and PARTFLOW_ARCHIVE_COPY_DIR in $ENV_FILE must be absolute paths" ;;
    esac
    case "$value" in
        *\"* | *\'* | *[[:space:]]* | *:*)
            ARCHIVE_PROBLEM="PARTFLOW_ARCHIVE_DIR and PARTFLOW_ARCHIVE_COPY_DIR in $ENV_FILE must be unquoted, without spaces or ':'" ;;
    esac
done
[ -z "$ARCHIVE_DIR" ] || ARCHIVE_DIR=${ARCHIVE_DIR%/}
[ -z "$COPY_DIR" ] || COPY_DIR=${COPY_DIR%/}
printf '%s' "$HTTP_PORT" | grep -Eq '^[0-9]{1,5}$' || could_not_run "PARTFLOW_HTTP_PORT in $ENV_FILE must be a port number."
if [ -n "$PROJECT" ]; then
    PROJECT_NAME=$PROJECT
else
    PROJECT_NAME=$(sed -n 's/^name: *//p' compose.production.yaml | head -n 1)
    [ -n "$PROJECT_NAME" ] || could_not_run "compose.production.yaml names no project."
fi
USER_IDS="$(id -u):$(id -g)"

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
# dk ARGS...: one docker command, bounded by timeout.
dk() {
    timeout "$DOCKER_SECONDS" docker "$@"
}

rc=0
pf "$DOCKER_SECONDS" config --quiet >"$WORK/config.err" 2>&1 || rc=$?
if [ "$rc" -eq 124 ]; then
    could_not_run "'docker compose ... config --quiet' gave no answer within $DOCKER_SECONDS s."
elif [ "$rc" -ne 0 ]; then
    sed -n '$p' "$WORK/config.err" | tr -d '\r' >&2
    could_not_run "'docker compose ... config --quiet' failed (exit $rc)."
fi

LINES=$WORK/lines
NOTES=$WORK/notes
: >"$LINES"
: >"$NOTES"
# emit WORD ID REASON: one check line (only for a selected check), reason on one line, at most 300 characters.
emit() {
    if selected "$2"; then
        reason=$(printf '%s' "$3" | tr '\r\n\t' '   ' | cut -c 1-300)
        printf '%s %s %s\n' "$1" "$2" "$reason" >>"$LINES"
    fi
}
note() {
    printf 'NOTE %s\n' "$1" >>"$NOTES"
}
# join_lines SEPARATOR: stdin's lines joined into one line.
join_lines() {
    awk -v separator="$1" 'NR > 1 { printf "%s", separator } { printf "%s", $0 }'
}

# ---------------------------------------------------------------------------
# 1 https
# ---------------------------------------------------------------------------

json_value() {
    sed -n 's/.*"'"$1"'":"\([^"]*\)".*/\1/p' "$2" 2>/dev/null | head -n 1
}

check_https() {
    set -- -sS --max-time 15
    [ -z "$CACERT" ] || set -- "$@" --cacert "$CACERT"
    [ -z "$RESOLVE_TO" ] || set -- "$@" --resolve "$HOST:$PORT:$RESOLVE_TO"
    set -- "$@" -o "$WORK/health.json" -w '%{http_code}' "$URL/api/health"
    : >"$WORK/health.json"
    crc=0
    code=$(curl "$@" 2>"$WORK/curl.err") || crc=$?
    case "$crc" in
        0) ;;
        6) emit FAIL https "host name $HOST not resolved (curl 6)"; return 0 ;;
        7) emit FAIL https "connection failed (curl 7)"; return 0 ;;
        28) emit FAIL https "no answer within 15 s (curl 28)"; return 0 ;;
        35) emit FAIL https "TLS handshake failed (curl 35)"; return 0 ;;
        60) emit FAIL https "TLS certificate not trusted (curl 60)"; return 0 ;;
        *) emit FAIL https "request failed (curl $crc)"; return 0 ;;
    esac
    schema=$(json_value schema "$WORK/health.json")
    release=$(json_value release "$WORK/health.json")
    case "$code" in
        200) ;;
        503)
            case "$schema" in
                mismatch) emit FAIL https "HTTP 503 from /api/health: schema mismatch (PartFlow refuses changes)" ;;
                unknown) emit FAIL https "HTTP 503 from /api/health: database unreachable" ;;
                *) emit FAIL https "HTTP 503 from /api/health" ;;
            esac
            return 0
            ;;
        502) emit FAIL https "HTTP 502 from web: the backend is not answering"; return 0 ;;
        504) emit FAIL https "HTTP 504 from web: the backend did not answer in time"; return 0 ;;
        *) emit FAIL https "HTTP $code from /api/health"; return 0 ;;
    esac
    if [ "$release" != "$RUNNING_RELEASE" ]; then
        emit FAIL https "running release ${release:-unknown} differs from $ENV_FILE ($RUNNING_RELEASE)"
        return 0
    fi
    case "$schema" in
        current) emit PASS https "HTTP 200, release $release, schema current" ;;
        accepted)
            accepted=$(json_value accepted_revision "$WORK/health.json")
            if [ -n "$ALLOW_ACCEPTED" ]; then
                emit PASS https "HTTP 200, release $release, schema accepted by the rollback override $accepted"
            else
                emit FAIL https "schema accepted by the rollback override $accepted (use --allow-accepted-schema while that is intended)"
            fi
            ;;
        *) emit FAIL https "HTTP 200 with schema ${schema:-unknown}" ;;
    esac
}

# ---------------------------------------------------------------------------
# 2 certificate
# ---------------------------------------------------------------------------

check_certificate() {
    if [ "$SCHEME" != https ]; then
        emit SKIP certificate "not an HTTPS URL"
        return 0
    fi
    connect=${RESOLVE_TO:-$HOST}:$PORT
    src=0
    timeout "$OPENSSL_SECONDS" openssl s_client -connect "$connect" -servername "$HOST" </dev/null \
        >"$WORK/certificate.pem" 2>"$WORK/s_client.err" || src=$?
    if [ "$src" -eq 124 ]; then
        emit FAIL certificate "no answer within $OPENSSL_SECONDS s from $HOST:$PORT"
        return 0
    fi
    end=$(timeout "$OPENSSL_SECONDS" openssl x509 -noout -enddate <"$WORK/certificate.pem" 2>/dev/null \
        | sed -n 's/^notAfter=//p' | head -n 1) || end=
    if [ -z "$end" ]; then
        emit FAIL certificate "no certificate could be read from $HOST:$PORT"
        return 0
    fi
    shown=$(python3 -c 'import sys, time
print(time.strftime("%Y-%m-%d %H:%M:%S GMT", time.strptime(" ".join(sys.argv[1].split()), "%b %d %H:%M:%S %Y %Z")))' \
        "$end" 2>/dev/null) || shown=$end
    xrc=0
    timeout "$OPENSSL_SECONDS" openssl x509 -noout -checkend "$((MIN_CERT_DAYS * 86400))" <"$WORK/certificate.pem" \
        >/dev/null 2>&1 || xrc=$?
    case "$xrc" in
        0) emit PASS certificate "certificate for $HOST valid until $shown (more than $MIN_CERT_DAYS days)" ;;
        1) emit FAIL certificate "certificate for $HOST expires $shown (within $MIN_CERT_DAYS days)" ;;
        *) emit FAIL certificate "the certificate of $HOST could not be checked (openssl exit $xrc)" ;;
    esac
}

# ---------------------------------------------------------------------------
# 3 containers, 4 restarts (the service containers only: one-off `run` containers are never inspected)
# ---------------------------------------------------------------------------

CONTAINERS=$WORK/containers
GATHER_ERROR=
DB_STATE=unknown
gather_containers() {
    : >"$CONTAINERS"
    : >"$WORK/container-problems"
    : >"$WORK/container-starting"
    for svc in db backend web; do
        drc=0
        ids=$(dk ps -a -q --filter "label=com.docker.compose.project=$PROJECT_NAME" \
            --filter "label=com.docker.compose.service=$svc" --filter "label=com.docker.compose.oneoff=False" \
            2>>"$WORK/docker.err") || drc=$?
        if [ "$drc" -eq 124 ]; then
            GATHER_ERROR="no answer within $DOCKER_SECONDS s"
            return 0
        elif [ "$drc" -ne 0 ]; then
            GATHER_ERROR="cannot list the containers (docker ps exit $drc)"
            return 0
        fi
        count=0
        for id in $ids; do
            count=$((count + 1))
        done
        if [ "$count" -eq 0 ]; then
            echo "$svc has no container" >>"$WORK/container-problems"
            [ "$svc" != db ] || DB_STATE=missing
            continue
        elif [ "$count" -gt 1 ]; then
            echo "$svc has $count containers" >>"$WORK/container-problems"
            continue
        fi
        drc=0
        info=$(dk inspect --format '{{.Id}} {{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} {{.RestartCount}} {{.State.OOMKilled}} {{.State.StartedAt}}' \
            "$ids" 2>>"$WORK/docker.err") || drc=$?
        if [ "$drc" -eq 124 ]; then
            GATHER_ERROR="no answer within $DOCKER_SECONDS s"
            return 0
        elif [ "$drc" -ne 0 ]; then
            GATHER_ERROR="cannot inspect the $svc container (docker inspect exit $drc)"
            return 0
        fi
        # shellcheck disable=SC2086
        set -- $info
        if [ "$#" -ne 6 ]; then
            GATHER_ERROR="unexpected docker inspect answer for $svc"
            return 0
        fi
        echo "$svc $*" >>"$CONTAINERS"
        [ "$svc" != db ] || DB_STATE=$2
        if [ "$2" != running ]; then
            echo "$svc is $2" >>"$WORK/container-problems"
        elif [ "$3" = unhealthy ]; then
            echo "$svc is unhealthy" >>"$WORK/container-problems"
        elif [ "$3" = starting ]; then
            echo "$svc" >>"$WORK/container-starting"
        fi
    done
}

check_containers() {
    if [ -n "$GATHER_ERROR" ]; then
        emit FAIL containers "$GATHER_ERROR"
    elif [ -s "$WORK/container-problems" ]; then
        emit FAIL containers "$(join_lines '; ' <"$WORK/container-problems")"
    elif [ -s "$WORK/container-starting" ]; then
        emit PASS containers "db, backend, web running; $(join_lines ', ' <"$WORK/container-starting") (health: starting)"
    else
        emit PASS containers "db, backend, web running and healthy"
    fi
}

RESTARTS_READY=
check_restarts() {
    if [ -n "$GATHER_ERROR" ]; then
        emit FAIL restarts "$GATHER_ERROR"
        return 0
    fi
    : >"$WORK/restarts.new"
    : >"$WORK/restart-problems"
    while read -r svc id status health count oom started; do
        echo "$svc $id $count $started" >>"$WORK/restarts.new"
        base=$(grep "^$svc " "$STATE_DIR/restarts" 2>/dev/null | head -n 1) || base=
        # shellcheck disable=SC2086
        set -- $base
        if [ "$#" -ne 4 ] || [ "$2" != "$id" ]; then
            continue
        fi
        printf '%s' "$3" | grep -Eq '^[0-9]+$' || continue
        oom_text=
        [ "$oom" != true ] || oom_text=" (last exit OOM-killed)"
        if [ "$count" -gt "$3" ]; then
            echo "$svc restarted $((count - $3)) times since the last check$oom_text" >>"$WORK/restart-problems"
        elif [ "$count" -lt "$3" ]; then
            if [ "$count" -gt 0 ]; then
                echo "$svc restarted $count times since it was started at $started$oom_text" >>"$WORK/restart-problems"
            else
                note "restarts $svc counter reset at $started; new baseline"
            fi
        fi
    done <"$CONTAINERS"
    RESTARTS_READY=1
    if [ -s "$WORK/restart-problems" ]; then
        emit FAIL restarts "$(join_lines '; ' <"$WORK/restart-problems")"
    else
        emit PASS restarts "no restart since the last check"
    fi
}

# ---------------------------------------------------------------------------
# 5 errors: backend log records at ERROR or CRITICAL since the stored cursor
# ---------------------------------------------------------------------------

NEW_CURSOR=
check_errors() {
    cursor=$(sed -n '1p' "$STATE_DIR/errors-since" 2>/dev/null) || cursor=
    t0=$(utc)
    printf '%s' "$cursor" | grep -Eq '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$' \
        || cursor=$(shift_utc "$t0" -900)
    lrc=0
    pf "$DOCKER_SECONDS" logs --no-log-prefix --since "$cursor" --until "$t0" backend >"$WORK/backend.log" \
        2>"$WORK/logs.err" || lrc=$?
    if [ "$lrc" -eq 124 ]; then
        emit FAIL errors "no answer within $DOCKER_SECONDS s"
        return 0
    elif [ "$lrc" -ne 0 ]; then
        emit FAIL errors "cannot read the backend log (docker compose logs exit $lrc)"
        return 0
    fi
    NEW_CURSOR=$t0
    # Docker removes a container's log with the container: a backend recreated since the stored restart baseline (a
    # release, a password rotation) took the records it wrote after the cursor with it.
    if [ -s "$CONTAINERS" ]; then
        current=$(awk '$1 == "backend" { print $2 }' "$CONTAINERS" | head -n 1)
        started=$(awk '$1 == "backend" { print $7 }' "$CONTAINERS" | head -n 1)
        base=$(awk '$1 == "backend" { print $2 }' "$STATE_DIR/restarts" 2>/dev/null | head -n 1) || base=
        if [ -n "$current" ] && [ -n "$base" ] && [ "$current" != "$base" ]; then
            note "errors backend was recreated (started at $started): records the replaced container wrote after $cursor could not be read"
        fi
    fi
    grep -E '"level":"(ERROR|CRITICAL)"' "$WORK/backend.log" >"$WORK/error-records" 2>/dev/null || true
    count=$(wc -l <"$WORK/error-records" | tr -d ' ')
    if [ "$count" -eq 0 ]; then
        emit PASS errors "no error records in the backend log since $cursor"
        return 0
    fi
    ids=$(sed -n 's/.*"request_id":"\([A-Za-z0-9._-]*\)".*/\1/p' "$WORK/error-records" | awk '!seen[$0]++' \
        | head -n 3 | join_lines ', ')
    if [ -n "$ids" ]; then
        emit FAIL errors "$count error records in the backend log since $cursor (request ids: $ids)"
    else
        emit FAIL errors "$count error records in the backend log since $cursor"
    fi
}

# ---------------------------------------------------------------------------
# 6-9 disks: free % = available x 100 / (used + available)
# ---------------------------------------------------------------------------

# disk_line ID DF_OUTPUT LABEL
disk_line() {
    sizes=$(awk 'NR == 2 {print $3, $4}' "$2")
    used=${sizes% *}
    avail=${sizes#* }
    if ! printf '%s' "$used" | grep -Eq '^[0-9]+$' || ! printf '%s' "$avail" | grep -Eq '^[0-9]+$' \
        || [ $((used + avail)) -eq 0 ]; then
        emit FAIL "$1" "cannot measure: unexpected df answer $3"
        return 0
    fi
    percent=$((avail * 100 / (used + avail)))
    sizes=$(awk -v a="$avail" -v t="$((used + avail))" 'BEGIN { printf "%.1f GiB of %.1f GiB", a / 1048576, t / 1048576 }')
    if [ "$percent" -ge "$MIN_FREE" ]; then
        emit PASS "$1" "$percent % free ($sizes) $3; threshold $MIN_FREE %"
    else
        emit FAIL "$1" "$percent % free ($sizes) $3; threshold $MIN_FREE %"
    fi
}

# host_disk ID PATH LABEL
host_disk() {
    hrc=0
    timeout "$DOCKER_SECONDS" df -Pk "$2" >"$WORK/df.out" 2>"$WORK/df.err" || hrc=$?
    if [ "$hrc" -eq 124 ]; then
        emit FAIL "$1" "no answer within $DOCKER_SECONDS s"
    elif [ "$hrc" -ne 0 ]; then
        emit FAIL "$1" "cannot measure $2 (df exit $hrc)"
    else
        disk_line "$1" "$WORK/df.out" "$3"
    fi
}

check_disk_data() {
    case "$DB_STATE" in
        unknown | running) ;;
        *)
            emit FAIL disk_data "cannot measure: db is not running"
            return 0
            ;;
    esac
    xrc=0
    pf "$DOCKER_SECONDS" exec -T db df -Pk /var/lib/postgresql/data >"$WORK/df.out" 2>"$WORK/df.err" || xrc=$?
    if [ "$xrc" -eq 124 ]; then
        emit FAIL disk_data "no answer within $DOCKER_SECONDS s"
    elif [ "$xrc" -ne 0 ]; then
        if grep -q 'not running' "$WORK/df.err" 2>/dev/null; then
            emit FAIL disk_data "cannot measure: db is not running"
        else
            emit FAIL disk_data "cannot measure: df failed in db (exit $xrc)"
        fi
    else
        disk_line disk_data "$WORK/df.out" "on the database volume"
    fi
}

check_disk_docker() {
    irc=0
    root=$(dk info --format '{{.DockerRootDir}}' 2>"$WORK/info.err") || irc=$?
    root=$(printf '%s' "$root" | tr -d '\r')
    if [ "$irc" -eq 124 ]; then
        emit FAIL disk_docker "no answer within $DOCKER_SECONDS s"
    elif [ "$irc" -ne 0 ] || [ -z "$root" ]; then
        emit FAIL disk_docker "cannot read the Docker root (docker info exit $irc)"
    else
        host_disk disk_docker "$root" "on the Docker root (images, container logs)"
    fi
}

# ---------------------------------------------------------------------------
# 10-13 database, schema, backup_age, archival_proposal: one read-only `status` run
# ---------------------------------------------------------------------------

STATUS_IDS='database schema backup_age archival_proposal'
ARCHIVE_NOT_SET_UP="archival is not set up: PARTFLOW_ARCHIVE_DIR is empty in $ENV_FILE (DEPLOYMENT §3.1)"
# archive.sh's incident record: the reconcile after a purge or a deletion did not end clean (read only).
PURGE_INCIDENT=$RECORDS_DIR/archive/reconcile-after-purge
# one_off SERVICE: the ids of this project's one-off containers of SERVICE (`docker compose run`).
one_off() {
    dk ps -a -q --filter "label=com.docker.compose.project=$PROJECT_NAME" \
        --filter "label=com.docker.compose.service=$1" --filter "label=com.docker.compose.oneoff=True" \
        2>>"$WORK/docker.err"
}
# remove_new_one_off SERVICE BEFORE: remove the one-off containers of SERVICE that were not in BEFORE (a timed-out run).
remove_new_one_off() {
    for id in $(one_off "$1" || true); do
        case " $(printf '%s' "$2" | tr '\n' ' ') " in
            *" $id "*) ;;
            *) dk rm -f "$id" >/dev/null 2>>"$WORK/docker.err" || true ;;
        esac
    done
}
# mount_point PATH: the mount point of the file system holding PATH (empty when df cannot tell).
mount_point() {
    timeout "$DOCKER_SECONDS" df -P "$1" 2>/dev/null | awk 'NR == 2 { print $6 }'
}
# check_unarchived_deletion: archival_proposal while archival is not set up. PartFlow records permanent deletion
# requests all the same, and without the history archive task nothing carries them out: the cheap check of archive.sh
# (`history-archive settings` in the running backend container, as the application database role; it creates no
# container) looks for an open request, and one requested longer ago than --deletion-wait-minutes fails the line
# (deletion_request_waiting). Otherwise, and when the check gives no answer, the line is SKIP.
check_unarchived_deletion() {
    src=0
    pf "$DOCKER_SECONDS" exec -T backend python -m app.cli history-archive settings >"$WORK/archive-settings.json" \
        2>"$WORK/archive-settings.err" || src=$?
    for name in archive-settings.json archive-settings.err; do
        cp "$WORK/$name" "$STATE_DIR/$name.tmp" && replace "$STATE_DIR/$name"
    done
    if [ "$src" -eq 124 ]; then
        emit SKIP archival_proposal "$ARCHIVE_NOT_SET_UP; the deletion request check (history-archive settings in the backend container) gave no answer within $DOCKER_SECONDS s"
        return 0
    fi
    python3 "$SCRIPT_DIR/monitor_report.py" settings "$WORK/archive-settings.json" "$src" \
        --skip-reason "$ARCHIVE_NOT_SET_UP" --deletion-wait-minutes "$DELETION_WAIT_MINUTES" \
        >"$WORK/archive.out" 2>"$WORK/archive-monitor.err" || true
    text=$(tr -d '\r' <"$WORK/archive.out" | grep -E '^(FAIL|SKIP) archival_proposal ' | head -n 1) || text=
    if [ -z "$text" ]; then
        emit SKIP archival_proposal "$ARCHIVE_NOT_SET_UP; the deletion request check could not be evaluated (monitor_report.py)"
        return 0
    fi
    word=${text%% *}
    rest=${text#* }
    emit "$word" archival_proposal "${rest#* }"
}
# check_archival: archival_proposal from one read-only `history-archive status` run in the maintenance service. The
# copy folder is mounted exactly as archive.sh mounts it (a long-syntax bind with create_host_path: false in an override
# file), only while it is a writable folder on a file system that is neither the archive folder's nor `/`; otherwise
# the run reports copy_dir_unusable, and the line gives the reason. A permanent deletion request still open (APPROVED or
# RUNNING) longer than --deletion-wait-minutes after it was requested fails the line (deletion_request_waiting).
check_archival() {
    if [ -n "$ARCHIVE_PROBLEM" ]; then
        emit FAIL archival_proposal "$ARCHIVE_PROBLEM (DEPLOYMENT §3.1)"
        return 0
    fi
    if [ -z "$ARCHIVE_DIR" ]; then
        check_unarchived_deletion
        return 0
    fi
    if [ ! -d "$ARCHIVE_DIR" ]; then
        emit FAIL archival_proposal "archive_dir_unusable: the archive folder $ARCHIVE_DIR (PARTFLOW_ARCHIVE_DIR) does not exist"
        return 0
    fi
    copy_mount=
    copy_reason=
    if [ -n "$COPY_DIR" ]; then
        if [ ! -d "$COPY_DIR" ]; then
            copy_reason="$COPY_DIR does not exist (is the share or disk mounted?)"
        elif [ ! -w "$COPY_DIR" ]; then
            copy_reason="$COPY_DIR is not writable by this account"
        else
            a_mount=$(mount_point "$ARCHIVE_DIR") || a_mount=
            c_mount=$(mount_point "$COPY_DIR") || c_mount=
            if [ -z "$a_mount" ] || [ -z "$c_mount" ]; then
                copy_reason="the file systems of $ARCHIVE_DIR and $COPY_DIR could not be read (df -P)"
            elif [ "$c_mount" = / ]; then
                copy_reason="$COPY_DIR is on the host's root file system (/), not on another device (is the share or disk mounted?)"
            elif [ "$c_mount" = "$a_mount" ]; then
                copy_reason="$COPY_DIR is on the archive folder's file system ($c_mount), not on another device"
            else
                copy_mount=1
            fi
        fi
    fi
    # Its own container name: a timed-out check removes only its own run, never an archive.sh step.
    run_name=$PROJECT_NAME-archive-check-$$
    set -- --profile ops run --rm --no-deps -T --user "$USER_IDS" --name "$run_name" maintenance status
    arc=0
    if [ -n "$copy_mount" ]; then
        cat >"$WORK/copy-mount.yaml" <<EOF
services:
    maintenance:
        volumes:
            - type: bind
              source: '$COPY_DIR'
              target: /archive-copy
              bind:
                  create_host_path: false
EOF
        if [ -n "$PROJECT" ]; then
            timeout "$STATUS_SECONDS" docker compose -p "$PROJECT" -f compose.production.yaml -f "$WORK/copy-mount.yaml" \
                --env-file "$ENV_FILE" "$@" >"$WORK/archive-status.json" 2>"$WORK/archive-status.err" || arc=$?
        else
            timeout "$STATUS_SECONDS" docker compose -f compose.production.yaml -f "$WORK/copy-mount.yaml" \
                --env-file "$ENV_FILE" "$@" >"$WORK/archive-status.json" 2>"$WORK/archive-status.err" || arc=$?
        fi
    else
        pf "$STATUS_SECONDS" "$@" >"$WORK/archive-status.json" 2>"$WORK/archive-status.err" || arc=$?
    fi
    for name in archive-status.json archive-status.err; do
        cp "$WORK/$name" "$STATE_DIR/$name.tmp" && replace "$STATE_DIR/$name"
    done
    if [ "$arc" -eq 124 ]; then
        dk rm -f "$run_name" >/dev/null 2>>"$WORK/docker.err" || true
        emit FAIL archival_proposal "history-archive status gave no answer within $STATUS_SECONDS s"
        return 0
    fi
    set -- archive "$WORK/archive-status.json" "$arc" --deletion-wait-minutes "$DELETION_WAIT_MINUTES"
    [ -z "$copy_reason" ] || set -- "$@" --copy-reason "$copy_reason"
    # A reconcile after a purge or a deletion that did not end clean is an incident until the operator removes
    # archive.sh's record (OPERATIONS_RUNBOOK §8); meanwhile archive.sh works on no run and no deletion request. The line
    # fails and names it first.
    [ ! -f "$PURGE_INCIDENT" ] || set -- "$@" --purge-reconcile "$PURGE_INCIDENT"
    python3 "$SCRIPT_DIR/monitor_report.py" "$@" >"$WORK/archive.out" 2>"$WORK/archive-monitor.err" || true
    text=$(tr -d '\r' <"$WORK/archive.out" | grep -E '^(PASS|FAIL|SKIP) archival_proposal ' | head -n 1) || text=
    if [ -z "$text" ]; then
        emit FAIL archival_proposal "the history-archive status report could not be evaluated (monitor_report.py)"
        return 0
    fi
    word=${text%% *}
    rest=${text#* }
    emit "$word" archival_proposal "${rest#* }"
}
# lock_age_seconds PATH: seconds since PATH was last modified (nothing when that cannot be read).
lock_age_seconds() {
    modified=$(python3 -c 'import os, sys; print(int(os.stat(sys.argv[1]).st_mtime))' "$1" 2>/dev/null) || return 0
    printf '%s' "$modified" | grep -Eq '^[0-9]+$' || return 0
    echo $(($(date -u +%s) - modified))
}
check_status() {
    guard=
    if [ -d "$RECORDS_DIR/.release.lock" ]; then
        guard=$RECORDS_DIR/.release.lock
    elif [ -f "$BACKUP_DIR/.backup.lock/owner" ] && grep -qx 'by=release.sh' "$BACKUP_DIR/.backup.lock/owner" 2>/dev/null; then
        guard=$BACKUP_DIR/.backup.lock
    fi
    if [ -n "$guard" ]; then
        age=$(lock_age_seconds "$guard")
        if [ -n "$age" ] && [ "$age" -ge $((RELEASE_LOCK_HOURS * 3600)) ]; then
            # A killed release.sh (SIGKILL, out of memory, power loss) leaves its lock: never skip silently forever.
            emit FAIL database "the release lock $guard is older than $RELEASE_LOCK_HOURS h; remove it if no release.sh runs (OPERATIONS_RUNBOOK §3)"
            for id in schema backup_age archival_proposal; do
                emit SKIP "$id" "status did not run (release lock $guard)"
            done
            return 0
        fi
        for id in $STATUS_IDS; do
            emit SKIP "$id" "a release is running ($guard)"
        done
        return 0
    fi
    before=$(one_off status) || before=
    src=0
    # Without --max-backup-age-hours, status takes the limit from the interval of Administration → Backups.
    set -- status
    [ -z "$MAX_AGE" ] || set -- status --max-backup-age-hours "$MAX_AGE"
    pf "$STATUS_SECONDS" --profile ops run --rm --no-deps -T --user "$USER_IDS" "$@" \
        >"$WORK/status.json" 2>"$WORK/status.err" || src=$?
    # This run evaluates its own output; the state copies (for the operator) are replaced whole, never mixed.
    for name in status.json status.err; do
        cp "$WORK/$name" "$STATE_DIR/$name.tmp" && replace "$STATE_DIR/$name"
    done
    if [ "$src" -eq 124 ]; then
        # Remove only the one-off status container this run left (one that was not there before the run).
        remove_new_one_off status "$before"
        emit FAIL database "no answer within $STATUS_SECONDS s"
        for id in schema backup_age archival_proposal; do
            emit SKIP "$id" "status did not run"
        done
        return 0
    fi
    set -- status "$WORK/status.json" "$src"
    [ -z "$FULL" ] || set -- "$@" --growth-file "$STATE_DIR/growth.tsv"
    mrc=0
    python3 "$SCRIPT_DIR/monitor_report.py" "$@" >"$WORK/status.out" 2>"$WORK/monitor.err" || mrc=$?
    tr -d '\r' <"$WORK/status.out" >"$WORK/status.lines"
    if [ "$mrc" -gt 1 ]; then
        emit FAIL database "the status report could not be evaluated (monitor_report.py exit $mrc)"
        return 0
    fi
    while IFS= read -r text; do
        case "$text" in
            NOTE\ *) note "${text#NOTE }" ;;
            # The status report's archival line is replaced by the history-archive status (check_archival).
            *\ archival_proposal\ *) ;;
            PASS\ * | FAIL\ * | SKIP\ *)
                word=${text%% *}
                rest=${text#* }
                emit "$word" "${rest%% *}" "${rest#* }"
                ;;
        esac
    done <"$WORK/status.lines"
    if selected archival_proposal; then check_archival; fi
}

# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------

if selected https; then check_https; fi
if selected certificate; then check_certificate; fi
if any_selected containers restarts errors disk_data; then gather_containers; fi
if selected containers; then check_containers; fi
if selected restarts; then check_restarts; fi
if selected errors; then check_errors; fi
if selected disk_data; then check_disk_data; fi
if selected disk_backup; then host_disk disk_backup "$BACKUP_DIR" "on the backup directory"; fi
if selected disk_docker; then check_disk_docker; fi
if selected disk_archive; then
    if [ -n "$ARCHIVE_PROBLEM" ]; then
        emit FAIL disk_archive "$ARCHIVE_PROBLEM (DEPLOYMENT §3.1)"
    elif [ -n "$ARCHIVE_DIR" ]; then
        host_disk disk_archive "$ARCHIVE_DIR" "on the archive folder"
    else
        emit SKIP disk_archive "$ARCHIVE_NOT_SET_UP"
    fi
fi
# shellcheck disable=SC2086
if any_selected $STATUS_IDS; then check_status; fi

FAILSET=$(sed -n 's/^FAIL \([a-z_]*\) .*/\1/p' "$LINES" | sort -u | tr '\n' ' ' | sed 's/ *$//')
EXIT=0
[ -z "$FAILSET" ] || EXIT=1
if [ -n "$FULL" ]; then
    if [ -n "$RESTARTS_READY" ]; then
        cp "$WORK/restarts.new" "$STATE_DIR/restarts.tmp" && replace "$STATE_DIR/restarts"
    fi
    if [ -n "$NEW_CURSOR" ]; then
        echo "$NEW_CURSOR" >"$STATE_DIR/errors-since.tmp" && replace "$STATE_DIR/errors-since"
    fi
    now=$(date -u +%s)
    if [ -z "$FAILSET" ]; then
        : >"$STATE_DIR/alert-state.tmp" && replace "$STATE_DIR/alert-state"
    else
        stored=$(sed -n 's/^failset=//p' "$STATE_DIR/alert-state" 2>/dev/null | head -n 1) || stored=
        stored_at=$(sed -n 's/^notified_at=//p' "$STATE_DIR/alert-state" 2>/dev/null | head -n 1) || stored_at=
        printf '%s' "$stored_at" | grep -Eq '^[0-9]+$' || stored_at=
        if [ "$RENOTIFY_HOURS" -gt 0 ] && [ "$stored" = "$FAILSET" ] && [ -n "$stored_at" ] \
            && [ $((now - stored_at)) -lt $((RENOTIFY_HOURS * 3600)) ]; then
            EXIT=0
            note "already reported at $(utc_of "$stored_at"); next reminder after $(utc_of $((stored_at + RENOTIFY_HOURS * 3600)))"
        else
            printf 'failset=%s\nnotified_at=%s\n' "$FAILSET" "$now" >"$STATE_DIR/alert-state.tmp" \
                && replace "$STATE_DIR/alert-state"
        fi
    fi
fi
write_last "$EXIT" "$LINES" "$NOTES"
if [ -z "$QUIET" ] || [ "$EXIT" -ne 0 ]; then
    cat "$LINES" "$NOTES"
fi
exit "$EXIT"
