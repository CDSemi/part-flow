#!/bin/sh
# Scheduled release-check wrapper for a root-owned DSM Task Scheduler task:
#   <root>/bootstrap/release-check.sh --instance <slug|uuid> [--channel stable|prerelease] [--apply]
# The PF-A2 installer places it next to the installed launcher <root>/bootstrap/pf; from a legacy
# v2.5 <home>/control/ directory it reaches the read-only legacy launcher, which refuses the command.
# A scheduled task must name its instance. In this checkpoint (PF-A1.4) the controller refuses the
# command without a terminal until a PF-A4.3 protected policy grant exists: a check with
# policy-grant-required, --apply with auto-apply-not-permitted (both exit 20). Automatic apply is off;
# the editable auto_update setting is a proposal only.
# The only program started is the sibling launcher, with a fixed PATH and argument vector.
set -eu
PATH=/usr/bin:/bin:/usr/sbin:/sbin
export PATH
unset PF_PYTHON PF_HOME PF_REPO_ROOT PF_CONFIG_DIR PF_CONTROL_DIR
unset PYTHONPATH PYTHONHOME PYTHONSTARTUP LD_PRELOAD LD_LIBRARY_PATH DOCKER_HOST DOCKER_CONTEXT DOCKER_CONFIG

usage() {
    echo "Usage: release-check.sh --instance <slug|uuid> [--channel stable|prerelease] [--apply]." \
        "A scheduled task must name its instance; nothing was run." >&2
    exit 2
}

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this release check as root." >&2
    exit 2
fi
[ "$#" -ge 2 ] && [ "$1" = --instance ] || usage
case "$2" in
    ''|-*|*[!a-z0-9_-]*) usage ;;
esac
INSTANCE=$2
shift 2
CHANNEL=
APPLY=
while [ "$#" -gt 0 ]; do
    case "$1" in
        --channel)
            [ -z "$CHANNEL" ] && [ "$#" -ge 2 ] || usage
            case "$2" in
                stable|prerelease) CHANNEL=$2 ;;
                *) usage ;;
            esac
            shift 2
            ;;
        --apply)
            [ -z "$APPLY" ] || usage
            APPLY=1
            shift
            ;;
        *) usage ;;
    esac
done
set -- --instance "$INSTANCE" release-check
[ -z "$CHANNEL" ] || set -- "$@" --channel "$CHANNEL"
[ -z "$APPLY" ] || set -- "$@" --apply

SELF_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
case "$(basename -- "$SELF_DIR")" in
    bootstrap) exec "$SELF_DIR/pf" "$@" ;;
    control) exec "$SELF_DIR/pf.sh" "$@" ;;
    *)
        echo "This wrapper runs only from an installed bootstrap/ or legacy control/ directory." >&2
        exit 2
        ;;
esac
