#!/bin/sh
# Install the repository's reviewed NAS admin source as a root-owned control plane.
set -eu

if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root: sudo sh ./deploy/synology/install-control.sh" >&2
    exit 2
fi
if [ ! -t 0 ]; then
    echo "Interactive terminal required; installation has no --yes bypass." >&2
    exit 2
fi

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)
PF_HOME=$(CDPATH= cd -- "$REPO_ROOT/.." && pwd)
CONTROL="$PF_HOME/control"
CONFIG="$PF_HOME/config"
GROUP=users

if ! grep -q "^${GROUP}:" /etc/group 2>/dev/null; then
    echo "Required DSM group '$GROUP' was not found." >&2
    exit 2
fi

for path in \
    "$REPO_ROOT/pf.sh" \
    "$REPO_ROOT/compose.nas.yaml" \
    "$SCRIPT_DIR/pf-admin.py" \
    "$SCRIPT_DIR/backup.sh" \
    "$SCRIPT_DIR/release-check.sh" \
    "$SCRIPT_DIR/pf-config.example.json" \
    "$SCRIPT_DIR/nas.env.example"
do
    if [ ! -f "$path" ]; then
        echo "Missing install source: $path" >&2
        exit 2
    fi
done

cat <<SUMMARY
PartFlow NAS control-plane installation
  Repository (users writable): $REPO_ROOT
  Runtime control (users read): $CONTROL
  Runtime config (users write): $CONFIG
  Backup/recovery read group:   $GROUP

The repository copies are source/reference files only after installation.
Operational commands will run the root-owned copy under control/.
SUMMARY
printf "Type exactly 'INSTALL CONTROL': "
IFS= read -r answer
if [ "$answer" != "INSTALL CONTROL" ]; then
    echo "Cancelled; nothing was changed." >&2
    exit 1
fi

# Resolve legacy runtime configuration before replacing the control plane.
mkdir -p "$CONFIG"
chown root:"$GROUP" "$CONFIG"
chmod 2770 "$CONFIG"

if [ -f "$REPO_ROOT/.env" ]; then
    if [ -f "$CONFIG/.env" ]; then
        if ! cmp -s "$REPO_ROOT/.env" "$CONFIG/.env"; then
            echo "Both repo/.env and config/.env exist and differ. Reconcile them before installation." >&2
            exit 1
        fi
        rm -f "$REPO_ROOT/.env"
    else
        mv "$REPO_ROOT/.env" "$CONFIG/.env"
    fi
fi

LEGACY_CONFIG="$SCRIPT_DIR/pf-config.json"
if [ -f "$LEGACY_CONFIG" ] && [ -f "$CONFIG/pf-config.json" ]; then
    if ! cmp -s "$LEGACY_CONFIG" "$CONFIG/pf-config.json"; then
        echo "Both legacy deploy/synology/pf-config.json and config/pf-config.json exist and differ." >&2
        echo "Reconcile them before installation; neither file was replaced." >&2
        exit 1
    fi
elif [ -f "$LEGACY_CONFIG" ]; then
    cp -p "$LEGACY_CONFIG" "$CONFIG/pf-config.json"
elif [ ! -f "$CONFIG/pf-config.json" ]; then
    cp "$SCRIPT_DIR/pf-config.example.json" "$CONFIG/pf-config.json"
fi

for path in "$CONFIG/pf-config.json" "$CONFIG/.env"; do
    if [ -f "$path" ]; then
        chown root:"$GROUP" "$path"
        chmod 0660 "$path"
    fi
done
# The repo-local runtime config was a v2.4 compatibility location only.
# Remove it after migration so there is one authoritative host configuration.
if [ -f "$LEGACY_CONFIG" ]; then
    rm -f "$LEGACY_CONFIG"
fi

TEMP="$PF_HOME/.control-install-$$"
trap 'rm -rf "$TEMP"' EXIT HUP INT TERM
rm -rf "$TEMP"
mkdir -p "$TEMP"
cp "$REPO_ROOT/pf.sh" "$TEMP/pf.sh"
cp "$SCRIPT_DIR/pf-admin.py" "$TEMP/pf-admin.py"
cp "$REPO_ROOT/compose.nas.yaml" "$TEMP/compose.nas.yaml"
cp "$SCRIPT_DIR/backup.sh" "$TEMP/backup.sh"
cp "$SCRIPT_DIR/release-check.sh" "$TEMP/release-check.sh"
cp "$SCRIPT_DIR/pf-config.example.json" "$TEMP/pf-config.example.json"
cp "$SCRIPT_DIR/nas.env.example" "$TEMP/nas.env.example"

chown -R root:"$GROUP" "$TEMP"
find "$TEMP" -type d -exec chmod 0750 {} \;
find "$TEMP" -type f -exec chmod 0640 {} \;
# Root may execute operational shell entry points; the users group can only read them.
chmod 0740 "$TEMP/pf.sh" "$TEMP/backup.sh" "$TEMP/release-check.sh"

if [ -d "$CONTROL" ]; then
    UPGRADE_DIR="$PF_HOME/recovery/control-upgrades"
    mkdir -p "$UPGRADE_DIR"
    chown root:"$GROUP" "$PF_HOME/recovery" "$UPGRADE_DIR" 2>/dev/null || true
    chmod 0750 "$PF_HOME/recovery" "$UPGRADE_DIR" 2>/dev/null || true
    stamp=$(date -u +%Y%m%dT%H%M%SZ)
    tar -czf "$UPGRADE_DIR/control-$stamp.tar.gz" -C "$PF_HOME" control
    chown root:"$GROUP" "$UPGRADE_DIR/control-$stamp.tar.gz" 2>/dev/null || true
    chmod 0640 "$UPGRADE_DIR/control-$stamp.tar.gz" 2>/dev/null || true
    rm -rf "$CONTROL"
fi
mv "$TEMP" "$CONTROL"
trap - EXIT HUP INT TERM

# Install a tiny root-owned launcher. It contains no lifecycle logic.
mkdir -p /usr/local/bin
LAUNCHER=/usr/local/bin/pf
if [ -e "$LAUNCHER" ] && ! grep -q '^# PartFlow NAS installed launcher$' "$LAUNCHER" 2>/dev/null; then
    echo "WARNING: $LAUNCHER already exists and is not managed by PartFlow; it was not overwritten." >&2
else
    cat > "$LAUNCHER" <<LAUNCHER_EOF
#!/bin/sh
# PartFlow NAS installed launcher
exec "$CONTROL/pf.sh" "\$@"
LAUNCHER_EOF
    chown root:root "$LAUNCHER"
    chmod 0700 "$LAUNCHER"
fi

# Normalize the writable repo/config and read-only backup/recovery policies.
PF_HOME="$PF_HOME" PF_REPO_ROOT="$REPO_ROOT" PF_CONFIG_DIR="$CONFIG" PF_CONTROL_DIR="$CONTROL" \
    "$CONTROL/pf.sh" permissions

cat <<DONE
Control-plane installation complete.

Use:
  sudo pf doctor
  sudo pf status
  sudo pf update --latest

Runtime .env is now:
  $CONFIG/.env
The writable repository .env is no longer used by the controller.
DONE
