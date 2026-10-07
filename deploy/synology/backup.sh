#!/bin/sh
# Scheduled backup wrapper for a root-owned DSM Task Scheduler task:
#   <root>/bootstrap/backup.sh --instance <slug|uuid>
# Placed in bootstrap/ by install-control.sh init (PF-A2.1), next to the installed launcher
# <root>/bootstrap/pf; from a legacy
# v2.5 <home>/control/ directory it reaches the read-only legacy launcher, which refuses backup.
# A scheduled task must name its instance. In this checkpoint (PF-A1.4) the controller refuses the
# command without a terminal (policy-grant-required, exit 20) until a PF-A4.3 protected policy grant
# permits unattended backups; run sudo pf --instance <slug> backup interactively meanwhile.
# The only program started is the sibling launcher, with a fixed PATH and argument vector.
set -eu
PATH=/usr/bin:/bin:/usr/sbin:/sbin
export PATH
unset PF_PYTHON PF_HOME PF_REPO_ROOT PF_CONFIG_DIR PF_CONTROL_DIR
unset PYTHONPATH PYTHONHOME PYTHONSTARTUP LD_PRELOAD LD_LIBRARY_PATH DOCKER_HOST DOCKER_CONTEXT DOCKER_CONFIG

usage() {
    echo "Usage: backup.sh --instance <slug|uuid>. A scheduled task must name its instance; nothing was run." >&2
    exit 2
}

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this scheduled backup as root." >&2
    exit 2
fi
[ "$#" -eq 2 ] && [ "$1" = --instance ] || usage
case "$2" in
    ''|-*|*[!a-z0-9_-]*) usage ;;
esac
INSTANCE=$2

SELF_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
case "$(basename -- "$SELF_DIR")" in
    bootstrap) exec "$SELF_DIR/pf" --instance "$INSTANCE" backup ;;
    control) exec "$SELF_DIR/pf.sh" --instance "$INSTANCE" backup ;;
    *)
        echo "This wrapper runs only from an installed bootstrap/ or legacy control/ directory." >&2
        exit 2
        ;;
esac
