#!/bin/sh
# PartFlow deployment-admin launcher source.
# Installed copies run in one of two protected locations:
#   <installation-root>/bootstrap/pf  - trusted bootstrap (PF-A1.1): parses bootstrap.conf as
#                                       data, then runs the installed verifier
#                                       bootstrap/pf_bootstrap.py, which checks the interpreter,
#                                       every ancestor and the pinned control release BEFORE
#                                       any release code executes.
#   <home>/control/pf.sh              - legacy v2.5 layout: this script prints a read-only
#                                       unregistered report itself and executes no Python
#                                       payload from the unverified legacy control directory.
# The launcher never accepts PF_PYTHON, PF_HOME, PF_REPO_ROOT, PF_CONFIG_DIR,
# PF_CONTROL_DIR, PYTHONPATH or the inherited PATH as privileged execution authority.
# Trust assumption (design r3): the administrator installed bootstrap/ from reviewed bytes
# and enters through the host's privilege entry (sudo). Nothing here proves its own bytes.
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

# Drop every inherited loader/interpreter and legacy override variable before exec.
unset PF_PYTHON PF_HOME PF_REPO_ROOT PF_CONFIG_DIR PF_CONTROL_DIR
unset PYTHONPATH PYTHONHOME PYTHONSTARTUP PYTHONSAFEPATH PYTHONUSERBASE LD_PRELOAD LD_LIBRARY_PATH
CHILD_TERM=${TERM:-dumb}

if [ "$MODE" = bootstrap ]; then
    ROOT=${SELF_DIR%/bootstrap}
    CONF="$SELF_DIR/bootstrap.conf"
    VERIFIER="$SELF_DIR/pf_bootstrap.py"
    [ -f "$CONF" ] && [ ! -L "$CONF" ] && [ -O "$CONF" ] || fail "Missing or untrusted bootstrap configuration: $CONF"
    [ -f "$VERIFIER" ] && [ ! -L "$VERIFIER" ] && [ -O "$VERIFIER" ] || fail "Missing or untrusted bootstrap verifier: $VERIFIER"
    INTERPRETER=
    RELEASE=
    RELEASE_SHA=
    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in
            ''|'#'*) continue ;;
            *=*) ;;
            *) fail "Invalid bootstrap.conf line (expected key=value)." ;;
        esac
        key=${line%%=*}
        value=${line#*=}
        case "$key" in
            interpreter|control_release)
                case "$value" in
                    /*) ;;
                    *) fail "bootstrap.conf: $key must be an absolute path." ;;
                esac
                case "$value" in
                    *[!A-Za-z0-9._/-]*|*/../*|*/..|*//*|*/) fail "bootstrap.conf: $key is not a normalized path." ;;
                esac
                ;;
            control_release_sha256)
                case "$value" in
                    *[!0-9a-f]*|'') fail "bootstrap.conf: control_release_sha256 must be lowercase hex." ;;
                esac
                [ "${#value}" -eq 64 ] || fail "bootstrap.conf: control_release_sha256 must be 64 hex characters."
                ;;
            *) fail "bootstrap.conf: unknown key '$key'." ;;
        esac
        case "$key" in
            interpreter) [ -z "$INTERPRETER" ] || fail "bootstrap.conf: duplicate interpreter."; INTERPRETER=$value ;;
            control_release) [ -z "$RELEASE" ] || fail "bootstrap.conf: duplicate control_release."; RELEASE=$value ;;
            control_release_sha256) [ -z "$RELEASE_SHA" ] || fail "bootstrap.conf: duplicate control_release_sha256."; RELEASE_SHA=$value ;;
        esac
    done < "$CONF"
    [ -n "$INTERPRETER" ] && [ -n "$RELEASE" ] && [ -n "$RELEASE_SHA" ] || fail "bootstrap.conf must set interpreter, control_release and control_release_sha256."
    case "$RELEASE" in
        "$ROOT"/releases/*) ;;
        *) fail "bootstrap.conf: control_release must live under $ROOT/releases." ;;
    esac
    [ -f "$INTERPRETER" ] && [ -x "$INTERPRETER" ] && [ -O "$INTERPRETER" ] || fail "Registered interpreter is missing, not executable or not root-owned: $INTERPRETER"
    # The installation root is chosen here, from this launcher's own location, and handed
    # to the verifier as its first argument. It is not an operator option: any operator
    # spelling of it (before or after the command) refuses the whole invocation.
    for arg in "$@"; do
        case "$arg" in
            --installation-root|--installation-root=*)
                fail "--installation-root is set by the installed bootstrap, not by the operator; refusing. Nothing was read and nothing was changed." ;;
        esac
    done
    # The verifier (installed in bootstrap/, not in the release) re-checks the
    # interpreter, configuration, ancestors and the pinned release tree with
    # no-follow/owner/mode/link/ACL rules, then execs <release>/pf-admin.py.
    # Isolated mode (-I) ignores PYTHON* variables, user site and the script
    # directory; -B writes no bytecode. The child environment is rebuilt from
    # an allowlist; nothing inherited reaches it.
    exec env -i PATH="$PATH" HOME=/root LANG=C.UTF-8 LC_ALL=C.UTF-8 TERM="$CHILD_TERM" \
        "$INTERPRETER" -I -B "$VERIFIER" --installation-root "$ROOT" "$@"
fi

# Legacy v2.5 control directory: read-only report produced by this shell only.
# No interpreter is selected, no file from the unverified legacy control
# directory is executed, and no file content is read or printed: the journal is
# private state, so only its existence and location are reported. Registration/
# migration is the PF-A2 installer's job.
HOME_DIR=${SELF_DIR%/control}
COMMAND=${1:-}
echo "PartFlow NAS Admin (PF-A1.1 checkpoint) - legacy launcher"
echo "UNREGISTERED legacy installation (v2.5 layout) at $HOME_DIR"
echo "  control: $SELF_DIR (not executed: unverified legacy payload)"
if [ -f "$HOME_DIR/config/pf-config.json" ]; then
    echo "  config/pf-config.json: present (not parsed here; editable, not authoritative)"
else
    echo "  config/pf-config.json: missing"
fi
if [ -f "$HOME_DIR/config/.env" ]; then
    echo "  config/.env: present (contents not read)"
else
    echo "  config/.env: missing"
fi
FOUND=0
for state_dir in "$HOME_DIR"/.pf-state-*; do
    [ -d "$state_dir" ] || continue
    if [ -e "$state_dir/pending.json" ]; then
        FOUND=1
        echo "  INCOMPLETE OPERATION recorded in $state_dir/pending.json (private journal; contents not read or shown here)"
        echo "    Do not delete or edit it. It is interpreted only by a protected registration after the PF-A2 migration."
    fi
done
[ "$FOUND" -eq 1 ] || echo "  Pending journal: none found under $HOME_DIR/.pf-state-*"
echo "Mutating commands require a protected registration (PF-A2 legacy migration). No files were changed."
case "$COMMAND" in
    ''|-h|--help|status|doctor|instances) exit 0 ;;
    *)
        echo "ERROR: '$COMMAND' is refused on an unregistered installation; read-only diagnostics only." >&2
        exit 1
        ;;
esac
