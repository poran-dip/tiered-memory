#!/usr/bin/env bash
# Run the committed gem5 full-system runs (findings 002 onward) into results/fs/<run>/.
#
#   scripts/fs_runs.sh                  # every run not yet done, one at a time
#   scripts/fs_runs.sh -j 3             # up to 3 gem5 processes at once (one host core and
#                                       # roughly 1 GB of host RAM each)
#   scripts/fs_runs.sh -j 3 'stream-448'  # only runs whose name matches the regex
#   scripts/fs_runs.sh -n               # list what would run, run nothing
#   scripts/fs_runs.sh --overwrite a-chase-64   # rerun, replacing the earlier outputs
#
# A run whose results/fs/<run>/fs_result.json exists is skipped (so an interrupted batch resumes),
# unless --overwrite. Each run adds its row to results/gem5_fs.csv when it finishes; gem5's own
# terminal output goes to results/fs/<run>/gem5.log (not committed). Rebuild the CSV from the run
# directories at any time with:
#   python3 -m tiered_memory.fsreport --csv results/gem5_fs.csv results/fs/*/
#
# --kvm-no-perf is added automatically when the host has no hardware perf counters (WSL2, most
# VMs). Extra gem5 config options for every run: FS_FLAGS="--roi-cpu o3" scripts/fs_runs.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GEM5="${GEM5:-$REPO_ROOT/third_party/gem5/build/X86/gem5.opt}"
OUT="$REPO_ROOT/results/fs"

# name                config  workload  workload args
RUNS="
a-chase-64            a       chase     -s 64 -i 1
c-chase-64            c       chase     -s 64 -i 1
d-chase-64            d       chase     -s 64 -i 1
a-chase-256           a       chase     -s 256 -i 1
c-chase-256           c       chase     -s 256 -i 1
d-chase-256           d       chase     -s 256 -i 1
a-stream-320          a       stream    -s 320 -i 1
c-stream-320          c       stream    -s 320 -i 1
d-stream-320          d       stream    -s 320 -i 1
a-stream-384          a       stream    -s 384 -i 1
c-stream-384          c       stream    -s 384 -i 1
d-stream-384          d       stream    -s 384 -i 1
a-stream-448          a       stream    -s 448 -i 1
c-stream-448          c       stream    -s 448 -i 1
d-stream-448          d       stream    -s 448 -i 1
a-stream-448-back     a       stream    -s 448 -i 1 -d 1
c-stream-448-back     c       stream    -s 448 -i 1 -d 1
d-stream-448-back     d       stream    -s 448 -i 1 -d 1
"

config_file() {
    case "$1" in
        a) echo configs/a_baseline.py ;;
        b) echo configs/b_proposed.py ;;
        c) echo configs/c_ideal.py ;;
        d) echo configs/d_swap.py ;;
        *) echo "unknown config '$1'" >&2; exit 1 ;;
    esac
}

jobs_max=1
dry=0
overwrite=""
filter="."
while [ $# -gt 0 ]; do
    case "$1" in
        -j) jobs_max="$2"; shift 2 ;;
        -n) dry=1; shift ;;
        --overwrite) overwrite="--overwrite"; shift ;;
        -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
        *) filter="$1"; shift ;;
    esac
done

[ -x "$GEM5" ] || [ "$dry" = 1 ] || { echo "error: $GEM5 not built; run scripts/setup.sh" >&2; exit 1; }

flags="${FS_FLAGS:-}"
if [ ! -e /sys/bus/event_source/devices/cpu ]; then
    flags="--kvm-no-perf $flags"
fi

run_one() {
    local name="$1" cfg="$2" wl="$3"
    shift 3
    local dir="$OUT/$name"
    mkdir -p "$dir"
    echo "[$(date +%H:%M:%S)] start  $name"
    # shellcheck disable=SC2086  # flags is a list of options
    if (cd "$REPO_ROOT" && "$GEM5" -d "$dir" "$(config_file "$cfg")" --workload "$wl" \
        $flags $overwrite -- "$@") >"$dir/gem5.log" 2>&1; then
        echo "[$(date +%H:%M:%S)] done   $name"
    else
        echo "[$(date +%H:%M:%S)] FAILED $name (see $dir/gem5.log)"
    fi
}

cd "$REPO_ROOT"
selected=0
while read -r name cfg wl args; do
    [ -n "$name" ] || continue
    [[ "$name" =~ $filter ]] || continue
    if [ -z "$overwrite" ] && [ -e "$OUT/$name/fs_result.json" ]; then
        echo "skip   $name (done; --overwrite to rerun)"
        continue
    fi
    selected=$((selected + 1))
    if [ "$dry" = 1 ]; then
        echo "would run $name: $(config_file "$cfg") --workload $wl $flags $overwrite -- $args"
        continue
    fi
    while [ "$(jobs -rp | wc -l)" -ge "$jobs_max" ]; do
        wait -n || true
    done
    # shellcheck disable=SC2086  # args is the workload's argument list
    run_one "$name" "$cfg" "$wl" $args &
done <<<"$RUNS"
wait
echo "$selected run(s) selected"
