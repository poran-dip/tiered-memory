#!/usr/bin/env python3
"""Run every workload at each of its working-set tiers (fits / near / over) under configs A, C, D.

The tier sizes live in tiered_memory/workloads.py (FS_TIERS). Every run goes to
results/fs/<config>-<workload>-<tier>-<MiB>/ and adds its row to results/gem5_fs.csv (see
tiered_memory/fsreport.py). Runs already done are skipped, so an interrupted batch resumes where it
stopped. Run from anywhere with the host Python (not gem5):

  python3 configs/run_all_working_sets.py -n            # list the runs and the time estimate
  python3 configs/run_all_working_sets.py -j 3          # everything, 3 gem5 processes at once
  python3 configs/run_all_working_sets.py --tiers fits  # only the sanity sizes
  python3 configs/run_all_working_sets.py --workloads stream chase -j 3

Config B (b_proposed.py) runs only with --configs, e.g. --configs a b c d, until its two memory
nodes have been checked.

Order: the whole fits tier first, cheapest first, so a broken setup shows up within minutes. Then
everything else longest-first, which keeps the batch's total time close to the longest single run
divided across the -j slots.

Each run is capped at --max-wall-h hours of host time; a run that hits it is killed, its row is
recorded with roi_completed=False and exit_cause "wall timeout", and it counts as done. Rerun it
with a bigger cap and --overwrite --only <run name>.
"""

import argparse
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO))

from tiered_memory.fsreport import (  # noqa: E402
    FS_CSV,
    FS_DIR,
    FS_RESULT,
    add_to_csv,
    claim_outdir,
    collect,
    csv_row,
    release_outdir,
)
from tiered_memory.workloads import FS_TIERS, NAMES, TIERS  # noqa: E402

GEM5 = REPO / "third_party/gem5/build/X86/gem5.opt"
CONFIG_FILES = {"a": "a_baseline.py", "b": "b_proposed.py", "c": "c_ideal.py", "d": "d_swap.py"}

# Time estimate (for -n and the run order only; ±50%). The timing CPU simulates about 1M guest
# instructions per wall second (finding 002, F9), and each run spends ~1.5 min booting on KVM and
# setting up. Over cap, A is OOM-killed during setup, and D also pays for paging: about 10,000
# guest instructions per page moved (finding 002, F9). The paging figures below are rough guesses
# per workload, in millions of instructions: sequential ones move about their overflow (stream,
# hashjoin, sort), kvdecode's 2 tokens cycle the whole working set, and random ones (graph, chase)
# can fault on every touch of an evicted page.
MINSTS_PER_MIN = 60
RUN_OVERHEAD_MIN = 1.5
D_OVER_PAGING_MINSTS = {
    "stream": 1500,
    "hashjoin": 1000,
    "sort": 2000,
    "kvdecode": 2500,
    "graph": 3000,
    "chase": 10000,
}


def estimate_min(cfg, wl, tier):
    t = FS_TIERS[wl][tier]
    if tier == "over" and cfg == "a":
        return RUN_OVERHEAD_MIN  # dies in setup
    minsts = t.est_minsts
    if tier == "over" and cfg == "d":
        minsts += D_OVER_PAGING_MINSTS.get(wl, 0)
    return RUN_OVERHEAD_MIN + minsts / MINSTS_PER_MIN


def plan(a):
    """[(name, config, workload, tier, [workload args], estimated minutes)], in run order."""
    runs = []
    for tier in a.tiers:
        for wl in a.workloads:
            t = FS_TIERS[wl][tier]
            if t is None:
                continue
            for cfg in a.configs:
                name = f"{cfg}-{wl}-{tier}-{t.ws}"
                wl_args = ["-s", str(t.ws), "-i", str(t.iters)]
                runs.append((name, cfg, wl, tier, wl_args, estimate_min(cfg, wl, tier)))
    if a.only:
        runs = [r for r in runs if r[0] in a.only]
    fits = sorted((r for r in runs if r[3] == "fits"), key=lambda r: r[5])
    rest = sorted((r for r in runs if r[3] != "fits"), key=lambda r: -r[5])
    return fits + rest


def makespan_h(minutes, jobs):
    """Total wall hours for these run lengths on `jobs` slots, started in the given order."""
    slots = [0.0] * max(jobs, 1)
    for m in minutes:
        i = slots.index(min(slots))
        slots[i] += m
    return max(slots) / 60


def gem5_cmd(name, cfg, wl, wl_args, a):
    cmd = [str(GEM5), "-d", str(FS_DIR / name), str(HERE / CONFIG_FILES[cfg]), "--workload", wl]
    if not Path("/sys/bus/event_source/devices/cpu").exists():
        cmd.append("--kvm-no-perf")  # WSL2 and most VMs have no hardware perf counters
    if a.overwrite:
        cmd.append("--overwrite")
    cmd += shlex.split(a.gem5_args)
    return cmd + ["--", *wl_args]


def stamp():
    return time.strftime("%H:%M:%S")


def wall_timeout(name, hours):
    """Record a run killed for taking too long, so the batch moves on and the CSV shows it."""
    outdir = FS_DIR / name
    row = csv_row(outdir, collect(outdir))
    row["exit_cause"] = f"wall timeout ({hours:g} h)"
    add_to_csv(row, FS_CSV)
    release_outdir(outdir)


def _run_all(todo, running, a):
    """Keep up to a.jobs gem5 processes busy until todo is empty; returns names that failed."""
    failed = []
    cap = a.max_wall_h * 3600
    while todo or running:
        while todo and len(running) < a.jobs:
            name, cfg, wl, _, wl_args, _ = todo.pop(0)
            outdir = FS_DIR / name
            outdir.mkdir(parents=True, exist_ok=True)
            log = open(outdir / "gem5.log", "w")  # noqa: SIM115 (closed when the run ends)
            proc = subprocess.Popen(
                gem5_cmd(name, cfg, wl, wl_args, a),
                cwd=REPO,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            running[name] = (proc, time.monotonic(), log)
            print(f"[{stamp()}] start   {name}  ({len(todo)} queued)", flush=True)
        time.sleep(5)
        for name, (proc, t0, log) in list(running.items()):
            rc = proc.poll()
            if rc is None and time.monotonic() - t0 > cap:
                os.killpg(proc.pid, 9)
                proc.wait()
                log.close()
                wall_timeout(name, a.max_wall_h)
                failed.append(name)
                print(f"[{stamp()}] TIMEOUT {name} after {a.max_wall_h:g} h", flush=True)
                del running[name]
            elif rc is not None:
                log.close()
                mins = (time.monotonic() - t0) / 60
                ok = rc == 0 and (FS_DIR / name / FS_RESULT).exists()
                if not ok:
                    failed.append(name)
                tag = "done   " if ok else "FAILED "
                print(f"[{stamp()}] {tag} {name}  {mins:.1f} min  (log: {log.name})", flush=True)
                del running[name]
    return failed


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--tiers", nargs="+", choices=TIERS, default=list(TIERS))
    p.add_argument("--configs", nargs="+", choices=list(CONFIG_FILES), default=["a", "c", "d"])
    p.add_argument("--workloads", nargs="+", choices=NAMES, default=NAMES)
    p.add_argument("-j", "--jobs", type=int, default=1, help="gem5 processes at once")
    p.add_argument("--max-wall-h", type=float, default=6, help="kill a run after this many hours")
    p.add_argument("--gem5-args", default="", help='extra config options, e.g. "--roi-cpu o3"')
    p.add_argument("--only", nargs="+", help="run only these run names")
    p.add_argument("--overwrite", action="store_true", help="rerun runs that are already done")
    p.add_argument("-n", "--dry-run", action="store_true", help="list the runs and the estimate")
    a = p.parse_args()

    todo = []
    for run in plan(a):
        if (FS_DIR / run[0] / FS_RESULT).exists() and not a.overwrite:
            print(f"skip    {run[0]} (done)")
        else:
            todo.append(run)
    est = [r[5] for r in todo]
    summary = (
        f"{len(todo)} run(s), estimated {sum(est) / 60:.1f} h back to back, "
        f"~{makespan_h(est, a.jobs):.1f} h with -j {a.jobs} (±50%)"
    )
    if a.dry_run:
        for name, cfg, wl, _, wl_args, mins in todo:
            cmd = shlex.join(gem5_cmd(name, cfg, wl, wl_args, a)[3:]).replace(f"{REPO}/", "")
            print(f"~{mins:5.0f} min  {name}: {cmd}")
        print(summary)
        return 0
    if not GEM5.exists():
        sys.exit(f"{GEM5} not built; run scripts/setup.sh")
    print(summary, flush=True)

    running = {}  # name -> (Popen, start time, log file)
    try:
        failed = _run_all(todo, running, a)
    except KeyboardInterrupt:
        # gem5 runs in its own session, so Ctrl-C does not reach it: stop the runs in flight.
        for name, (proc, _, log) in running.items():
            os.killpg(proc.pid, 9)
            proc.wait()
            log.close()
            claim_outdir(FS_DIR / name, overwrite=True)  # clear its partial outputs
            release_outdir(FS_DIR / name)
            print(f"stopped {name}; it will run again next time")
        return 130
    print(
        f"finished; {len(failed)} failed or timed out: {' '.join(failed)}" if failed else "finished"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
