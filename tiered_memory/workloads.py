"""The one list of workloads. Everything that runs or reports on them reads this table.

To add a workload: write workloads/<name>.cpp, add it to WL in workloads/Makefile, and add one
row here (tests/test_workloads.py checks the two lists match).
"""

from pathlib import Path
from typing import NamedTuple


class Workload(NamedTuple):
    work_unit: str  # what one unit of the RESULT line's `work` means
    pdf_class: str  # workload class from the project brief (section 7)
    native_ws: int  # MiB, default working set for quick native runs (sweep_ram --descend)
    native_iters: int  # iters for native runs, sized for a ROI of roughly 0.1-0.5 s at native_ws
    gem5_ws: int  # MiB, default working set for gem5 SE runs (keep >> L2 so DRAM is exercised)
    gem5_iters: int


WORKLOADS = {
    "stream": Workload("bytes", "bandwidth", 64, 3, 8, 2),
    "sort": Workload("compares", "random/irregular", 64, 1, 4, 1),
    # gemm's work grows with the cube of its size; it is the cache-resident compute control
    "gemm": Workload("flops", "compute/matrix", 16, 1, 2, 1),
    "chase": Workload("hops", "random/irregular", 64, 2, 8, 1),
    "kvdecode": Workload("bytes", "CPU AI/LLM", 64, 64, 8, 2),
    "graph": Workload("edges", "graph", 64, 4, 8, 1),
    "kvstore": Workload("ops", "database (OLTP)", 64, 4, 8, 1),
    "hashjoin": Workload("tuples", "database (OLAP)", 64, 4, 8, 1),
}


class Tier(NamedTuple):
    ws: int  # MiB (-s)
    iters: int  # -i
    est_minsts: int  # estimated ROI instructions, millions (see below)


# Full-system working-set tiers, against config A's fast tier (512 MiB; the guest has MemTotal
# 459 MiB and a workload gets at least ~386 MiB but less than 448, finding 002 F8):
#   fits  sanity size, far below capacity and far above the 256 KiB L2; A, C and D should agree
#   near  just fits in A: 384 MiB ran with no swap (chase 336: its setup needs 12.5% extra)
#   over  past A's capacity: 448 MiB overflowed by 30-60 MiB
# None = not meaningful for that workload. gemm is the compute control (work grows as N^3), so it
# only has a fits size. kvstore's table rounds down to a power of two (any -s from 256 to 511 gives
# a 256 MiB table), so near and over wait for a sizing fix. kvdecode over cap runs 2 tokens: the
# first reads every page once; only from the second does LRU evict pages just before reuse.
# est_minsts: ROI instructions counted with callgrind on the same gcc 13.3 -O2 build (within
# ~1.3x of gem5's own count for chase and stream), excluding any paging work.
FS_TIERS = {
    "stream": {"fits": Tier(64, 1, 11), "near": Tier(384, 1, 67), "over": Tier(448, 1, 78)},
    "chase": {"fits": Tier(64, 1, 6), "near": Tier(336, 1, 33), "over": Tier(448, 1, 44)},
    "kvdecode": {"fits": Tier(64, 1, 29), "near": Tier(384, 1, 175), "over": Tier(448, 2, 409)},
    "hashjoin": {"fits": Tier(64, 1, 113), "near": Tier(384, 1, 678), "over": Tier(448, 1, 791)},
    "graph": {"fits": Tier(64, 1, 201), "near": Tier(384, 1, 1205), "over": Tier(448, 1, 1406)},
    "kvstore": {"fits": Tier(64, 1, 298), "near": None, "over": None},
    "sort": {"fits": Tier(32, 1, 321), "near": Tier(384, 1, 4504), "over": Tier(448, 1, 5302)},
    "gemm": {"fits": Tier(1, 1, 198), "near": None, "over": None},
}
TIERS = ("fits", "near", "over")

NAMES = list(WORKLOADS)
WORK_UNIT = {k: w.work_unit for k, w in WORKLOADS.items()}
NATIVE_WS = {k: w.native_ws for k, w in WORKLOADS.items()}
NATIVE_ITERS = {k: w.native_iters for k, w in WORKLOADS.items()}
GEM5_DEFAULTS = {k: (w.gem5_ws, w.gem5_iters) for k, w in WORKLOADS.items()}


def stale_binaries(bindir, names, src_dir=None):
    """Names whose binary in bindir is missing or older than its source or common.h."""
    src = Path(src_dir) if src_dir else Path(__file__).resolve().parents[1] / "workloads"
    common = (src / "common.h").stat().st_mtime
    out = []
    for n in names:
        exe = Path(bindir) / n
        if not exe.exists() or exe.stat().st_mtime < max(
            common, (src / f"{n}.cpp").stat().st_mtime
        ):
            out.append(n)
    return out
