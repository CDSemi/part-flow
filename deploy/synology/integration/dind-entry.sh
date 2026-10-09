#!/bin/sh
# PF-A3.4 isolated environment, inside the privileged docker:28.5.1-dind container (SPEC section 2.2).
#
#   /pfa34/work/dind-entry.sh            provision (packages, capacity devices, groups), then supervise dockerd
#                                        (started once by the host's allowlisted `docker exec -d`)
#   /pfa34/work/dind-entry.sh teardown   stop every inner container and dockerd, clear the immutable flags the
#                                        run set, unmount both trees and detach our loop devices (SPEC 2.3 step 1)
#
# State: /run/pfa34/state holds one word (provisioning, supervising, blocked:<reason>, failed:<reason>);
# /run/pfa34/dockerd.pid the current daemon; /run/pfa34/loops our loop devices; environment facts go to
# /pfa34/evidence/env/. The harness restarts the daemon by TERM to dockerd.pid (fault I6); this loop starts it again.
set -u
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH
STATE=/run/pfa34
ENV_DIR=/pfa34/evidence/env
DISK=/pfa34/disk
DOCKER_IMG_GIB=64
DATA_IMG_GIB=48
NEEDED_FREE_GIB=120
PACKAGES="python3 git iproute2 acl e2fsprogs e2fsprogs-extra util-linux procps coreutils findutils postgresql16-client tzdata"

mkdir -p "$STATE" "$ENV_DIR"

say() {
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*" >> "$ENV_DIR/dind-entry.log"
}

set_state() {
    echo "$1" > "$STATE/state.tmp" && mv "$STATE/state.tmp" "$STATE/state"
    say "state $1"
}

teardown() {
    : > "$STATE/stop"
    report="$ENV_DIR/teardown.txt"
    : > "$report"
    if [ -S /var/run/docker.sock ] && docker info > /dev/null 2>&1; then
        ids=$(docker ps -q)
        if [ -n "$ids" ]; then
            # shellcheck disable=SC2086 # one ID per word
            docker stop -t 10 $ids >> "$report" 2>&1
        fi
        echo "inner containers stopped: $(echo "$ids" | grep -c .)" >> "$report"
    fi
    if [ -f "$STATE/dockerd.pid" ]; then
        pid=$(cat "$STATE/dockerd.pid")
        kill -TERM "$pid" 2> /dev/null
        waited=0
        while kill -0 "$pid" 2> /dev/null && [ "$waited" -lt 120 ]; do
            sleep 1
            waited=$((waited + 1))
        done
        if kill -0 "$pid" 2> /dev/null; then
            kill -KILL "$pid" 2> /dev/null
            echo "dockerd needed SIGKILL after 120 s" >> "$report"
        fi
        echo "dockerd stopped" >> "$report"
    fi
    if [ -f "$STATE/immutable.list" ]; then
        while IFS= read -r path; do
            [ -n "$path" ] && chattr -i "$path" >> "$report" 2>&1 && echo "chattr -i $path" >> "$report"
        done < "$STATE/immutable.list"
    fi
    for tree in /var/lib/docker /srv/pfa34; do
        if mountpoint -q "$tree"; then
            umount -R "$tree" >> "$report" 2>&1 || umount -l "$tree" >> "$report" 2>&1
            echo "unmounted $tree" >> "$report"
        fi
    done
    if [ -f "$STATE/loops" ]; then
        while IFS= read -r device; do
            [ -n "$device" ] && losetup -d "$device" >> "$report" 2>&1 && echo "detached $device" >> "$report"
        done < "$STATE/loops"
    fi
    # Loop devices are VM-global and another environment's images carry the same path inside its own container:
    # ours are identified by the backing file's device and inode ("[dev]:inode" in losetup -a), never by path.
    leaked=0
    for image in "$DISK/docker.img" "$DISK/data.img"; do
        [ -e "$image" ] || continue
        key="[$(stat -c %d "$image")]:$(stat -c %i "$image") "
        if losetup -a 2> /dev/null | grep -F "$key" >> "$report"; then
            leaked=1
        fi
    done
    if [ "$leaked" -eq 1 ]; then
        echo "LEAK: loop devices of this environment still attached (lines above)" >> "$report"
        cat "$report"
        exit 3
    fi
    echo "loop devices: none of ours attached" >> "$report"
    cat "$report"
    exit 0
}

if [ "${1:-}" = teardown ]; then
    teardown
fi

set_state provisioning
if ! apk add --no-cache $PACKAGES >> "$ENV_DIR/apk.log" 2>&1; then
    set_state "failed:apk-add"
    exit 1
fi
apk info -v 2> /dev/null | sort > "$ENV_DIR/apk-versions.txt"

free_kib=$(df -Pk /pfa34 | awk 'NR == 2 { print $4 }')
echo "$free_kib" > "$ENV_DIR/pfa34-free-kib.txt"
if [ "$free_kib" -lt $((NEEDED_FREE_GIB * 1024 * 1024)) ]; then
    set_state "blocked:insufficient Docker Desktop VM space"
    exit 2
fi

mkdir -p "$DISK" /srv/pfa34
: > "$STATE/loops"
loop_mode=loop
for spec in "docker.img:$DOCKER_IMG_GIB:/var/lib/docker:" "data.img:$DATA_IMG_GIB:/srv/pfa34:acl"; do
    name=${spec%%:*}
    rest=${spec#*:}
    size=${rest%%:*}
    rest=${rest#*:}
    target=${rest%%:*}
    options=${rest#*:}
    image="$DISK/$name"
    if [ ! -e "$image" ]; then
        truncate -s "${size}G" "$image" && mkfs.ext4 -q -F -m 0 "$image" >> "$ENV_DIR/mkfs.log" 2>&1
    fi
    device=$(losetup -f --show "$image" 2>> "$ENV_DIR/losetup.log")
    if [ -z "$device" ]; then
        loop_mode=volume
        break
    fi
    echo "$device" >> "$STATE/loops"
    if [ -n "$options" ]; then
        mount -o "$options" "$device" "$target"
    else
        mount "$device" "$target"
    fi || { set_state "failed:mount $target"; exit 1; }
    echo "$name $device $target ${size}G" >> "$ENV_DIR/loop-devices.txt"
done
if [ "$loop_mode" = volume ]; then
    # SPEC 2.2 item 5: no loop devices; both trees use the volume directly (A3-T14 storage part, R13/R56 blocked).
    while IFS= read -r device; do
        [ -n "$device" ] && umount "$device" 2> /dev/null
        [ -n "$device" ] && losetup -d "$device" 2> /dev/null
    done < "$STATE/loops"
    : > "$STATE/loops"
    mkdir -p /pfa34/srv
    mount --bind /pfa34/srv /srv/pfa34
fi
echo "$loop_mode" > "$ENV_DIR/capacity-mode.txt"
chmod 0755 /srv /srv/pfa34
chown root:root /srv /srv/pfa34

for group in pfadmin pfread; do
    getent group "$group" > /dev/null 2>&1 || addgroup -S "$group"
done

set_state supervising
while [ ! -e "$STATE/stop" ]; do
    # /run/pfa34/hold (C-A1-13 engine-ID drift): the harness edits the engine ID while no daemon runs.
    while [ -e "$STATE/hold" ] && [ ! -e "$STATE/stop" ]; do
        sleep 1
    done
    /usr/local/bin/dind dockerd --host=unix:///var/run/docker.sock >> "$ENV_DIR/dockerd.log" 2>&1 &
    pid=$!
    echo "$pid" > "$STATE/dockerd.pid"
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) start pid=$pid" >> "$ENV_DIR/dockerd-starts.log"
    wait "$pid"
    status=$?
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) exit pid=$pid status=$status" >> "$ENV_DIR/dockerd-starts.log"
    sleep 1
done
set_state stopped
