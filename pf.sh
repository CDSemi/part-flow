#!/bin/sh
# Root entry point for the local Synology administration controller.
set -eu
PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:/var/packages/ContainerManager/target/usr/bin:/var/packages/Docker/target/usr/bin:/var/packages/Git/target/bin:${PATH:-}"
export PATH
REPO_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ADMIN="$REPO_ROOT/deploy/synology/pf-admin.py"

if [ ! -f "$ADMIN" ]; then
    echo "Missing Synology admin controller: $ADMIN" >&2
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
        if command -v "$candidate" >/dev/null 2>&1 &&
           "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null; then
            PYTHON=$candidate
            break
        fi
    done
fi

if [ -z "$PYTHON" ] || ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "Python 3.9+ is required. Set PF_PYTHON to its absolute executable path." >&2
    exit 2
fi

export PF_REPO_ROOT="$REPO_ROOT"
exec "$PYTHON" "$ADMIN" "$@"
