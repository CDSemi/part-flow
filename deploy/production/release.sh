#!/bin/sh
# PartFlow release (P16-S3; DEPLOYMENT §3.1 and §7, OPERATIONS_RUNBOOK §5). Run from the repository root of the
# release checkout, with the git tag of the release checked out; `deploy/production/release.sh --help` prints the usage.
#
# It automates the release sequence with `PF="docker compose -f compose.production.yaml --env-file <env>"`:
# preflight; the current revision and pre-release reconcile with the RUNNING release; the candidate build (the only
# use of compose.production.build.yaml); the candidate's check (j) and revision; the write freeze (`$PF stop backend`)
# when a migration is pending; `migrate` (one transaction); the post-release reconcile; then the switch in two steps:
# `backend` first while `web` still serves the previous bundle (every loaded page sends the previous release, so the
# release gate refuses its writes with 409), health, then `web` (this reopens writes), then smoke.sh. A failed check
# after the switch re-freezes (`$PF stop backend`). Nothing is ever removed, pruned or re-tagged: rollback path 1
# needs the previous release's images on this host. Nothing schedules this script (no auto-updater).
#
# Exit status (record.json "outcome"): 0 completed; 1 stopped_unchanged / aborted_reopened (nothing changed, or writes
# reopened on the current release); 2 could_not_run; 3 stopped_frozen (backend left stopped: follow
# OPERATIONS_RUNBOOK §6); 4 started_needs_review (a check after the switch failed and the re-freeze failed too).
# Ctrl-C/TERM records "interrupted" with the last step and never starts or stops a service.
# Every step's output and record.json are written to <records-dir>/<UTC>-<tag>/ (mode 0700).
set -eu

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
RELEASE_PATTERN='^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'
PRODUCTION_TAG_PATTERN='^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-(alpha|beta|rc)\.[1-9][0-9]*)?$'
HEALTH_ATTEMPTS=90
HEALTH_INTERVAL=2
# The release in the shell would override the env file for every command: the script sets it per command only.
unset PARTFLOW_RELEASE PARTFLOW_COMMIT PARTFLOW_ACCEPT_SCHEMA_REVISION COMPOSE_PROJECT_NAME COMPOSE_FILE COMPOSE_PROFILES

usage() {
    cat <<'EOF'
Usage: deploy/production/release.sh --release TAG --operator NAME --approver NAME
           (--pre-release-backup REF | --no-backup-reason TEXT)
           [--accept-pre-release-findings | --skip-pre-reconcile REASON] [--env-file .env.production]
           [--records-dir DIR] [--environment NAME] [--url URL]
           [--rollback-deadline TEXT] [--observation-owner TEXT] [--known-limitations TEXT]
           [--rehearsal --project NAME]

Run from the repository root of the release checkout (git tag TAG checked out, clean build inputs).
  --pre-release-backup REF       the pre-release dump taken before this run (OPERATIONS_RUNBOOK §5, Before maintenance)
  --no-backup-reason TEXT        why there is no pre-release dump (recorded)
  --accept-pre-release-findings  the pre-release reconcile has findings: continue, and block only on findings that
                                 are absent from it (reconcile_regression.py; needs python3)
  --skip-pre-reconcile REASON    no image of a release has the database revision as its head (rollback path 2
                                 state): skip the pre-release reconcile (recorded); every post-release finding blocks
  --records-dir DIR              deployment records (default $HOME/partflow-deployments)
  --rehearsal --project NAME     rehearsal on a throwaway Compose project (never partflow-production); the git checks
                                 are skipped and recorded
Text values: 1-500 characters, no control characters, no " or \.
Exit: 0 completed, 1 stopped (unchanged or reopened), 2 could not run, 3 frozen (OPERATIONS_RUNBOOK §6),
      4 the new release may be running and writable after a failed check.
EOF
}

usage_error() {
    echo "release: $1 Nothing was changed." >&2
    usage >&2
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
# Arguments (refused before any action)
# ---------------------------------------------------------------------------

TAG=
OPERATOR=
APPROVER=
BACKUP_REF=
NO_BACKUP_REASON=
ACCEPT_FINDINGS=
SKIP_REASON=
ENV_FILE=.env.production
RECORDS_DIR=
ENVIRONMENT=
URL=
ROLLBACK_DEADLINE=
OBSERVATION_OWNER=
KNOWN_LIMITATIONS=
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
        --accept-pre-release-findings) ACCEPT_FINDINGS=1; shift; continue ;;
        --rehearsal) REHEARSAL=1; shift; continue ;;
        --release | --operator | --approver | --pre-release-backup | --no-backup-reason | --skip-pre-reconcile | \
            --env-file | --records-dir | --environment | --url | --rollback-deadline | --observation-owner | \
            --known-limitations | --project)
            [ "$#" -ge 2 ] || usage_error "$option needs a value."
            value=$2
            shift 2
            ;;
        *) usage_error "unknown argument $option." ;;
    esac
    case "$option" in
        --release) TAG=$value ;;
        --operator) check_text "$option" "$value"; OPERATOR=$value ;;
        --approver) check_text "$option" "$value"; APPROVER=$value ;;
        --pre-release-backup) check_text "$option" "$value"; BACKUP_REF=$value ;;
        --no-backup-reason) check_text "$option" "$value"; NO_BACKUP_REASON=$value ;;
        --skip-pre-reconcile) check_text "$option" "$value"; SKIP_REASON=$value ;;
        --env-file) ENV_FILE=$value ;;
        --records-dir) RECORDS_DIR=$value ;;
        --environment) check_text "$option" "$value"; ENVIRONMENT=$value ;;
        --url) check_text "$option" "$value"; URL=$value ;;
        --rollback-deadline) check_text "$option" "$value"; ROLLBACK_DEADLINE=$value ;;
        --observation-owner) check_text "$option" "$value"; OBSERVATION_OWNER=$value ;;
        --known-limitations) check_text "$option" "$value"; KNOWN_LIMITATIONS=$value ;;
        --project) PROJECT=$value ;;
    esac
done

[ -n "$TAG" ] || usage_error "--release is required."
[ -n "$OPERATOR" ] || usage_error "--operator is required."
[ -n "$APPROVER" ] || usage_error "--approver is required."
if [ -n "$BACKUP_REF" ] && [ -n "$NO_BACKUP_REASON" ]; then
    usage_error "give either --pre-release-backup or --no-backup-reason, not both."
fi
[ -n "$BACKUP_REF$NO_BACKUP_REASON" ] || usage_error "--pre-release-backup REF or --no-backup-reason TEXT is required."
if [ -n "$ACCEPT_FINDINGS" ] && [ -n "$SKIP_REASON" ]; then
    usage_error "--accept-pre-release-findings and --skip-pre-reconcile exclude each other."
fi
printf '%s' "$TAG" | grep -Eq "$RELEASE_PATTERN" \
    || usage_error "--release must be a release tag of at most 64 letters, digits, '.', '_' or '-'."
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
if [ -z "$RECORDS_DIR" ]; then
    [ -n "${HOME:-}" ] || usage_error "HOME is not set: give --records-dir DIR."
    RECORDS_DIR=$HOME/partflow-deployments
fi
BACKUP_OPTION=--pre-release-backup
BACKUP_VALUE=$BACKUP_REF
if [ -n "$NO_BACKUP_REASON" ]; then
    BACKUP_OPTION=--no-backup-reason
    BACKUP_VALUE=$NO_BACKUP_REASON
fi

could_not_run() {
    echo "release: $1 Nothing was changed." >&2
    exit 2
}

for tool in docker git curl; do
    command -v "$tool" >/dev/null 2>&1 || could_not_run "$tool is not installed."
done
if [ -n "$ACCEPT_FINDINGS" ]; then
    command -v python3 >/dev/null 2>&1 || could_not_run "python3 is needed for --accept-pre-release-findings."
fi
[ -f compose.production.yaml ] && [ -f compose.production.build.yaml ] \
    || could_not_run "Run it from the repository root of the release checkout (compose.production.yaml not found)."
[ -f "$ENV_FILE" ] || could_not_run "The env file $ENV_FILE does not exist."
CURRENT=$(sed -n 's/^PARTFLOW_RELEASE=//p' "$ENV_FILE" | tail -n 1)
PORT=$(sed -n 's/^PARTFLOW_HTTP_PORT=//p' "$ENV_FILE" | tail -n 1)
printf '%s' "$CURRENT" | grep -Eq "$RELEASE_PATTERN" \
    || could_not_run "PARTFLOW_RELEASE in $ENV_FILE must name the running release (unquoted)."
printf '%s' "$PORT" | grep -Eq '^[0-9]{1,5}$' || could_not_run "PARTFLOW_HTTP_PORT in $ENV_FILE is not an unquoted port."

if [ "$TAG" = "$CURRENT" ]; then
    echo "release: $TAG is already the running release in $ENV_FILE. Nothing was changed." >&2
    exit 1
fi
if [ -z "$REHEARSAL" ] && ! printf '%s' "$TAG" | grep -Eq "$PRODUCTION_TAG_PATTERN"; then
    echo "release: $TAG is not a release tag vMAJOR.MINOR.PATCH[-alpha|beta|rc.N] (DEPLOYMENT §10.2)." \
        "Nothing was changed." >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Records and the lock
# ---------------------------------------------------------------------------

umask 077
mkdir -p "$RECORDS_DIR" 2>/dev/null || could_not_run "The records directory $RECORDS_DIR cannot be created."
LOCK=$RECORDS_DIR/.release.lock
if ! mkdir "$LOCK" 2>/dev/null; then
    echo "release: another release holds the lock $LOCK (or an earlier run ended without removing it: check" \
        "that no release.sh runs, then remove the directory). Nothing was changed." >&2
    exit 1
fi
LOCK_HELD=1
utc() {
    date -u +%Y-%m-%dT%H:%M:%SZ
}
STARTED_AT=$(utc)
RECORD_DIR=$RECORDS_DIR/$(date -u +%Y%m%dT%H%M%SZ)-$TAG
if ! mkdir "$RECORD_DIR" 2>/dev/null; then
    rmdir "$LOCK"
    could_not_run "The record directory $RECORD_DIR cannot be created."
fi
chmod 700 "$RECORD_DIR"
: >"$RECORD_DIR/steps.part"

# State written into record.json.
OUTCOME=
FINALIZED=
STEP_NAME=
STEP_STARTED=
HEAD_COMMIT=
GIT_TAG_CHECKED=false
IMAGE_BACKEND=
IMAGE_WEB=
REVISION_BEFORE=
REVISION_AFTER=
EXPECTED_REVISION=
MIGRATE_RESULT=
MIGRATE_REACHED=
PRE_RC=
J_RC=
POST_RC=
BASELINE_IMAGE=
BASELINE_ACCEPTED=
REGRESSION_RC=
WRITES_REOPENED_AT=
REFROZEN=false
SMOKE_RC=
FROZEN=
SWITCHED=
PENDING=

if [ -n "$PROJECT" ]; then
    PF_TEXT="docker compose -p $PROJECT -f compose.production.yaml --env-file $ENV_FILE"
else
    PF_TEXT="docker compose -f compose.production.yaml --env-file $ENV_FILE"
fi

# pf: the running release (tag from the env file). pf_candidate: the candidate tag in the shell for one command.
pf() {
    if [ -n "$PROJECT" ]; then
        docker compose -p "$PROJECT" -f compose.production.yaml --env-file "$ENV_FILE" "$@"
    else
        docker compose -f compose.production.yaml --env-file "$ENV_FILE" "$@"
    fi
}
pf_candidate() {
    if [ -n "$PROJECT" ]; then
        env PARTFLOW_RELEASE="$TAG" docker compose -p "$PROJECT" -f compose.production.yaml --env-file "$ENV_FILE" "$@"
    else
        env PARTFLOW_RELEASE="$TAG" docker compose -f compose.production.yaml --env-file "$ENV_FILE" "$@"
    fi
}

js() {
    if [ -n "$1" ]; then printf '"%s"' "$1"; else printf 'null'; fi
}
file_rc() {
    if [ -n "$2" ]; then printf '{"file": "%s", "exit_code": %s}' "$1" "$2"; else printf 'null'; fi
}
# json_field FILE KEY: a top-level string value of an indented app.cli report (empty for null or absent).
json_field() {
    sed -n 's/^  "'"$2"'": "\([^"]*\)",\{0,1\}$/\1/p' "$1" 2>/dev/null | head -n 1
}
# json_object FILE KEY: a top-level object of an indented app.cli report, joined on one line.
json_object() {
    awk -v key="$2" '
        !on && index($0, "  \"" key "\": ") == 1 {
            value = substr($0, length(key) + 7)
            if (value !~ /\{$/) { sub(/,$/, "", value); print value; exit }
            on = 1; text = value; next
        }
        on {
            line = $0; sub(/^ +/, "", line)
            if ($0 ~ /^  \}/) { sub(/,$/, "", line); print text line; exit }
            text = text line
        }
    ' "$1" 2>/dev/null
}
# report_valid FILE RC: the report holds exactly the line of its exit code (OPERATIONS_RUNBOOK §7 rule).
report_valid() {
    grep -qx "  \"exit_code\": $2," "$1" 2>/dev/null
}

step_begin() {
    STEP_NAME=$1
    STEP_OUTPUT=${2:-}
    STEP_STARTED=$(utc)
    echo "release: [$STEP_NAME] $3"
}
step_end() {
    printf '    {"name": "%s", "exit_code": %s, "started_at": "%s", "finished_at": "%s", "output": %s}\n' \
        "$STEP_NAME" "$1" "$STEP_STARTED" "$(utc)" "$(js "$STEP_OUTPUT")" >>"$RECORD_DIR/steps.part"
}

write_record() {
    if [ -n "$BACKUP_REF" ]; then
        backup="{\"kind\": \"reference\", \"reference\": \"$BACKUP_REF\", \"verified\": false}"
    else
        backup="{\"kind\": \"none\", \"reason\": \"$NO_BACKUP_REASON\", \"verified\": false}"
    fi
    if [ -n "$MIGRATE_REACHED" ]; then
        reached=$(json_object "$RECORD_DIR/migrate.json" backup)
        [ -z "$reached" ] || backup=$reached
        migration="{\"result\": $(js "$MIGRATE_RESULT"), \"file\": \"migrate.json\", \"log\": \"migrate.log\"}"
    else
        migration='{"result": null, "file": null, "log": null}'
    fi
    host=$(uname -n 2>/dev/null | tr -cd 'A-Za-z0-9._-') || host=
    after=$REVISION_AFTER
    [ -n "$after" ] || after=$REVISION_BEFORE
    # A migrate with an unknown outcome (or no valid report) leaves the database revision unknown: never "before".
    if [ -n "$MIGRATE_REACHED" ]; then
        case "$MIGRATE_RESULT" in
            '' | outcome_unknown) after= ;;
        esac
    fi
    {
        printf '{\n'
        printf '  "record_version": 1,\n'
        printf '  "environment": %s,\n' "$(js "$ENVIRONMENT")"
        printf '  "url": %s,\n' "$(js "$URL")"
        printf '  "host": %s,\n' "$(js "$host")"
        printf '  "release": {"tag": "%s", "commit": %s, "previous_tag": "%s", "git_tag_checked": %s,' \
            "$TAG" "$(js "$HEAD_COMMIT")" "$CURRENT" "$GIT_TAG_CHECKED"
        printf ' "images": {"backend": %s, "web": %s}},\n' "$(js "$IMAGE_BACKEND")" "$(js "$IMAGE_WEB")"
        printf '  "alembic": {"before": %s, "after": %s, "expected": %s},\n' \
            "$(js "$REVISION_BEFORE")" "$(js "$after")" "$(js "$EXPECTED_REVISION")"
        printf '  "operator": "%s",\n' "$OPERATOR"
        printf '  "approver": "%s",\n' "$APPROVER"
        printf '  "started_at": "%s",\n' "$STARTED_AT"
        printf '  "finished_at": "%s",\n' "$(utc)"
        printf '  "backup": %s,\n' "$backup"
        printf '  "migration": %s,\n' "$migration"
        printf '  "steps": [\n'
        sed '$!s/$/,/' "$RECORD_DIR/steps.part"
        printf '  ],\n'
        printf '  "reconcile": {"pre": %s, "candidate_j": %s, "post": %s, "baseline_image": %s, "skip_reason": %s,' \
            "$(file_rc pre-reconcile.json "$PRE_RC")" "$(file_rc candidate-j.json "$J_RC")" \
            "$(file_rc post-reconcile.json "$POST_RC")" "$(js "$BASELINE_IMAGE")" "$(js "$SKIP_REASON")"
        printf ' "regression": %s},\n' "$(file_rc regression.txt "$REGRESSION_RC")"
        printf '  "writes_reopened_at": %s,\n' "$(js "$WRITES_REOPENED_AT")"
        printf '  "refrozen": %s,\n' "$REFROZEN"
        printf '  "smoke": %s,\n' "$(file_rc smoke.txt "$SMOKE_RC")"
        printf '  "rollback_deadline": %s,\n' "$(js "$ROLLBACK_DEADLINE")"
        printf '  "observation_owner": %s,\n' "$(js "$OBSERVATION_OWNER")"
        printf '  "known_limitations": %s,\n' "$(js "$KNOWN_LIMITATIONS")"
        if [ -n "$REHEARSAL" ]; then
            printf '  "rehearsal": true,\n  "project": "%s",\n' "$PROJECT"
        else
            printf '  "rehearsal": false,\n'
        fi
        printf '  "outcome": "%s"\n' "$OUTCOME"
        printf '}\n'
    } >"$RECORD_DIR/record.json.tmp"
    mv "$RECORD_DIR/record.json.tmp" "$RECORD_DIR/record.json"
}

finish() {
    OUTCOME=$1
    FINALIZED=1
    write_record
    rm -f "$RECORD_DIR/steps.part"
    if [ -n "${LOCK_HELD:-}" ]; then
        rmdir "$LOCK" 2>/dev/null || true
        LOCK_HELD=
    fi
    echo "release: $OUTCOME (exit $2); record: $RECORD_DIR/record.json"
    exit "$2"
}

on_signal() {
    trap - INT TERM
    # The trap can run while a step's output is redirected to its file (migrate.json, switch-backend.log, ...):
    # report on the script's own stdout and stderr, never into the step's file.
    exec 1>&8 2>&9
    echo "release: interrupted during step ${STEP_NAME:-preflight}; no service was started or stopped by the" \
        "interruption." >&2
    if [ -n "$MIGRATE_REACHED" ] && [ -z "$MIGRATE_RESULT" ]; then
        # The trap runs before migrate.json is read: the migration may or may not have been committed.
        MIGRATE_RESULT=outcome_unknown
        echo "release: the migrate outcome is unknown. Run '$PF_TEXT run --rm --no-deps -T backend python -m app.cli" \
            "revision' before anything else." >&2
    fi
    if [ -n "$SWITCHED" ]; then
        echo "release: $ENV_FILE already names $TAG (the previous file is $RECORD_DIR/env-before.txt)." >&2
    elif [ -n "$FROZEN" ]; then
        echo "release: writes stay FROZEN: backend is stopped and nothing reopens automatically." >&2
    fi
    echo "release: check the state with: $PF_TEXT ps; then OPERATIONS_RUNBOOK §5 (exit $1) and §6." >&2
    [ -z "$STEP_NAME" ] || step_end "$1"
    finish interrupted "$1"
}

on_exit() {
    status=$?
    [ -z "$FINALIZED" ] || return 0
    # An unexpected command failure under `set -e`: record the state as it is.
    echo "release: stopped unexpectedly during step ${STEP_NAME:-preflight} (exit $status)." >&2
    if [ -n "$SWITCHED" ]; then
        finish started_needs_review 4
    elif [ -n "$FROZEN" ]; then
        finish stopped_frozen 3
    else
        finish stopped_unchanged 1
    fi
}
# fds 8 and 9: the script's own stdout and stderr, for on_signal.
exec 8>&1 9>&2
trap 'on_signal 130' INT
trap 'on_signal 143' TERM
trap on_exit EXIT

decision_tree() {
    cat >&2 <<EOF
release: writes stay FROZEN: backend is stopped and nothing reopens automatically.
  Record and step outputs: $RECORD_DIR
  PF="$PF_TEXT"
  Read the failed step's output first. Reopen only after every required check passed (OPERATIONS_RUNBOOK §5 step 10):
    the release named in $ENV_FILE: \$PF up -d backend   (after a switch: \$PF up -d --no-deps backend, then
    \$PF up -d --no-deps web, then deploy/production/smoke.sh)
  Otherwise follow the rollback decision tree (OPERATIONS_RUNBOOK §6): path 1 the previous tag with its images,
  path 2 the previous tag with PARTFLOW_ACCEPT_SCHEMA_REVISION after the compatibility check; never alembic downgrade.
EOF
}

stop_frozen() {
    echo "release: $1" >&2
    decision_tree
    finish stopped_frozen 3
}

stop_unchanged() {
    echo "release: $1 Nothing was changed." >&2
    finish stopped_unchanged 1
}

# refreeze REASON: a check after the switch failed: stop backend again (exit 3), or exit 4 when that fails too.
refreeze() {
    echo "release: $1" >&2
    echo "release: re-freezing: $PF_TEXT stop backend" >&2
    if pf stop backend >>"$RECORD_DIR/refreeze.log" 2>&1; then
        REFROZEN=true
        decision_tree
        finish stopped_frozen 3
    fi
    cat >&2 <<EOF
release: the re-freeze FAILED: the new release $TAG may be running and accepting writes.
  Stop it by hand now: $PF_TEXT stop backend   (output: $RECORD_DIR/refreeze.log)
  Then follow OPERATIONS_RUNBOOK §6.
EOF
    finish started_needs_review 4
}

# run_report STEP FILE CANDIDATE ARGS...: an app.cli command whose JSON goes to FILE (stderr to FILE.log); sets RC.
run_report() {
    report=$RECORD_DIR/$1
    shift
    RC=0
    if [ "$1" = candidate ]; then
        shift
        pf_candidate run --rm --no-deps -T backend python -m app.cli "$@" >"$report" 2>"$report.log" || RC=$?
    else
        shift
        pf run --rm --no-deps -T backend python -m app.cli "$@" >"$report" 2>"$report.log" || RC=$?
    fi
}

# reconcile_baseline WHO: the pre-release reconcile with the current or the candidate image (steps 2 and 5a).
reconcile_baseline() {
    step_begin pre_reconcile pre-reconcile.json "pre-release reconcile with the $1 image"
    BASELINE_IMAGE=$1
    run_report pre-reconcile.json "$1" reconcile --max-findings 10000
    step_end "$RC"
    if ! report_valid "$RECORD_DIR/pre-reconcile.json" "$RC" || [ "$RC" -ge 2 ]; then
        PRE_RC=$RC
        stop_unchanged "the pre-release reconcile could not run (exit $RC; $RECORD_DIR/pre-reconcile.json)."
    fi
    PRE_RC=$RC
    if [ "$RC" -eq 1 ]; then
        if [ -z "$ACCEPT_FINDINGS" ]; then
            stop_unchanged "the pre-release reconcile has findings ($RECORD_DIR/pre-reconcile.json). Resolve them, or rerun with --accept-pre-release-findings after the owner accepted them as open incidents."
        fi
        BASELINE_ACCEPTED=1
        echo "release: pre-existing findings accepted; only findings absent from the pre-release report will block."
    fi
}

# ---------------------------------------------------------------------------
# 0 preflight
# ---------------------------------------------------------------------------

step_begin preflight preflight.log "release $TAG over $CURRENT ($ENVIRONMENT)"
PREFLIGHT=$RECORD_DIR/preflight.log
if ! pf config --quiet >>"$PREFLIGHT" 2>&1; then
    step_end 2
    echo "release: '$PF_TEXT config --quiet' failed ($PREFLIGHT). Nothing was changed." >&2
    finish could_not_run 2
fi
# Every top-level secret file must exist before any `run`: Compose only warns about a missing file and creates an
# empty directory at its path (P16-S4). The names and paths come from the resolved model ("secrets" block).
secret_files=
if model=$(pf config --format json 2>>"$PREFLIGHT"); then
    secret_files=$(printf '%s\n' "$model" | awk '
        /^  "secrets": \{/ { on = 1; next }
        on && /^  \}/ { exit }
        on && /^    "[^"]+": \{/ { name = $0; sub(/^    "/, "", name); sub(/".*$/, "", name); next }
        on && /^      "file": "/ { path = $0; sub(/^      "file": "/, "", path); sub(/",?$/, "", path); print name "=" path }
    ')
fi
if [ -z "$secret_files" ]; then
    step_end 2
    echo "release: '$PF_TEXT config --format json' failed or names no secret file ($PREFLIGHT). Nothing was changed." >&2
    finish could_not_run 2
fi
set -f
saved_ifs=$IFS
IFS='
'
for entry in $secret_files; do
    IFS=$saved_ifs
    secret_name=${entry%%=*}
    secret_path=${entry#*=}
    echo "secret file $secret_name: $secret_path" >>"$PREFLIGHT"
    if ! { [ -f "$secret_path" ] && [ -s "$secret_path" ]; }; then
        set +f
        step_end 1
        stop_unchanged "the secret file $secret_name ($secret_path) is missing, empty or not a regular file. Create it as DEPLOYMENT §3.1 describes; if Compose already created a directory there, remove it first."
    fi
done
IFS=$saved_ifs
set +f
HEAD_COMMIT=$(git rev-parse HEAD 2>>"$PREFLIGHT") || HEAD_COMMIT=
if ! printf '%s' "$HEAD_COMMIT" | grep -Eq '^[0-9a-f]{40}$'; then
    HEAD_COMMIT=
    step_end 2
    echo "release: 'git rev-parse HEAD' did not give the commit of this checkout. Nothing was changed." >&2
    finish could_not_run 2
fi
if [ -n "$REHEARSAL" ]; then
    echo "rehearsal: git checks skipped (clean build inputs, tag $TAG at HEAD)" >>"$PREFLIGHT"
else
    dirty=$(git status --porcelain --untracked-files=all -- backend frontend compose.production.yaml \
        compose.production.build.yaml deploy/production 2>>"$PREFLIGHT") || dirty="(git status failed)"
    if [ -n "$dirty" ]; then
        printf '%s\n' "$dirty" >>"$PREFLIGHT"
        step_end 1
        stop_unchanged "the build inputs differ from the commit (changed or untracked files; $PREFLIGHT)."
    fi
    ignored=$(git ls-files --others --ignored --exclude-standard -- frontend ':!frontend/node_modules' \
        ':!frontend/dist' ':!frontend/coverage' 2>>"$PREFLIGHT") || ignored="(git ls-files failed)"
    if [ -n "$ignored" ]; then
        printf '%s\n' "$ignored" >>"$PREFLIGHT"
        step_end 1
        stop_unchanged "git-ignored files under frontend/ would enter the web build ($PREFLIGHT)."
    fi
    tagged=$(git rev-parse -q --verify "refs/tags/$TAG^{commit}" 2>>"$PREFLIGHT") || tagged=
    if [ -z "$tagged" ]; then
        step_end 1
        stop_unchanged "the git tag $TAG does not exist."
    fi
    if [ "$tagged" != "$HEAD_COMMIT" ]; then
        step_end 1
        stop_unchanged "the git tag $TAG is not the checked-out commit (HEAD $HEAD_COMMIT)."
    fi
    GIT_TAG_CHECKED=true
fi
projects=$(docker ps -a --format '{{.Label "com.docker.compose.project"}}' 2>>"$PREFLIGHT") || {
    step_end 2
    echo "release: 'docker ps -a' failed ($PREFLIGHT). Nothing was changed." >&2
    finish could_not_run 2
}
if printf '%s\n' "$projects" | sort -u | grep -qx partflow-staging; then
    step_end 1
    stop_unchanged "a partflow-staging project exists on this Docker daemon (DEPLOYMENT §6 environment separation)."
fi
for service in backend web; do
    if ! docker image inspect --format '{{.Id}}' "partflow/$service:$CURRENT" >>"$PREFLIGHT" 2>&1; then
        step_end 1
        stop_unchanged "The images of the running release $CURRENT are not on this host, so rollback path 1 would be impossible. Restore them before releasing (OPERATIONS_RUNBOOK §5, Before maintenance)."
    fi
done
step_end 0

# ---------------------------------------------------------------------------
# 1 current revision, 2 pre-release reconcile (current image)
# ---------------------------------------------------------------------------

step_begin current_revision current-revision.json "revision with the running release $CURRENT"
run_report current-revision.json current revision
step_end "$RC"
[ "$RC" -lt 2 ] || stop_unchanged "'app.cli revision' with $CURRENT could not run ($RECORD_DIR/current-revision.json)."
REVISION_BEFORE=$(json_field "$RECORD_DIR/current-revision.json" database_revision)
current_state=$(json_field "$RECORD_DIR/current-revision.json" state)
current_readiness=$(json_field "$RECORD_DIR/current-revision.json" readiness)
if [ "$current_state" = current ]; then
    baseline=current
elif [ "$current_readiness" = accepted ]; then
    baseline=candidate
    echo "release: the database ($REVISION_BEFORE) is newer than $CURRENT, which runs on its override (rollback path 2)."
else
    stop_unchanged "the running release $CURRENT does not match the database (state ${current_state:-unknown}, readiness ${current_readiness:-unknown}) and no override is recorded. Resolve that first (OPERATIONS_RUNBOOK §6)."
fi

if [ -n "$SKIP_REASON" ]; then
    echo "release: pre-release reconcile skipped (--skip-pre-reconcile): every post-release finding will block."
elif [ "$baseline" = current ]; then
    reconcile_baseline current
fi

# ---------------------------------------------------------------------------
# 3 build (the only use of the build file), 4 check (j), 5 candidate revision, 5a candidate baseline
# ---------------------------------------------------------------------------

step_begin build build.log "images partflow/backend:$TAG and partflow/web:$TAG"
BUILD_LOG=$RECORD_DIR/build.log
present=
labels=
for service in backend web; do
    if label=$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' \
        "partflow/$service:$TAG" 2>>"$BUILD_LOG"); then
        present="$present$service "
        labels="$labels$label "
    fi
done
if [ -z "$present" ]; then
    rc=0
    if [ -n "$PROJECT" ]; then
        env PARTFLOW_RELEASE="$TAG" PARTFLOW_COMMIT="$HEAD_COMMIT" docker compose -p "$PROJECT" \
            -f compose.production.yaml --env-file "$ENV_FILE" -f compose.production.build.yaml build backend web \
            >>"$BUILD_LOG" 2>&1 || rc=$?
    else
        env PARTFLOW_RELEASE="$TAG" PARTFLOW_COMMIT="$HEAD_COMMIT" docker compose \
            -f compose.production.yaml --env-file "$ENV_FILE" -f compose.production.build.yaml build backend web \
            >>"$BUILD_LOG" 2>&1 || rc=$?
    fi
    step_end "$rc"
    [ "$rc" -eq 0 ] || stop_unchanged "the build failed ($BUILD_LOG)."
elif [ "$present" = "backend web " ] && [ "$labels" = "$HEAD_COMMIT $HEAD_COMMIT " ]; then
    # The release identity is baked in at build time: a re-tagged image keeps the release it was built as.
    versions=
    for service in backend web; do
        version=$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.version"}}' \
            "partflow/$service:$TAG" 2>>"$BUILD_LOG") || version=
        versions="$versions${version:-?} "
    done
    if [ "$versions" != "$TAG $TAG " ]; then
        step_end 1
        stop_unchanged "images tagged $TAG were built as release(s) ${versions% } (org.opencontainers.image.version), not $TAG: an image carries the release it was built as, so a re-tagged image is never reused."
    fi
    echo "reused: both images exist with org.opencontainers.image.revision $HEAD_COMMIT and version $TAG" >>"$BUILD_LOG"
    step_end 0
    echo "release: both images of $TAG already exist from this commit: reused."
else
    step_end 1
    stop_unchanged "images tagged $TAG already exist ($present) but not both from commit $HEAD_COMMIT: never rebuild an existing tag."
fi
IMAGE_BACKEND=$(docker image inspect --format '{{.Id}}' "partflow/backend:$TAG" 2>>"$BUILD_LOG") || IMAGE_BACKEND=
IMAGE_WEB=$(docker image inspect --format '{{.Id}}' "partflow/web:$TAG" 2>>"$BUILD_LOG") || IMAGE_WEB=

step_begin candidate_identity_check candidate-j.json "reconcile --check j with the candidate image"
run_report candidate-j.json candidate reconcile --check j
step_end "$RC"
J_RC=$RC
[ "$RC" -eq 0 ] || stop_unchanged "check (j) of the candidate image failed (exit $RC; $RECORD_DIR/candidate-j.json)."

step_begin candidate_revision candidate-revision.json "revision with the candidate image"
run_report candidate-revision.json candidate revision
step_end "$RC"
candidate_state=$(json_field "$RECORD_DIR/candidate-revision.json" state)
EXPECTED_REVISION=$(json_field "$RECORD_DIR/candidate-revision.json" expected_revision)
case "$RC:$candidate_state" in
    0:current) PENDING= ;;
    1:upgrade_available) PENDING=1 ;;
    *) stop_unchanged "the candidate cannot migrate this database (state ${candidate_state:-unknown}, exit $RC; $RECORD_DIR/candidate-revision.json)." ;;
esac
candidate_release=$(json_field "$RECORD_DIR/candidate-revision.json" release)
candidate_commit=$(json_field "$RECORD_DIR/candidate-revision.json" commit)
if [ "$candidate_release" != "$TAG" ] || [ "$candidate_commit" != "$HEAD_COMMIT" ]; then
    stop_unchanged "the candidate image reports release ${candidate_release:-unknown} at commit ${candidate_commit:-unknown}, not $TAG at $HEAD_COMMIT ($RECORD_DIR/candidate-revision.json): /api/health would never show $TAG after the switch."
fi

if [ "$baseline" = candidate ] && [ -z "$SKIP_REASON" ]; then
    if [ -n "$PENDING" ]; then
        stop_unchanged "No image of this release has the database revision as its head, so the pre-release reconciliation cannot run. Return to the release that created revision $REVISION_BEFORE first (RUNBOOK §6 path 1), or rerun with --skip-pre-reconcile REASON (recorded; every post-release finding then blocks)."
    fi
    reconcile_baseline candidate
fi

# ---------------------------------------------------------------------------
# 6 freeze (pending only), 7 migrate
# ---------------------------------------------------------------------------

if [ -n "$PENDING" ]; then
    step_begin freeze freeze.log "write freeze: $PF_TEXT stop backend (waits up to 200 s for in-flight requests)"
    FROZEN=1
    rc=0
    pf stop backend >>"$RECORD_DIR/freeze.log" 2>&1 || rc=$?
    if [ "$rc" -ne 0 ]; then
        step_end "$rc"
        stop_frozen "'stop backend' failed ($RECORD_DIR/freeze.log); backend may still be running."
    fi
    running=$(pf ps --status running -q backend 2>>"$RECORD_DIR/freeze.log") || running="(ps failed)"
    if [ -n "$running" ]; then
        step_end 1
        stop_frozen "backend is still running after the stop ($running); migrate was not run."
    fi
    step_end 0
fi

step_begin migrate migrate.json "migrate with the candidate image ($BACKUP_OPTION)"
MIGRATE_REACHED=1
rc=0
pf_candidate --profile ops run --rm -T migrate "$BACKUP_OPTION" "$BACKUP_VALUE" \
    >"$RECORD_DIR/migrate.json" 2>"$RECORD_DIR/migrate.log" || rc=$?
step_end "$rc"
MIGRATE_RESULT=$(json_field "$RECORD_DIR/migrate.json" result)
migrate_error=$(sed -n 's/^    "code": "\([^"]*\)",\{0,1\}$/\1/p' "$RECORD_DIR/migrate.json" 2>/dev/null | head -n 1)
if ! report_valid "$RECORD_DIR/migrate.json" "$rc"; then
    MIGRATE_RESULT=
fi
REVISION_AFTER=$(json_field "$RECORD_DIR/migrate.json" revision_after)
case "$rc:$MIGRATE_RESULT" in
    0:upgraded | 0:already_current)
        echo "release: migrate $MIGRATE_RESULT (${REVISION_BEFORE:-empty database} -> ${REVISION_AFTER:-?})."
        ;;
    1:refused)
        case "$migrate_error" in
            migrate_running | backend_connected)
                if [ -n "$FROZEN" ]; then
                    stop_frozen "migrate refused: $migrate_error. Another migrate may be committing, or something still serves the database: run '$PF_TEXT run --rm --no-deps -T backend python -m app.cli revision' once it has finished, and find the connected backend."
                fi
                stop_unchanged "migrate refused: $migrate_error ($RECORD_DIR/migrate.json)."
                ;;
        esac
        ;;
    *:outcome_unknown | *:)
        if [ -n "$FROZEN" ]; then
            stop_frozen "the migrate outcome is unknown (exit $rc, result ${MIGRATE_RESULT:-unreadable}; $RECORD_DIR/migrate.json, migrate.log). Run 'app.cli revision' before anything else."
        fi
        echo "release: the migrate outcome is unknown (exit $rc, result ${MIGRATE_RESULT:-unreadable}); nothing was pending. Run '$PF_TEXT run --rm --no-deps -T backend python -m app.cli revision' before anything else." >&2
        finish stopped_unchanged 1
        ;;
esac
if [ "$rc:$MIGRATE_RESULT" != 0:upgraded ] && [ "$rc:$MIGRATE_RESULT" != 0:already_current ]; then
    # refused or failed: the database is unchanged.
    if [ -z "$FROZEN" ]; then
        stop_unchanged "migrate ${MIGRATE_RESULT} (${migrate_error:-no code}; $RECORD_DIR/migrate.json)."
    fi
    echo "release: migrate $MIGRATE_RESULT (${migrate_error:-no code}); the database is unchanged: reopening writes on $CURRENT." >&2
    step_begin reopen reopen-revision.json "reopen writes on $CURRENT"
    run_report reopen-revision.json current revision
    readiness=$(json_field "$RECORD_DIR/reopen-revision.json" readiness)
    case "$readiness" in
        current | accepted) ;;
        *)
            step_end "$RC"
            stop_frozen "the running release $CURRENT is not ready on the database (readiness ${readiness:-unknown}); writes stay frozen."
            ;;
    esac
    rc=0
    pf up -d backend >>"$RECORD_DIR/reopen.log" 2>&1 || rc=$?
    step_end "$rc"
    [ "$rc" -eq 0 ] || stop_frozen "'up -d backend' failed ($RECORD_DIR/reopen.log)."
    FROZEN=
    echo "release: writes reopened on $CURRENT; migrate ${MIGRATE_RESULT} ($RECORD_DIR/migrate.json)." >&2
    finish aborted_reopened 1
fi

# ---------------------------------------------------------------------------
# 8 post-release reconcile (candidate image; backend still frozen when a migration ran)
# ---------------------------------------------------------------------------

step_begin post_reconcile post-reconcile.json "post-release reconcile with the candidate image"
run_report post-reconcile.json candidate reconcile --max-findings 10000
step_end "$RC"
POST_RC=$RC
post_ok=
if report_valid "$RECORD_DIR/post-reconcile.json" "$RC"; then
    if [ "$RC" -eq 0 ]; then
        post_ok=1
    elif [ "$RC" -eq 1 ] && [ -n "$BASELINE_ACCEPTED" ]; then
        step_begin regression regression.txt "compare with the pre-release findings (reconcile_regression.py)"
        rc=0
        python3 "$SCRIPT_DIR/reconcile_regression.py" "$RECORD_DIR/pre-reconcile.json" \
            "$RECORD_DIR/post-reconcile.json" >"$RECORD_DIR/regression.txt" 2>&1 || rc=$?
        step_end "$rc"
        REGRESSION_RC=$rc
        if [ "$rc" -eq 0 ]; then
            post_ok=1
            echo "release: post-release findings were all in the pre-release report ($(head -n 1 "$RECORD_DIR/regression.txt"))."
        else
            cat "$RECORD_DIR/regression.txt" >&2
        fi
    fi
fi
if [ -z "$post_ok" ]; then
    detail="the post-release reconcile blocks (exit $RC): $RECORD_DIR/pre-reconcile.json and $RECORD_DIR/post-reconcile.json"
    [ -z "$REGRESSION_RC" ] || detail="$detail, $RECORD_DIR/regression.txt"
    if [ -n "$FROZEN" ]; then
        stop_frozen "$detail."
    fi
    stop_unchanged "$detail (nothing was switched; treat the findings as an incident)."
fi

# ---------------------------------------------------------------------------
# 9 switch backend, 10 health, 11 switch web (the reopen), 12 smoke
# ---------------------------------------------------------------------------

step_begin switch_backend switch-backend.log "PARTFLOW_RELEASE=$TAG in $ENV_FILE; up -d --no-deps backend"
cp "$ENV_FILE" "$RECORD_DIR/env-before.txt"
ENV_TMP=$ENV_FILE.release-tmp
awk -v tag="$TAG" '
    /^PARTFLOW_RELEASE=/ { print "PARTFLOW_RELEASE=" tag; next }
    /^PARTFLOW_ACCEPT_SCHEMA_REVISION=/ { print "PARTFLOW_ACCEPT_SCHEMA_REVISION="; seen = 1; next }
    { print }
    END { if (!seen) print "PARTFLOW_ACCEPT_SCHEMA_REVISION=" }
' "$RECORD_DIR/env-before.txt" >"$ENV_TMP"
mv "$ENV_TMP" "$ENV_FILE"
SWITCHED=1
rc=0
pf up -d --no-deps backend >>"$RECORD_DIR/switch-backend.log" 2>&1 || rc=$?
step_end "$rc"
[ "$rc" -eq 0 ] || refreeze "'up -d --no-deps backend' failed ($RECORD_DIR/switch-backend.log)."
FROZEN=

step_begin health_wait health.txt "GET /api/health until release $TAG and schema current (up to 180 s)"
attempt=0
healthy=
while [ "$attempt" -lt "$HEALTH_ATTEMPTS" ]; do
    attempt=$((attempt + 1))
    body=$(curl -fsS --max-time 10 "http://127.0.0.1:$PORT/api/health" 2>/dev/null) || body=
    printf '%s %s\n' "$(utc)" "$body" >>"$RECORD_DIR/health.txt"
    case "$body" in
        *"\"release\":\"$TAG\""*)
            case "$body" in
                *'"schema":"current"'*) healthy=1; break ;;
            esac
            ;;
    esac
    sleep "$HEALTH_INTERVAL"
done
if [ -z "$healthy" ]; then
    step_end 1
    {
        echo "== $PF_TEXT ps"
        pf ps 2>&1 || true
        echo "== $PF_TEXT logs --tail=100 backend"
        pf logs --tail=100 backend 2>&1 || true
        echo "== backend restart count"
        container=$(pf ps -q backend 2>/dev/null) || container=
        if [ -n "$container" ]; then docker inspect --format '{{.RestartCount}}' $container 2>&1 || true; fi
    } >"$RECORD_DIR/health-diagnostics.log" 2>&1
    cat "$RECORD_DIR/health-diagnostics.log" >&2
    refreeze "/api/health did not report release $TAG with schema current within 180 s ($RECORD_DIR/health.txt)."
fi
step_end 0

step_begin switch_web switch-web.log "up -d --no-deps web (reopens writes)"
rc=0
pf up -d --no-deps web >>"$RECORD_DIR/switch-web.log" 2>&1 || rc=$?
step_end "$rc"
[ "$rc" -eq 0 ] || refreeze "'up -d --no-deps web' failed ($RECORD_DIR/switch-web.log)."
WRITES_REOPENED_AT=$(utc)

step_begin smoke smoke.txt "deploy/production/smoke.sh --release $TAG"
rc=0
if [ -n "$PROJECT" ]; then
    sh "$SCRIPT_DIR/smoke.sh" --release "$TAG" --env-file "$ENV_FILE" --project "$PROJECT" \
        >"$RECORD_DIR/smoke.txt" 2>&1 || rc=$?
else
    sh "$SCRIPT_DIR/smoke.sh" --release "$TAG" --env-file "$ENV_FILE" >"$RECORD_DIR/smoke.txt" 2>&1 || rc=$?
fi
step_end "$rc"
SMOKE_RC=$rc
cat "$RECORD_DIR/smoke.txt"
[ "$rc" -eq 0 ] || refreeze "the smoke check failed ($RECORD_DIR/smoke.txt); writes were open for its duration."

step_begin record record.json "release $TAG completed"
step_end 0
finish completed 0
