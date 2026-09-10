#!/bin/sh
# Check-only unless both --apply and auto_update=true are explicitly configured.
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)
exec sh "$REPO_ROOT/pf.sh" release-check "$@"
