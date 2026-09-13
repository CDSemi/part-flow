#!/bin/sh
# Installed under <home>/control. Check-only unless --apply and auto_update=true.
set -eu
CONTROL_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ "$(id -u)" -ne 0 ]; then
    echo "Run this release check as root." >&2
    exit 2
fi
exec "$CONTROL_DIR/pf.sh" release-check "$@"
