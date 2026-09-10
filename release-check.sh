#!/bin/sh
# Check-only unless both --apply and auto_update=true are explicitly configured.
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec sh "$SCRIPT_DIR/pf.sh" release-check "$@"
