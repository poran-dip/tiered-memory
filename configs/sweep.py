#!/usr/bin/env python3
"""Shrink simulated local DRAM per workload; record gem5-native stats for the ROI.
  python3 configs/sweep.py        # from the repo root, or anywhere
Status 'OOM' = working set no longer fits in simulated DRAM (the 'limit').
Metrics come from the FIRST stats block in stats.txt = the ROI (reset at ROI begin,
dumped at ROI end by common.h); the trailing block is post-ROI teardown, ignored.
Columns are documented in docs/measurements.md. The full ROI stats of every run are also saved
as roi_stats.json next to stats.txt, so new metrics can be derived later without re-simulating.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO))

from tiered_memory.csvout import RESULTS, ResultsFile  # noqa: E402
from tiered_memory.gem5stats import roi_block, summarize  # noqa: E402
from tiered_memory.result import parse_result, throughput_cols  # noqa: E402
from tiered_memory.schema import SLOW_TIER_PATTERN  # noqa: E402
from tiered_memory.workloads import GEM5_DEFAULTS  # noqa: E402

# workload -> (ws_mb, iters); sized for gem5 speed, keep ws >> L2 so DRAM is exercised
DEFAULTS = GEM5_DEFAULTS


def pow2_mib(x):
    m = 1
    while m < x:
        m *= 2
    return m


def collect(row, od, log, slow_pattern):
    """Fill row with everything measurable from one finished run's output directory."""
    res = parse_result(log)
    if res.get("checksum"):
        row["checksum"] = res["checksum"]
    sp = os.path.join(od, "stats.txt")
    if not os.path.exists(sp):
        return
    st = roi_block(sp)
    row.update(summarize(st, slow_pattern))
    Path(od, "roi_stats.json").write_text(json.dumps(st, indent=1, sort_keys=True) + "\n")
    # Time and work for throughput come from the simulator, not the guest's emulated clock.
    if res and row.get("sim_sec"):
        row.update(throughput_cols(row["workload"], res.get("work"), row["sim_sec"]))
        row["roi_sec_guest"] = res.get("roi_sec", "")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--gem5-bin", default=str(REPO / "third_party/gem5/build/X86/gem5.opt"))
    p.add_argument("--bindir", default=str(REPO / "workloads/build/m5"))
    p.add_argument("--workloads", nargs="+", default=list(DEFAULTS))
    p.add_argument(
        "--ratios",
        type=float,
        nargs="+",
        default=[8, 4, 2, 1],
        help="simulated DRAM = ws * ratio, rounded up to power-of-2 MiB",
    )
    p.add_argument("--mem", nargs="+", help="explicit sizes (MiB) overriding --ratios")
    p.add_argument("--cpu", default="timing")
    p.add_argument("--timeout", type=int, default=3600)
    p.add_argument(
        "--reparse", action="store_true", help="re-parse existing m5out dirs, no simulation"
    )
    p.add_argument(
        "--slow-pattern",
        default=SLOW_TIER_PATTERN,
        help="regex; memory controllers whose stat path matches count as the slow tier",
    )
    p.add_argument("--out", default=str(RESULTS / "gem5_sweep.csv"))
    p.add_argument("--fresh", action="store_true", help="overwrite --out instead of merging")
    p.add_argument("--outroot", default=str(REPO / "m5out"))
    a = p.parse_args()
    results = ResultsFile(a.out, key=("workload", "ws_mb", "dram_mib", "cpu"), fresh=a.fresh)
    for w in a.workloads:
        ws, it = DEFAULTS[w]
        mems = [int(m) for m in a.mem] if a.mem else [pow2_mib(ws * r) for r in a.ratios]
        for m in mems:
            od = f"{a.outroot}/{w}_{m}MiB_{a.cpu}"
            cmd = [
                a.gem5_bin,
                "-d",
                od,
                str(HERE / "run.py"),
                "--binary",
                f"{a.bindir}/{w}",
                "--mem-size",
                f"{m}MiB",
                "--cpu",
                a.cpu,
                "--",
                "-s",
                str(ws),
                "-i",
                str(it),
            ]
            row = {
                "workload": w,
                "ws_mb": ws,
                "dram_mib": m,
                "slow_mib": 0,  # no second tier yet
                "ratio": round(m / ws, 2),
                "cpu": a.cpu,
            }
            if a.reparse:
                sp, lg = os.path.join(od, "stats.txt"), os.path.join(od, "run.log")
                log = Path(lg).read_text() if os.path.exists(lg) else ""
                if "Out of memory" in log:
                    row["status"] = "OOM"
                elif os.path.exists(sp):
                    collect(row, od, log, a.slow_pattern)
                    # OOM runs leave an all-zero stats.txt
                    row["status"] = "ok" if row.get("insts") else "OOM"
                else:
                    row["status"] = "not-run"
                print(row, file=sys.stderr)
                results.add(row)
                continue
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=a.timeout)
                log = r.stdout + r.stderr
                os.makedirs(od, exist_ok=True)
                Path(od, "run.log").write_text(log)
                if "Out of memory" in log:
                    row["status"] = "OOM"
                elif r.returncode != 0:
                    row["status"] = f"exit{r.returncode}"
                else:
                    row["status"] = "ok"
                if row["status"] == "ok":
                    collect(row, od, log, a.slow_pattern)
                else:
                    m_ = re.search(r"checksum=(\d+)", log)
                    if m_:
                        row["checksum"] = m_.group(1)
            except subprocess.TimeoutExpired:
                row["status"] = "timeout"
            print(row, file=sys.stderr)
            results.add(row)
    print(f"wrote {a.out}", file=sys.stderr)
