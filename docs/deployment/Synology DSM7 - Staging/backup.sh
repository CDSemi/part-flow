#!/bin/sh
# Logical backup only. Listing an archive is not a full restore test.
set -eu
umask 077
PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
export PATH
cd "$(dirname "$0")"

pf() {
    if [ "$(id -u)" -eq 0 ]; then
        sh ./pf.sh "$@"
    else
        sudo sh ./pf.sh "$@"
    fi
}

if [ -d .git ] && command -v git >/dev/null 2>&1; then
    source_revision=$(git -c safe.directory="$(pwd)" rev-parse HEAD)
elif [ -s DEPLOYED_SOURCE.txt ]; then
    source_revision=$(cat DEPLOYED_SOURCE.txt)
else
    echo "Record the extracted source commit in DEPLOYED_SOURCE.txt before backing up." >&2
    exit 1
fi

backup_dir="../backups/database"
mkdir -p "$backup_dir"
stamp="$(date -u +%Y%m%dT%H%M%SZ)-$$"
base="$backup_dir/partflow-$stamp"
partial="$base.dump.partial"
trap 'rm -f "$partial"' 0
trap 'exit 1' 1 2 15

pf exec -T db sh -c \
    'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --no-owner --no-privileges' \
    > "$partial"
test -s "$partial"
pf exec -T db pg_restore --list < "$partial" > "$base.list"

{
    printf 'environment=staging\ncreated_at_utc=%s\nsource_revision=%s\n' "$stamp" "$source_revision"
    printf '\npostgresql_version:\n'
    pf exec -T db sh -c \
        'psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SHOW server_version;"'
    printf '\nalembic_revision:\n'
    pf exec -T db sh -c \
        'psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT version_num FROM alembic_version;"'
} > "$base.manifest.txt"

mv "$partial" "$base.dump"
(
    cd "$backup_dir"
    sha256sum "partflow-$stamp.dump" "partflow-$stamp.list" "partflow-$stamp.manifest.txt" \
        > "partflow-$stamp.sha256"
)
printf 'Backup created and archive listing checked: %s.dump\n' "$base"
printf 'Copy all four files off-NAS. A full isolated restore test is still required.\n'
