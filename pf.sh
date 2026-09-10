#!/bin/sh
# The entry point and controller are local tools, never updated by GitHub checkout.
set -eu
PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:/var/packages/ContainerManager/target/usr/bin:/var/packages/Docker/target/usr/bin:/var/packages/Git/target/bin:${PATH:-}"
export PATH
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ -n "${PF_PYTHON:-}" ]; then
    PYTHON=$PF_PYTHON
else
    PYTHON=
    for candidate in python3 python3.13 python3.12 python3.11 python3.10 python3.9 /var/packages/Python3.9/target/usr/bin/python3.9; do
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
exec "$PYTHON" "$SCRIPT_DIR/pf-admin.py" "$@"
