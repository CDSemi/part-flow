#!/bin/sh
# Deployment Admin repository installer (PF-A2.1): initializes a NEW protected installation root only.
#
#   sudo sh ./deploy/synology/install-control.sh init [--root <root>] [--interpreter <python3>]
#        [--tool <id>=<path>]... [--launcher-path <path> | --no-launcher]
#
# Trust boundary: the administrator chooses and runs these reviewed repository bytes. This wrapper only
# selects a root-owned system interpreter and execs the repository's pf_install.py in isolated mode with
# an environment rebuilt from scratch; pf_install.py reads the candidate control files as data, shows every
# conflict, asks for a typed confirmation and builds the root in a locked private sibling directory before
# one atomic rename. Every other verb (register, migrate-legacy, control, resume, status) runs from the
# installed, verified control: sudo <root>/bootstrap/pf install <verb>.
set -eu
PATH=/usr/bin:/bin:/usr/sbin:/sbin
export PATH
unset PF_PYTHON PF_HOME PF_REPO_ROOT PF_CONFIG_DIR PF_CONTROL_DIR
unset PYTHONPATH PYTHONHOME PYTHONSTARTUP PYTHONSAFEPATH PYTHONUSERBASE LD_PRELOAD LD_LIBRARY_PATH

if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root: sudo sh ./deploy/synology/install-control.sh init --root <root>" >&2
    exit 2
fi
if [ ! -t 0 ]; then
    echo "Interactive terminal required; installation has no --yes bypass." >&2
    exit 2
fi
if [ "$#" -lt 1 ]; then
    echo "Usage: sudo sh ./deploy/synology/install-control.sh init [--root <root>] [--interpreter <python3>]" \
        "[--tool <id>=<path>]... [--launcher-path <path> | --no-launcher]" >&2
    exit 2
fi
if [ "$1" != init ]; then
    echo "installer-verb-installed-only: install-control.sh only initializes a new installation root." \
        "Run '$1' from the installed control: sudo <root>/bootstrap/pf install $1 …. Nothing was read or changed." >&2
    exit 2
fi

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
PY=
for candidate in /usr/bin/python3 /usr/local/bin/python3 /var/packages/Python3.9/target/usr/bin/python3; do
    if [ -f "$candidate" ] && [ -x "$candidate" ] && [ -O "$candidate" ]; then
        PY=$candidate
        break
    fi
done
if [ -z "$PY" ]; then
    echo "No root-owned python3 found in /usr/bin, /usr/local/bin or the DSM Python3.9 package; nothing was changed." >&2
    exit 2
fi
exec env -i PATH="$PATH" HOME=/root LANG=C.UTF-8 LC_ALL=C.UTF-8 TERM="${TERM:-dumb}" "$PY" -I -B "$SCRIPT_DIR/pf_install.py" "$@"
