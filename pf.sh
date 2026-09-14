#!/bin/sh
# PartFlow deployment-admin launcher source.
# Installed copies run in one of two protected locations:
#   <installation-root>/bootstrap/pf  - trusted bootstrap (PF-A1.1): selects the
#                                       registered interpreter and control release
#                                       from bootstrap.conf, then a registered instance.
#   <home>/control/pf.sh              - legacy v2.5 layout: read-only diagnostics only
#                                       until PF-A2 migrates it into a registration.
# The launcher never accepts PF_PYTHON, PF_HOME, PF_REPO_ROOT, PF_CONFIG_DIR,
# PF_CONTROL_DIR, PYTHONPATH or the inherited PATH as privileged execution authority.
set -eu
PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:/var/packages/ContainerManager/target/usr/bin:/var/packages/Docker/target/usr/bin:/var/packages/Git/target/bin"
export PATH

fail() {
    echo "$1" >&2
    exit 2
}

SELF_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
case "$(basename -- "$SELF_DIR")" in
    bootstrap) MODE=bootstrap ;;
    control) MODE=legacy ;;
    *)
        echo "This is the repository source copy of pf.sh and is intentionally not used for NAS operations." >&2
        echo "Install/update the root-owned control plane with:" >&2
        echo "  sudo sh ./deploy/synology/install-control.sh" >&2
        echo "Then run:" >&2
        echo "  sudo pf <command>" >&2
        exit 2
        ;;
esac

if [ "$(id -u)" -ne 0 ]; then
    echo "PartFlow NAS control commands must run as root. Use: sudo pf <command>" >&2
    exit 2
fi

# Only the interpreter's own flags below are trusted; drop every inherited
# loader/interpreter and legacy override variable before exec.
unset PF_PYTHON PF_HOME PF_REPO_ROOT PF_CONFIG_DIR PF_CONTROL_DIR
unset PYTHONPATH PYTHONHOME PYTHONSTARTUP PYTHONSAFEPATH PYTHONUSERBASE LD_PRELOAD LD_LIBRARY_PATH
CHILD_TERM=${TERM:-dumb}

if [ "$MODE" = bootstrap ]; then
    ROOT=${SELF_DIR%/bootstrap}
    CONF="$SELF_DIR/bootstrap.conf"
    [ -f "$CONF" ] && [ ! -L "$CONF" ] && [ -O "$CONF" ] || fail "Missing or untrusted bootstrap configuration: $CONF"
    INTERPRETER=
    RELEASE=
    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in
            ''|'#'*) continue ;;
            *=*) ;;
            *) fail "Invalid bootstrap.conf line (expected key=value)." ;;
        esac
        key=${line%%=*}
        value=${line#*=}
        case "$value" in
            /*) ;;
            *) fail "bootstrap.conf: $key must be an absolute path." ;;
        esac
        case "$value" in
            *[!A-Za-z0-9._/-]*|*/../*|*/..|*//*|*/) fail "bootstrap.conf: $key is not a normalized path." ;;
        esac
        case "$key" in
            interpreter) [ -z "$INTERPRETER" ] || fail "bootstrap.conf: duplicate interpreter."; INTERPRETER=$value ;;
            control_release) [ -z "$RELEASE" ] || fail "bootstrap.conf: duplicate control_release."; RELEASE=$value ;;
            *) fail "bootstrap.conf: unknown key '$key'." ;;
        esac
    done < "$CONF"
    [ -n "$INTERPRETER" ] && [ -n "$RELEASE" ] || fail "bootstrap.conf must set interpreter and control_release."
    case "$RELEASE" in
        "$ROOT"/releases/*) ;;
        *) fail "bootstrap.conf: control_release must live under $ROOT/releases." ;;
    esac
    [ -f "$INTERPRETER" ] && [ -x "$INTERPRETER" ] && [ -O "$INTERPRETER" ] || fail "Registered interpreter is missing, not executable or not root-owned: $INTERPRETER"
    ADMIN="$RELEASE/pf-admin.py"
    [ -f "$ADMIN" ] && [ ! -L "$ADMIN" ] && [ -O "$ADMIN" ] || fail "Installed control release is missing or untrusted: $ADMIN"
    # Isolated mode (-I) ignores PYTHON* variables, user site and the script
    # directory; -B writes no bytecode into the protected release. The child
    # environment is rebuilt from an allowlist; nothing inherited reaches it.
    exec env -i PATH="$PATH" HOME=/root LANG=C.UTF-8 LC_ALL=C.UTF-8 TERM="$CHILD_TERM" \
        "$INTERPRETER" -I -B "$ADMIN" --installation-root "$ROOT" "$@"
fi

# Legacy v2.5 control directory: read-only diagnostics only (no registration).
ADMIN="$SELF_DIR/pf-admin.py"
if [ ! -f "$ADMIN" ]; then
    echo "Missing installed Synology admin controller: $ADMIN" >&2
    exit 2
fi

PYTHON=
for candidate in \
    python3 \
    python3.14 \
    python3.13 \
    python3.12 \
    python3.11 \
    python3.10 \
    python3.9 \
    /var/packages/python314/target/bin/python3.14 \
    /var/packages/python313/target/bin/python3.13 \
    /var/packages/python312/target/bin/python3.12 \
    /var/packages/python311/target/bin/python3.11 \
    /var/packages/python310/target/bin/python3.10 \
    /var/packages/Python3.9/target/usr/bin/python3.9
do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null; then
        PYTHON=$candidate
        break
    fi
done

if [ -z "$PYTHON" ] || ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "Python 3.9+ is required on the fixed PATH; register the interpreter through the protected bootstrap." >&2
    exit 2
fi

exec env -i PATH="$PATH" HOME=/root LANG=C.UTF-8 LC_ALL=C.UTF-8 TERM="$CHILD_TERM" \
    "$PYTHON" -I -B "$ADMIN" "$@"
