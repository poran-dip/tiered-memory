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
nodes have been checked. Its PCIe link is swept with --b-links / --b-latency (Phase 4/5):

  python3 configs/run_all_working_sets.py --configs b --b-links gen3x4 gen4x8 gen4x16 -j 3
  python3 configs/run_all_working_sets.py --configs b --b-links gen4x8 --b-latency pcie cxl

B on the default link (gen4x8, pcie latencies) is named b-<workload>-<tier>-<MiB>; other links
add a tag: b_gen3x4-..., b_gen4x8-cxl-..., b_nolink-... (--b-links none: DDR3 straight on the
memory bus, no link).

Order: the whole fits tier first, cheapest first, so a broken setup shows up within minutes. Then
everything else longest-first, which keeps the batch's total time close to the longest single run
divided across the -j slots.

While the batch runs, a status block every --status-sec seconds (default 60) shows each run in
flight: its stage (booting, setup, ROI, after ROI) and, in the ROI, simulated time, the work the
workload reports done (every workload but sort; before its first percent, instructions against
the estimate) and roughly how long is left, from the progress lines gem5 writes to
results/fs/<run>/gem5.log. "Time left" comes from the reported work at the ROI's pace so far, or
without it from the instruction estimate at the current speed; the batch total adds the queued
runs' estimates.

Each run is capped at --max-wall-h hours of host time; a run that hits it is killed, its row is
recorded with roi_completed=False and exit_cause "wall timeout", and it counts as done. Rerun it
with a bigger cap and --overwrite --only <run name>.
"""

import argparse
import csv
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO))

# --dataset picks results/<dataset>/ and results/gem5_<dataset>.csv. tiered_memory.fsreport reads it
# from TM_DATASET when it is imported (and the gem5 processes inherit it), so it is set here, before
# the imports below; argparse declares the option again further down for --help and validation.
for _i, _arg in enumerate(sys.argv):
    if _arg == "--dataset" and _i + 1 < len(sys.argv):
        os.environ["TM_DATASET"] = sys.argv[_i + 1]
    elif _arg.startswith("--dataset="):
        os.environ["TM_DATASET"] = _arg.split("=", 1)[1]

from tiered_memory import caps, pcie  # noqa: E402
from tiered_memory.batchstatus import LogWatch, batch_left_h, status_line  # noqa: E402
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
from tiered_memory.progress import fmt_wall  # noqa: E402
from tiered_memory.workloads import FS_TIERS, NAMES, TIERS  # noqa: E402

GEM5 = REPO / "third_party/gem5/build/X86/gem5.opt"
CONFIG_FILES = {"a": "a_baseline.py", "b": "b_proposed.py", "c": "c_ideal.py", "d": "d_swap.py"}
CAP_RANK = {"a": 0, "c": 0, "b": 1, "d": 2}  # B's cap comes from C; D's from B and C

# Time estimate (for -n and the run order only; ±50%). The timing CPU simulates about 1M guest
# instructions per wall second (finding 002, F9), and each run spends ~1.5 min booting on KVM and
# setting up. Over cap, A is OOM-killed during setup, and D also pays for paging: about 10,000
# guest instructions per page moved (finding 002, F9). The paging figures below are rough guesses
# per workload, in millions of instructions: sequential ones move about their overflow (stream,
# hashjoin, sort), kvdecode's 2 tokens cycle the whole working set, and random ones (graph, chase)
# can fault on every touch of an evicted page. hotcold's hot window is touched last, so it stays
# resident; its faults come from the 5% of accesses that land on swapped-out cold pages.
MINSTS_PER_MIN = 60
RUN_OVERHEAD_MIN = 1.5
D_OVER_PAGING_MINSTS = {
    "stream": 1500,
    "hashjoin": 1000,
    "sort": 2000,
    "kvdecode": 2500,
    "graph": 3000,
    "chase": 10000,
    "hotcold": 2000,
    "kvstore": 20000,
}


def estimate_minsts(cfg, wl, tier):
    """Estimated ROI instructions in millions, guest paging included (what gem5 will count)."""
    if tier == "over" and cfg == "a":
        return 0  # dies in setup
    minsts = FS_TIERS[wl][tier].est_minsts
    if tier == "over" and cfg == "d":
        minsts += D_OVER_PAGING_MINSTS.get(wl, 0)
    return minsts


def estimate_min(cfg, wl, tier):
    return RUN_OVERHEAD_MIN + estimate_minsts(cfg, wl, tier) / MINSTS_PER_MIN


# The six config B variants of the final dataset, as link:latency[:tiering]. Worst and best link,
# each with PCIe and CXL latencies; the best link again with the kernel's tiering on.
FINAL_B = [
    "gen3x4:pcie",
    "gen3x4:cxl",
    "gen4x16:pcie",
    "gen4x16:cxl",
    "gen4x16:pcie:on",
    "gen4x16:cxl:on",
]


def parse_variant(text):
    """'gen4x16:cxl:on' -> ('gen4x16', 'cxl', 'on'); latency and tiering are optional."""
    parts = text.split(":")
    if not 1 <= len(parts) <= 3:
        raise ValueError(f"bad --b-variants entry {text!r}: expected link[:latency[:tiering]]")
    link = parts[0]
    lat = parts[1] if len(parts) > 1 and parts[1] else pcie.DEFAULT_LATENCY
    tiering = parts[2] if len(parts) > 2 else "default"
    pcie.parse_link(link)
    if lat not in pcie.LATENCY_PRESETS:
        raise ValueError(
            f"bad latency {lat!r} in {text!r}: one of {', '.join(pcie.LATENCY_PRESETS)}"
        )
    if tiering not in ("default", "on", "off"):
        raise ValueError(f"bad tiering {tiering!r} in {text!r}: default, on or off")
    return link, lat, tiering


def b_variants(a):
    """[(run-name prefix, config options)] for config B.

    --b-variants lists them explicitly (or 'final'); otherwise one per --b-links x --b-latency,
    with the kernel's default tiering. A tiering run is named like its link variant plus
    -tiering (b_gen4x16-cxl-tiering-...), or -notiering when forced off.
    """
    explicit = getattr(a, "b_variants", None)
    if explicit:
        names = FINAL_B if explicit == ["final"] else explicit
        specs = [parse_variant(t) for t in names]
    else:
        specs = [(link, lat, "default") for link in a.b_links for lat in a.b_latency]
    out = []
    for link, lat, tiering in specs:
        cfg = pcie.link_config(link, lat)
        if cfg is None and lat != pcie.DEFAULT_LATENCY:
            continue  # no link: the latency preset means nothing
        tag = pcie.variant_tag(cfg)
        prefix = "b_" + tag if tag else "b"
        opts = ["--link", link] + (["--link-latency", lat] if cfg is not None else [])
        if tiering != "default":
            prefix += "-tiering" if tiering == "on" else "-notiering"
            opts += ["--tiering", tiering]
        out.append((prefix, opts))
    return out


def plan(a):
    """[(name, config, workload, tier, [workload args], est. minutes, [config options])], in order."""
    runs = []
    for tier in a.tiers:
        for wl in a.workloads:
            t = FS_TIERS[wl][tier]
            if t is None:
                continue
            for cfg in a.configs:
                wl_args = ["-s", str(t.ws), "-i", str(t.iters)]
                variants = b_variants(a) if cfg == "b" else [(cfg, [])]
                for prefix, opts in variants:
                    name = f"{prefix}-{wl}-{tier}-{t.ws}"
                    runs.append((name, cfg, wl, tier, wl_args, estimate_min(cfg, wl, tier), opts))
    if a.only:
        runs = [r for r in runs if r[0] in a.only]
    fits = sorted((r for r in runs if r[3] == "fits"), key=lambda r: r[5])
    # Capped runs need finished reference runs (C before B before D), so the order of the rest
    # follows that dependency first and only then goes longest-first.
    rest = sorted((r for r in runs if r[3] != "fits"), key=lambda r: (CAP_RANK[r[1]], -r[5]))
    return fits + rest


def makespan_h(minutes, jobs):
    """Total wall hours for these run lengths on `jobs` slots, started in the given order."""
    slots = [0.0] * max(jobs, 1)
    for m in minutes:
        i = slots.index(min(slots))
        slots[i] += m
    return max(slots) / 60


def gem5_cmd(name, cfg, wl, wl_args, a, opts=()):
    cmd = [str(GEM5), "-d", str(FS_DIR / name), str(HERE / CONFIG_FILES[cfg]), "--workload", wl]
    if not Path("/sys/bus/event_source/devices/cpu").exists():
        cmd.append("--kvm-no-perf")  # WSL2 and most VMs have no hardware perf counters
    if a.overwrite:
        cmd.append("--overwrite")
    cmd += list(opts)
    cmd += shlex.split(a.gem5_args)
    return cmd + ["--", *wl_args]


def stamp():
    return time.strftime("%H:%M:%S")


def csv_rows():
    """The dataset's CSV rows so far (caps are derived from finished runs in it)."""
    if not FS_CSV.exists():
        return []
    with open(FS_CSV, newline="") as fh:
        return list(csv.DictReader(fh))


def cap_spec(a):
    return caps.CapSpec(d_x_b=a.cap_d_x_b, d_insts_x_c=a.cap_d_insts_x_c, b_x_c=a.cap_b_x_c)


def drop_scratch(name):
    """Delete a finished run's sparse swap image: it is scratch, and Explorer counts its 2 GiB."""
    swap = FS_DIR / name / "swap.img"
    if swap.exists():
        swap.unlink()


def dirty_tree():
    """Uncommitted changes outside results/ (a dataset should come from one clean commit)."""
    out = subprocess.run(
        ["git", "status", "--porcelain", "--", ".", ":(exclude)results"],
        cwd=REPO,
        capture_output=True,
        text=True,
    ).stdout
    return [line for line in out.splitlines() if line.strip()]


def wall_timeout(name, hours):
    """Record a run killed for taking too long, so the batch moves on and the CSV shows it."""
    outdir = FS_DIR / name
    row = csv_row(outdir, collect(outdir))
    row["exit_cause"] = f"wall timeout ({hours:g} h)"
    add_to_csv(row, FS_CSV)
    release_outdir(outdir)


def print_status(running, watches, todo, n_done, a):
    """One line per run in flight (stage, ROI progress, time left), read from its gem5.log."""
    now = time.monotonic()
    lines, left = [], []
    for name, (_, t0, log) in running.items():
        watch, minsts, est = watches[name]
        watch.poll(log.name)
        lines.append(status_line(name, watch, now - t0, minsts, est))
        left.append(watch.remaining_min(minsts, (now - t0) / 60, est))
    hours = batch_left_h(left, [r[5] for r in todo], a.jobs)
    print(
        f"[{stamp()}] status  {len(running)} running, {len(todo)} queued, {n_done} finished;"
        f" batch ~{fmt_wall(hours * 3600)} left (estimate)",
        flush=True,
    )
    for line in lines:
        print(line, flush=True)


def _run_all(todo, running, a):
    """Keep up to a.jobs gem5 processes busy until todo is empty; returns names that failed."""
    failed = []
    cap = a.max_wall_h * 3600
    watches = {}  # name -> (LogWatch, est. ROI M insts, est. minutes)
    n_done = 0
    last_status = time.monotonic()
    deferred = 0  # runs put back in a row because their reference run has not finished yet
    while todo or running:
        while todo and len(running) < a.jobs:
            item = todo.pop(0)
            name, cfg, wl, tier, wl_args, est, opts = item
            try:
                opts = [*opts, *caps.cap_options(cfg, wl, tier, csv_rows(), cap_spec(a))]
            except caps.MissingReference as e:
                if running:  # the reference run may be in flight: try others, come back later
                    todo.append(item)
                    deferred += 1
                    if deferred > len(todo):
                        break  # every queued run is waiting on a reference
                    continue
                failed.append(name)
                print(
                    f"[{stamp()}] SKIPPED {name}: {e}; run its reference configs first", flush=True
                )
                continue
            deferred = 0
            outdir = FS_DIR / name
            outdir.mkdir(parents=True, exist_ok=True)
            log = open(outdir / "gem5.log", "w")  # noqa: SIM115 (closed when the run ends)
            proc = subprocess.Popen(
                gem5_cmd(name, cfg, wl, wl_args, a, opts),
                cwd=REPO,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            running[name] = (proc, time.monotonic(), log)
            watches[name] = (LogWatch(), estimate_minsts(cfg, wl, tier), est)
            print(f"[{stamp()}] start   {name}  ({len(todo)} queued)", flush=True)
        time.sleep(5)
        for name, (proc, t0, log) in list(running.items()):
            rc = proc.poll()
            if rc is None and time.monotonic() - t0 > cap:
                os.killpg(proc.pid, 9)
                proc.wait()
                log.close()
                wall_timeout(name, a.max_wall_h)
                drop_scratch(name)
                failed.append(name)
                print(f"[{stamp()}] TIMEOUT {name} after {a.max_wall_h:g} h", flush=True)
                del running[name]
                n_done += 1
            elif rc is not None:
                log.close()
                mins = (time.monotonic() - t0) / 60
                ok = rc == 0 and (FS_DIR / name / FS_RESULT).exists()
                drop_scratch(name)
                if not ok:
                    failed.append(name)
                tag = "done   " if ok else "FAILED "
                print(f"[{stamp()}] {tag} {name}  {mins:.1f} min  (log: {log.name})", flush=True)
                del running[name]
                n_done += 1
        if a.status_sec and running and time.monotonic() - last_status >= a.status_sec:
            print_status(running, watches, todo, n_done, a)
            last_status = time.monotonic()
    return failed


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--dataset",
        default=os.environ.get("TM_DATASET", "fs"),
        help="results/<dataset>/ holds the runs and results/gem5_<dataset>.csv their rows "
        "(default %(default)s)",
    )
    p.add_argument(
        "--tiers",
        nargs="+",
        choices=TIERS,
        default=["fits", "over"],
        help="working-set tiers; near was dropped (it costs much more than fits and shows the "
        "same thing)",
    )
    p.add_argument("--configs", nargs="+", choices=list(CONFIG_FILES), default=["a", "c", "d"])
    p.add_argument("--workloads", nargs="+", choices=NAMES, default=NAMES)
    p.add_argument(
        "--b-links",
        nargs="+",
        default=[pcie.DEFAULT_LINK],
        metavar="LINK",
        help="config B's PCIe links to run: genNxM or none (default %(default)s)",
    )
    p.add_argument(
        "--b-latency",
        nargs="+",
        choices=list(pcie.LATENCY_PRESETS),
        default=[pcie.DEFAULT_LATENCY],
        help="config B's link latency presets to run (default %(default)s)",
    )
    p.add_argument(
        "--b-variants",
        nargs="+",
        metavar="LINK[:LAT[:TIERING]]",
        help="config B variants explicitly, e.g. gen4x16:cxl:on (tiering on); 'final' is the six "
        "of the final dataset: " + " ".join(FINAL_B) + ". Replaces --b-links/--b-latency",
    )
    p.add_argument(
        "--cap-d-x-b",
        type=float,
        default=caps.CapSpec().d_x_b,
        help="D over tier: stop the ROI after this many times the longest config B ROI for the "
        "same workload (0 = no cap). Needs those B runs finished first. Default %(default)s",
    )
    p.add_argument(
        "--cap-d-insts-x-c",
        type=float,
        default=caps.CapSpec().d_insts_x_c,
        help="D over tier: also stop at this many times config C's ROI instructions (0 = off). "
        "Default %(default)s",
    )
    p.add_argument(
        "--cap-b-x-c",
        type=float,
        default=caps.CapSpec().b_x_c,
        help="B over tier: stop the ROI after this many times config C's ROI (0 = no cap). "
        "Default %(default)s",
    )
    p.add_argument(
        "--allow-dirty",
        action="store_true",
        help="run even with uncommitted changes outside results/ (rows then carry -dirty commits)",
    )
    p.add_argument("-j", "--jobs", type=int, default=1, help="gem5 processes at once")
    p.add_argument(
        "--max-wall-h",
        type=float,
        default=200,
        help="backstop: kill a run after this many hours of host time. The ROI caps normally end "
        "a slow run far sooner and keep its stats; a wall kill keeps nothing. Default %(default)s",
    )
    p.add_argument(
        "--status-sec",
        type=float,
        default=60,
        help="print the stage and ROI progress of every run in flight about every this many "
        "seconds, read from each run's gem5.log (0 = off)",
    )
    p.add_argument("--gem5-args", default="", help='extra config options, e.g. "--roi-cpu o3"')
    p.add_argument("--only", nargs="+", help="run only these run names")
    p.add_argument("--overwrite", action="store_true", help="rerun runs that are already done")
    p.add_argument("-n", "--dry-run", action="store_true", help="list the runs and the estimate")
    a = p.parse_args()
    for link in a.b_links:
        try:
            pcie.parse_link(link)
        except ValueError as e:
            p.error(str(e))
    if a.b_variants:
        try:
            b_variants(a)
        except ValueError as e:
            p.error(str(e))
    if a.dataset != os.environ.get("TM_DATASET", "fs"):
        p.error("--dataset must be given on the command line (not through a wrapper that drops it)")
    if not a.dry_run and not a.allow_dirty and (dirty := dirty_tree()):
        shown = "\n  ".join(dirty[:8])
        sys.exit(
            "uncommitted changes outside results/ (a dataset should come from one clean commit; "
            f"--allow-dirty to run anyway):\n  {shown}"
        )

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
        for name, cfg, wl, tier, wl_args, mins, opts in todo:
            try:
                cap_opts = caps.cap_options(cfg, wl, tier, csv_rows(), cap_spec(a))
            except caps.MissingReference as e:
                cap_opts, note = [], f"   [cap pending: {e}]"
            else:
                note = ""
            cmd = shlex.join(gem5_cmd(name, cfg, wl, wl_args, a, [*opts, *cap_opts])[3:])
            print(f"~{mins:5.0f} min  {name}: {cmd.replace(f'{REPO}/', '')}{note}")
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
