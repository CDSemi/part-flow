#!/bin/sh
# Scheduled backup: no prompts, no update, and no database reset.
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)
if [ "$(id -u)" -eq 0 ]; then
    exec sh "$REPO_ROOT/pf.sh" backup "$@"
fi
exec sudo sh "$REPO_ROOT/pf.sh" backup "$@"
