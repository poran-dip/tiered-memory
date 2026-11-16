"""Report for one finished gem5 full-system run, from its output directory alone.

configs/fsboard.py calls collect() when a run ends. It can also be rerun on any old run without
gem5, e.g. after adding a metric:

    python3 -m tiered_memory.fsreport results/fs/a-chase-64
    python3 -m tiered_memory.fsreport --csv results/gem5_fs.csv results/fs/*   # rebuild the CSV

To add runs from other machines to the CSV without rewriting anything else, use
scripts/merge_fs_results.py instead.

Runs whose -d directory is under results/fs/ are the committed ones: each one also gets a row in
results/gem5_fs.csv (keyed by the directory name), and results/fs/.gitignore keeps only the small
files below (fs_result.json, roi_stats.json, guest_output.txt, run.json) in git.

Inputs in the run directory:
  guest_output.txt         the guest run script's stdout, copied to the host with gem5's writefile
                           at the end of the run. Only program output, so lines stay intact
  board.pc.com_1.device    the guest serial console; fallback when guest_output.txt is missing.
                           Guest kernel messages are interleaved with program output there and can
                           split a RESULT or LINUXSTATS line, so a fallback report may lack them
  stats.txt                block 1 is the ROI only if the ROI completed (see roi_completed)
  roi_done                 empty marker written when the ROI's stats were dumped
  roi_stats.json           the ROI block as JSON; used instead of stats.txt when stats.txt is absent
                           (stats.txt is not committed)
  run.json                 how the run was started: config, arguments, gem5 and repo commits, host
"""

import argparse
import contextlib
import fcntl
import json
import os
import re
import sys
from pathlib import Path

from .csvout import RESULTS, ResultsFile, write_rows
from .gem5stats import roi_block, summarize
from .result import parse_result, parse_tagged, throughput_cols

GUEST_OUTPUT = "guest_output.txt"
CONSOLE_FILE = "board.pc.com_1.device"
ROI_MARKER = "roi_done"  # empty file written by the ROI-end handler
ROI_STATS = "roi_stats.json"
ROI_CAPPED = "roi_capped.json"  # written instead of ROI_MARKER when a cap stopped the ROI early
ROI_PROGRESS = "roi_progress.json"  # (sim s, instructions) at each percent of the ROI's work
FS_RESULT = "fs_result.json"
RUN_INFO = "run.json"
PID_FILE = "gem5.pid"  # written while a gem5 process owns the directory

# Which dataset of committed full-system runs this process reads and writes: results/<dataset>/<run>/
# and results/gem5_<dataset>.csv. "fs" is the original dataset (Ubuntu kernel 6.8, no HMAT); set
# TM_DATASET (or run_all_working_sets.py --dataset) to keep a new dataset apart, e.g. "fs_hmat".
DATASET = os.environ.get("TM_DATASET", "fs")
if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", DATASET):
    raise ValueError("TM_DATASET must be a simple name containing letters, digits, '_' or '-'")
FS_DIR = RESULTS / DATASET  # committed full-system runs, one directory each
FS_CSV = RESULTS / f"gem5_{DATASET}.csv"

# Outputs of an earlier run that would be mistaken for this run's if left in place: a stale
# roi_done in particular makes an unfinished run look complete.
STALE_OUTPUTS = (ROI_MARKER, ROI_CAPPED, ROI_PROGRESS, ROI_STATS, FS_RESULT, GUEST_OUTPUT, RUN_INFO)


def roi_completed(outdir):
    """True if the run reached ROI end: configs/fsboard.py writes ROI_MARKER right after the dump.

    Without it, block 1 of stats.txt is gem5's end-of-run dump of the whole run, not the ROI.
    A committed run keeps neither the marker nor stats.txt, only roi_stats.json, which collect()
    writes for a completed ROI alone; so with no stats.txt, roi_stats.json stands in for it.
    """
    outdir = Path(outdir)
    if (outdir / ROI_CAPPED).exists():
        return False  # stopped early by a cap: its stats are a partial ROI
    if (outdir / ROI_MARKER).exists():
        return True
    return not (outdir / "stats.txt").exists() and (outdir / ROI_STATS).exists()


def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}


def censored_columns(outdir):
    """Report columns for a run a cap stopped early (see configs/fsboard.py); {} for any other run.

    est_roi_sec_linear extrapolates the elapsed ROI time over the percent of work done. It is a
    rough guide only: work is not spread evenly over time (a sort's early passes page, its later
    ones do not), which is why the whole progress curve is kept in roi_capped.json.
    """
    cap = read_json(Path(outdir) / ROI_CAPPED)
    if not cap:
        return {}
    cols = {
        "censored_by": cap.get("reason", ""),
        "censored_at_sim_sec": cap.get("sim_sec", ""),
        "censored_at_insts": cap.get("insts", ""),
        "censored_cap_sec": cap.get("cap_sec", ""),
        "censored_cap_insts": cap.get("cap_insts", ""),
        "censored_work_pct": cap.get("work_pct", ""),
    }
    pct, sec = cap.get("work_pct"), cap.get("sim_sec")
    if pct and sec:
        cols["est_roi_sec_linear"] = round(sec * 100.0 / pct, 6)
    return cols


def guest_text(outdir):
    """The guest's output: the writefile copy if present, else the serial console."""
    for name in (GUEST_OUTPUT, CONSOLE_FILE):
        p = Path(outdir) / name
        if p.exists():
            return p.read_text(errors="replace")
    return ""


def _gem5_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass  # exists, owned by someone else
    cmdline = Path(f"/proc/{pid}/cmdline")
    return not cmdline.exists() or b"gem5" in cmdline.read_bytes()


def claim_outdir(outdir, overwrite=False):
    """Make outdir safe for a new run, or return why it is not.

    Refuses while another gem5 process is still writing to outdir (its PID_FILE names a live gem5),
    and refuses to mix with an earlier run's outputs unless overwrite, in which case they are
    deleted first. On success writes this process's PID_FILE and returns None.
    """
    outdir = Path(outdir)
    pid_file = outdir / PID_FILE
    with contextlib.suppress(OSError, ValueError):
        pid = int(pid_file.read_text().strip())
        if pid != os.getpid() and _gem5_alive(pid):
            return f"{outdir} is in use by a running gem5 (pid {pid}); pick another -d directory"
    stale = [n for n in STALE_OUTPUTS if (outdir / n).exists()]
    if stale and not overwrite:
        return (
            f"{outdir} already has a run's outputs ({', '.join(stale)}); "
            "pass --overwrite to replace them, or pick another -d directory"
        )
    for n in stale:
        (outdir / n).unlink()
    outdir.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(f"{os.getpid()}\n")
    return None


def release_outdir(outdir):
    with contextlib.suppress(OSError):
        (Path(outdir) / PID_FILE).unlink()


def roi_stats(outdir):
    """The ROI's stats: from stats.txt (block 1) if present, else from a saved roi_stats.json."""
    outdir = Path(outdir)
    if (outdir / "stats.txt").exists():
        return roi_block(outdir / "stats.txt")
    p = outdir / ROI_STATS
    return json.loads(p.read_text()) if p.exists() else {}


def collect(outdir, workload=None):
    """Report row for one run; also writes roi_stats.json and fs_result.json into outdir."""
    outdir = Path(outdir)
    text = guest_text(outdir)
    # Capped runs never reach guest writefile. Preserve their console-derived evidence
    # in the same small artifact as completed runs before ignored console files disappear.
    if text and (outdir / ROI_CAPPED).exists() and not (outdir / GUEST_OUTPUT).exists():
        (outdir / GUEST_OUTPUT).write_text(text)
    res = parse_result(text)
    # A run killed before its RESULT line still names its workload in run.json.
    workload = workload or res.get("name", "") or read_run_info(outdir).get("workload", "")
    row = {"workload": workload}
    if res.get("checksum"):
        row["checksum"] = res["checksum"]
    sp = outdir / "stats.txt"
    row["roi_completed"] = roi_completed(outdir)
    censored = censored_columns(outdir)
    row.update(censored)
    if row["roi_completed"] or censored:
        # A censored run's stats block is the ROI up to the cap; roi_completed=False says so.
        st = roi_stats(outdir)
        row.update(summarize(st))
        if sp.exists():
            (outdir / ROI_STATS).write_text(json.dumps(st, indent=1, sort_keys=True) + "\n")
        if row["roi_completed"] and res and row.get("sim_sec"):
            row.update(throughput_cols(workload, res.get("work"), row["sim_sec"]))
            row["roi_sec_guest"] = res.get("roi_sec", "")
    row["guest"] = parse_tagged(text, "GUESTINFO")
    row.update(parse_tagged(text, "LINUXSTATS"))
    guest_exit = parse_tagged(text, "GUEST_EXIT").get("returncode")
    if guest_exit is not None:
        row["guest_exit_code"] = guest_exit
    if "TIERING SETUP FAILED" in text or "SWAP SETUP FAILED" in text:
        row["outcome"] = "setup_failed"
    elif censored and row.get("sim_sec"):
        row["outcome"] = "censored"
    elif (
        row["roi_completed"]
        and "checksum" in row
        and row.get("returncode") == 0
        and guest_exit in (None, 0)
    ):
        row["outcome"] = "completed"
    elif (
        read_run_info(outdir).get("config") == "A"
        and read_run_info(outdir).get("settings", {}).get("expect_oom")
        and row.get("returncode") == -9
        and (row.get("oom_kills") or 0) > 0
    ):
        row["outcome"] = "expected_oom"
    else:
        row["outcome"] = "failed"
    (outdir / FS_RESULT).write_text(json.dumps(row, indent=1, sort_keys=True) + "\n")
    return row


def accepted_report(outdir):
    """Resume only measured success, deliberate censoring, or confirmed expected OOM."""
    row = read_json(Path(outdir) / FS_RESULT)
    if "outcome" in row:
        return row["outcome"] in ("completed", "censored", "expected_oom")
    # Legacy completed results predate explicit outcomes; incomplete ones need review.
    return row.get("roi_completed") is True and row.get("returncode") == 0 and "checksum" in row


def read_run_info(outdir):
    p = Path(outdir) / RUN_INFO
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {}


def csv_row(outdir, report=None):
    """One flat CSV row for a run directory: run name, run.json fields, then the report columns.

    The report's nested `guest` entry becomes guest_* columns, one pair per NUMA node.
    """
    outdir = Path(outdir)
    report = dict(report if report is not None else json.loads((outdir / FS_RESULT).read_text()))
    info = read_run_info(outdir)
    row = {"run": outdir.name}
    for k in ("config", "workload_args", "started", "wall_sec", "exit_cause"):
        if k in info:
            row[k] = info[k]
    for k, v in (info.get("settings") or {}).items():
        row[k] = v
    guest = report.pop("guest", {}) or {}
    row.update(report)
    row["guest_kernel"] = guest.get("kernel", "")
    row["guest_mem_total_kib"] = guest.get("mem_total_kib", "")
    row["guest_swap_total_kib"] = guest.get("swap_total_kib", "")
    # The kernel's own placement mechanisms for this run (blank on runs from before they were
    # recorded; "none" for memory tiers means the kernel exposes no tiers).
    for k in ("numa_balancing", "demotion_enabled", "hmat_present"):
        v = guest.get(k)
        row[f"guest_{k}"] = "" if v is None else v
    if "memory_tiers" in guest:
        tiers = guest["memory_tiers"] or {}
        row["guest_memory_tiers"] = (
            " ".join(f"{t}:{n}" for t, n in sorted(tiers.items(), key=lambda kv: int(kv[0])))
            or "none"
        )
    for node, nd in sorted((guest.get("nodes") or {}).items(), key=lambda kv: int(kv[0])):
        row[f"guest_node{node}_cpus"] = nd.get("cpus", "")
        row[f"guest_node{node}_mem_kib"] = nd.get("mem_kib", "")
    for k in ("gem5_commit", "repo_commit"):
        if k in info:
            row[k] = info[k]
    for k, v in (info.get("host") or {}).items():
        row[k] = v
    return row


def add_to_csv(row, path=FS_CSV):
    """Merge one run's row into a results CSV (replacing an earlier row for the same run).

    Runs started in parallel finish independently, so the read-merge-write is done under a lock.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.parent / f".{path.name}.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        ResultsFile(path, key=("run",)).add(row)


def is_committed_run(outdir):
    """True for -d directories under results/fs/, the runs that are kept in git."""
    return FS_DIR.resolve() in Path(outdir).resolve().parents


def write_csv(dirs, path=FS_CSV):
    """Rebuild a results CSV from run directories that have an fs_result.json, sorted by run."""
    rows = [csv_row(d) for d in sorted(dirs, key=lambda d: Path(d).name)]
    write_rows(path, rows)
    return rows


def main(argv):
    p = argparse.ArgumentParser(
        prog="python3 -m tiered_memory.fsreport",
        description="Rebuild fs_result.json for each run directory (no gem5 needed).",
    )
    p.add_argument("dirs", nargs="+", help="gem5 -d directories")
    p.add_argument(
        "--csv",
        metavar="PATH",
        help="also write one row per directory to this CSV (replacing it), e.g. "
        "results/gem5_fs.csv",
    )
    a = p.parse_args(argv)
    done = []
    for d in a.dirs:
        if not Path(d).is_dir():
            continue
        row = collect(d)
        done.append(d)
        print(f"{d}: " + json.dumps(row, sort_keys=True))
    if a.csv:
        write_csv(done, a.csv)
        print(f"wrote {a.csv} ({len(done)} runs)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
