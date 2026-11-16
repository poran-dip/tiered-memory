#!/usr/bin/env bash
# The v2 dataset (results/fs_hmat), stage by stage in the order of the plan. Each stage is one
# run_all_working_sets.py call, so a stage's runs finish before the next stage starts, which the
# caps need (B over is capped from C, D over from B and C).
#
#   scripts/run_v2.sh -n pilot          # list the pilot's runs (hotcold only), start nothing
#   scripts/run_v2.sh pilot             # hotcold, fits and over, every config and B variant
#   scripts/run_v2.sh all               # every workload
#   scripts/run_v2.sh b-over            # one stage (a-fits a-over c b-fits b-over d-fits d-over)
#   WORKLOADS="chase stream" scripts/run_v2.sh b-fits
#
# JOBS (default 3) is the number of gem5 processes at once. Extra environment: TM_DATASET (default
# fs_hmat). Runs already done are skipped, so rerunning a stage resumes it. Run it from a clean
# checkout of one commit (run_all_working_sets.py refuses a dirty tree; git worktree add ../tm-v2).
set -euo pipefail
cd "$(dirname "$0")/.."

DRY=()
if [ "${1:-}" = "-n" ]; then DRY=(-n); shift; fi
[ $# -eq 1 ] || { sed -n 2,14p "$0"; exit 2; }

DS="${TM_DATASET:-fs_hmat}"
J="${JOBS:-3}"
WL=()
if [ "$1" = pilot ]; then WORKLOADS=hotcold; fi
# shellcheck disable=SC2206  # word splitting of WORKLOADS is the point
[ -z "${WORKLOADS:-}" ] || WL=(--workloads $WORKLOADS)

run() { python3 configs/run_all_working_sets.py --dataset "$DS" -j "$J" "${DRY[@]}" "${WL[@]}" "$@"; }

stage() {
    case "$1" in
        a-fits) run --configs a --tiers fits ;;
        a-over) run --configs a --tiers over ;;
        c) run --configs c --tiers fits over ;;
        b-fits) run --configs b --b-variants final --tiers fits ;;
        b-over) run --configs b --b-variants final --tiers over ;;
        d-fits) run --configs d --tiers fits ;;
        d-over) run --configs d --tiers over ;;
        *) echo "unknown stage: $1" >&2; exit 2 ;;
    esac
}

case "$1" in
    pilot | all)
        for s in a-fits a-over c b-fits b-over d-fits d-over; do
            echo "=== stage $s ($DS) ==="
            stage "$s"
        done
        ;;
    *) stage "$1" ;;
esac
