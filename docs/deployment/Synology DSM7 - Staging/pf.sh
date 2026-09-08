#!/bin/sh
# Keep the project name, working directory, and standalone Compose file fixed.
set -eu
PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
export PATH
cd "$(dirname "$0")"

if [ ! -f compose.nas.yaml ] || [ ! -f .env ]; then
    echo "Missing compose.nas.yaml or .env beside pf.sh." >&2
    exit 1
fi

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    exec docker compose -p partflow-staging -f compose.nas.yaml "$@"
fi

if command -v docker-compose >/dev/null 2>&1 && docker-compose version >/dev/null 2>&1; then
    exec docker-compose -p partflow-staging -f compose.nas.yaml "$@"
fi

echo "No working Compose CLI found. Check the supported Synology package and Docker/Compose versions." >&2
exit 1
