#!/bin/sh
# Installed under <home>/control. Intended for a root-owned DSM scheduled task.
set -eu
CONTROL_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ "$(id -u)" -ne 0 ]; then
    echo "Run this scheduled backup as root." >&2
    exit 2
fi
exec "$CONTROL_DIR/pf.sh" backup "$@"
