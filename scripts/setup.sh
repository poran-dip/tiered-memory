#!/usr/bin/env bash
# Fetch gem5 at a pinned commit, build it and its m5 library, build the workloads, then fetch the
# full-system kernel and disk image and add the workloads to a copy of the disk.
#
#   scripts/setup.sh                 # everything
#   scripts/setup.sh fetch           # only clone/check out gem5
#   scripts/setup.sh fetch m5 wl     # any subset of: fetch gem5 m5 wl resources disk
#
# resources: downloads the pinned Ubuntu 24.04 kernel + disk (configs/fetch_resources.py) and
#            writes their paths to third_party/resources.json.
# disk:      rebuilds third_party/disks/x86-ubuntu-24.04-tm.img with the m5-fs workloads
#            (scripts/make_disk.sh, needs sudo). Rerun after changing workloads/ or tiered_memory/.
#
# Overrides: GEM5_REPO, GEM5_REF (commit to pin), GEM5_DIR, JOBS.
# gem5 is not committed to this repo; GEM5_REF is the reproducibility pin, so bump it
# deliberately and note the change in the commit message.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GEM5_REPO="${GEM5_REPO:-https://github.com/poran-dip/gem5.git}"
GEM5_REF="${GEM5_REF:-618cf87d11c91cb2b4ca763b7a647b8e3cefe9fe}"
GEM5_DIR="${GEM5_DIR:-$REPO_ROOT/third_party/gem5}"
JOBS="${JOBS:-$(nproc)}"

log() { printf '\n==> %s\n' "$*"; }

fetch_gem5() {
    if [ -d "$GEM5_DIR/.git" ]; then
        local have
        have="$(git -C "$GEM5_DIR" rev-parse HEAD)"
        if [ "$have" = "$GEM5_REF" ]; then
            log "gem5 already at $GEM5_REF"
            return
        fi
        log "gem5 at $have, checking out pinned $GEM5_REF"
        if ! git -C "$GEM5_DIR" diff --quiet || ! git -C "$GEM5_DIR" diff --cached --quiet; then
            echo "error: $GEM5_DIR has local changes; commit or stash them first" >&2
            exit 1
        fi
    else
        log "Cloning gem5 into $GEM5_DIR"
        mkdir -p "$GEM5_DIR"
        git -C "$GEM5_DIR" init -q
        git -C "$GEM5_DIR" remote add origin "$GEM5_REPO"
    fi
    git -C "$GEM5_DIR" fetch --depth 1 origin "$GEM5_REF"
    git -C "$GEM5_DIR" checkout -q FETCH_HEAD
}

build_gem5() {
    log "Building gem5 (build/X86/gem5.opt, $JOBS jobs; this takes a while)"
    (cd "$GEM5_DIR" && scons build/X86/gem5.opt -j"$JOBS")
}

build_m5() {
    log "Building the m5 utility library"
    (cd "$GEM5_DIR/util/m5" && scons build/x86/out/m5)
}

build_workloads() {
    log "Building workloads (gem5 SE, gem5 full-system and native)"
    make -C "$REPO_ROOT/workloads" GEM5="$GEM5_DIR" m5 m5-fs native
}

fetch_resources() {
    log "Fetching the full-system kernel and disk image from gem5-resources"
    "$GEM5_DIR/build/X86/gem5.opt" -d "$REPO_ROOT/third_party/m5out-resources" \
        "$REPO_ROOT/configs/fetch_resources.py"
}

build_disk() {
    make -C "$REPO_ROOT/workloads" GEM5="$GEM5_DIR" m5-fs
    "$REPO_ROOT/scripts/make_disk.sh"
}

steps=("$@")
[ ${#steps[@]} -eq 0 ] && steps=(fetch gem5 m5 wl resources disk)
for s in "${steps[@]}"; do
    case "$s" in
        fetch) fetch_gem5 ;;
        gem5) build_gem5 ;;
        m5) build_m5 ;;
        wl) build_workloads ;;
        resources) fetch_resources ;;
        disk) build_disk ;;
        *)
            echo "unknown step: $s (expected fetch, gem5, m5, wl, resources or disk)" >&2
            exit 2
            ;;
    esac
done
log "Done"
