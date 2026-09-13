#!/bin/sh
# PartFlow NAS control-plane launcher source.
# Operational use must run the installed root-owned copy under <home>/control.
set -eu
PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:/var/packages/ContainerManager/target/usr/bin:/var/packages/Docker/target/usr/bin:/var/packages/Git/target/bin:${PATH:-}"
export PATH

CONTROL_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ "$(basename -- "$CONTROL_DIR")" != "control" ]; then
    echo "This is the repository source copy of pf.sh and is intentionally not used for NAS operations." >&2
    echo "Install/update the root-owned control plane with:" >&2
    echo "  sudo sh ./deploy/synology/install-control.sh" >&2
    echo "Then run:" >&2
    echo "  sudo pf <command>" >&2
    exit 2
fi

if [ "$(id -u)" -ne 0 ]; then
    echo "PartFlow NAS control commands must run as root. Use: sudo pf <command>" >&2
    exit 2
fi

PF_HOME=${PF_HOME:-$(CDPATH= cd -- "$CONTROL_DIR/.." && pwd)}
PF_REPO_ROOT=${PF_REPO_ROOT:-$PF_HOME/repo}
PF_CONFIG_DIR=${PF_CONFIG_DIR:-$PF_HOME/config}
PF_CONTROL_DIR=$CONTROL_DIR
ADMIN="$CONTROL_DIR/pf-admin.py"

if [ ! -f "$ADMIN" ]; then
    echo "Missing installed Synology admin controller: $ADMIN" >&2
    exit 2
fi
if [ ! -d "$PF_REPO_ROOT" ]; then
    echo "Missing PartFlow repository: $PF_REPO_ROOT" >&2
    exit 2
fi

if [ -n "${PF_PYTHON:-}" ]; then
    PYTHON=$PF_PYTHON
else
    PYTHON=
    for candidate in \
        python3 \
        python3.14 \
        python3.13 \
        python3.12 \
        python3.11 \
        python3.10 \
        python3.9 \
        /var/packages/python314/target/bin/python3.14 \
        /var/packages/python313/target/bin/python3.13 \
        /var/packages/python312/target/bin/python3.12 \
        /var/packages/python311/target/bin/python3.11 \
        /var/packages/python310/target/bin/python3.10 \
        /var/packages/Python3.9/target/usr/bin/python3.9
    do
        if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null; then
            PYTHON=$candidate
            break
        fi
    done
fi

if [ -z "$PYTHON" ] || ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "Python 3.9+ is required. Set PF_PYTHON to its absolute executable path." >&2
    exit 2
fi

export PF_HOME PF_REPO_ROOT PF_CONFIG_DIR PF_CONTROL_DIR
exec "$PYTHON" "$ADMIN" "$@"
