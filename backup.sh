#!/bin/sh
# Scheduled backup: no prompts, no update, and no database reset.
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ "$(id -u)" -eq 0 ]; then
    exec sh "$SCRIPT_DIR/pf.sh" backup "$@"
fi
exec sudo sh "$SCRIPT_DIR/pf.sh" backup "$@"
