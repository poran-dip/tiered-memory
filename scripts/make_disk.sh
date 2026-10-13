#!/usr/bin/env bash
# Build the disk image the full-system configs boot: a copy of the stock gem5-resources Ubuntu 24.04
# image with this repo's workloads and guest-side Python added under /opt/tiered-memory.
#
#   scripts/make_disk.sh            # normally run as `scripts/setup.sh disk`
#
# Needs: third_party/resources.json (scripts/setup.sh resources), workloads/build/m5-fs/ built
# (make -C workloads m5-fs), and sudo for losetup/mount. The stock image is never modified.
# Rerun whenever the workloads or tiered_memory/ change; the copy is rebuilt from scratch.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RES_JSON="$REPO_ROOT/third_party/resources.json"
OUT="${DISK_OUT:-$REPO_ROOT/third_party/disks/x86-ubuntu-24.04-tm.img}"
BIN_DIR="$REPO_ROOT/workloads/build/m5-fs"
GUEST_DIR=/opt/tiered-memory

log() { printf '\n==> %s\n' "$*"; }
die() {
    echo "error: $*" >&2
    exit 1
}

[ -f "$RES_JSON" ] || die "$RES_JSON missing; run scripts/setup.sh resources"
read_json() { python3 -c "import json,sys; v=json.load(open(sys.argv[1]))[sys.argv[2]]; print('' if v is None else v)" "$RES_JSON" "$1"; }
BASE="$(read_json base_disk)"
PART="$(read_json root_partition)"
[ -f "$BASE" ] || die "base disk $BASE missing; rerun scripts/setup.sh resources"

names="$(python3 -c "import sys; sys.path.insert(0, sys.argv[1]); from tiered_memory.workloads import NAMES; print(' '.join(NAMES))" "$REPO_ROOT")"
for n in $names; do
    [ -x "$BIN_DIR/$n" ] || die "$BIN_DIR/$n missing; run make -C workloads m5-fs"
    [ "$BIN_DIR/$n" -nt "$REPO_ROOT/workloads/$n.cpp" ] && [ "$BIN_DIR/$n" -nt "$REPO_ROOT/workloads/common.h" ] ||
        die "$BIN_DIR/$n is older than its source; run make -C workloads m5-fs"
done

log "Copying $BASE -> $OUT"
mkdir -p "$(dirname "$OUT")"
cp --sparse=always "$BASE" "$OUT.tmp"

MNT="$(mktemp -d)"
LOOP=""
cleanup() {
    if mountpoint -q "$MNT"; then sudo umount "$MNT"; fi
    if [ -n "$LOOP" ]; then sudo losetup -d "$LOOP"; fi
    rmdir "$MNT" 2>/dev/null || true
    rm -f "$OUT.tmp"
}
trap cleanup EXIT

log "Finding the root filesystem (sudo)"
LOOP="$(sudo losetup --find --show --partscan "$OUT.tmp")"
# gem5-resources metadata can name the wrong partition (x86-ubuntu-24.04-img 4.0.0 says 1, but
# partition 1 is a 1 MiB BIOS-boot partition and the root ext4 filesystem is 2). Detect it
# instead: the first ext4 partition. The metadata value is only a hint used for ordering.
sleep 1 # partition device nodes can take a moment to appear
ROOT_PART=""
for p in $PART $(seq 1 16); do
    DEV="${LOOP}p${p}"
    [ -b "$DEV" ] || continue
    if [ "$(sudo blkid -o value -s TYPE "$DEV" 2>/dev/null)" = ext4 ]; then
        ROOT_PART="$p"
        break
    fi
done
if [ -z "$ROOT_PART" ]; then
    # no partition table: the image is the filesystem itself
    [ "$(sudo blkid -o value -s TYPE "$LOOP" 2>/dev/null)" = ext4 ] ||
        die "no ext4 root filesystem found in $BASE (sudo fdisk -l and blkid on it to inspect)"
    DEV="$LOOP"
else
    DEV="${LOOP}p${ROOT_PART}"
fi
[ "$ROOT_PART" = "$PART" ] || echo "note: root filesystem is on partition '${ROOT_PART:-none}', not '$PART' as gem5-resources says; recording that for the boot"
sudo mount "$DEV" "$MNT"

log "Installing workloads and tiered_memory/ into $GUEST_DIR"
sudo rm -rf "$MNT$GUEST_DIR"
sudo mkdir -p "$MNT$GUEST_DIR/bin" "$MNT$GUEST_DIR/tiered_memory"
for n in $names; do sudo install -m 755 "$BIN_DIR/$n" "$MNT$GUEST_DIR/bin/$n"; done
sudo cp "$REPO_ROOT"/tiered_memory/*.py "$MNT$GUEST_DIR/tiered_memory/"
sudo chmod -R a+rX "$MNT$GUEST_DIR"
sudo sh -c "cd '$REPO_ROOT' && git rev-parse HEAD 2>/dev/null > '$MNT$GUEST_DIR/REPO_COMMIT'" || true
ls -l "$MNT$GUEST_DIR/bin"

sudo umount "$MNT"
sudo losetup -d "$LOOP"
LOOP=""
mv "$OUT.tmp" "$OUT"
# The configs boot with root=/dev/sda<this>; record what was actually found.
python3 - "$RES_JSON" "$ROOT_PART" <<'PY'
import json, sys
path, part = sys.argv[1], sys.argv[2] or None
info = json.load(open(path))
info["disk_root_partition"] = part
with open(path, "w") as fh:
    fh.write(json.dumps(info, indent=1) + "\n")
PY
log "Disk image ready: $OUT (root filesystem on partition ${ROOT_PART:-none})"
