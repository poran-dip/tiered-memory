#!/usr/bin/env python3
"""Compare config B runs against A, C and D at the same workload and arguments.

  python3 compare_bcd.py [results/gem5_fs.csv]

For every B run with a finished ROI, finds the A, C and D runs of the same workload with the
same workload arguments and prints ROI simulated time ratios:
  vs D: D_time / B_time   > 1 means B is faster than swap (expected when the working set spills)
  vs C: C_time / B_time   <= 1 expected (C is all-fast DRAM); > 1.02 is flagged for a look
Also checks the checksums agree (same answer in every config) and shows how much of B's memory
traffic went to node 1 (remote_access_frac) and over the link (link_up_bytes).
"""

import csv
import sys
from collections import defaultdict

path = sys.argv[1] if len(sys.argv) > 1 else "results/gem5_fs.csv"
with open(path, newline="") as f:
    rows = list(csv.DictReader(f))


def done(r):
    return r.get("roi_completed") == "True" and r.get("sim_sec") not in ("", None)


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


by_key = defaultdict(lambda: defaultdict(list))
for r in rows:
    by_key[(r.get("workload", ""), r.get("workload_args", ""))][r.get("config", "")].append(r)

flags, missing = [], set()
print(
    f"{'B run':34} {'B sim s':>9} {'vs D':>7} {'vs C':>7} {'A':>6} {'remote':>7} "
    f"{'link up MB':>10}  checksum"
)
for key in sorted(by_key):
    cfgs = by_key[key]
    ref = {}
    for c in "ACD":
        ok = [r for r in cfgs.get(c, []) if done(r)]
        ref[c] = ok[0] if ok else None
        if not cfgs.get(c):
            missing.add((c, key))
    for b in sorted(cfgs.get("B", []), key=lambda r: r["run"]):
        if not done(b):
            print(f"{b['run']:34} {'(ROI not completed: ' + b.get('exit_cause', '?') + ')'}")
            continue
        tb = f(b["sim_sec"])
        vd = f(ref["D"]["sim_sec"]) / tb if ref["D"] else None
        vc = f(ref["C"]["sim_sec"]) / tb if ref["C"] else None
        a = "ok" if ref["A"] else ("OOM/fail" if cfgs.get("A") else "-")
        sums = {c: r.get("checksum", "") for c, r in ref.items() if r}
        sums["B"] = b.get("checksum", "")
        agree = len({s for s in sums.values() if s}) <= 1
        up = f(b.get("link_up_bytes"))
        print(
            f"{b['run']:34} {tb:9.4f} "
            f"{(f'{vd:.2f}x' if vd else '-'):>7} {(f'{vc:.2f}x' if vc else '-'):>7} {a:>6} "
            f"{b.get('remote_access_frac', ''):>7} "
            f"{(f'{up / 1e6:.1f}' if up is not None else '-'):>10}  "
            f"{'same' if agree else 'DIFFERS ' + str(sums)}"
        )
        if vc and vc > 1.02:
            flags.append(f"{b['run']}: faster than C ({vc:.2f}x)")
        if vd and vd < 1:
            flags.append(f"{b['run']}: slower than D ({vd:.2f}x)")
        if not agree:
            flags.append(f"{b['run']}: checksum differs from another config")

print()
if missing:
    print("No runs at all for these (config, workload, args), so no comparison:")
    for c, (wl, args) in sorted(missing):
        if by_key[(wl, args)].get("B"):
            print(f"  config {c}: {wl} {args}")
print("Flags:" if flags else "No flags.")
for x in flags:
    print("  " + x)
