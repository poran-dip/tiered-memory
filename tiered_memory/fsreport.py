"""Report for one finished gem5 full-system run, from its output directory alone.

configs/fsboard.py calls collect() when a run ends. It can also be rerun on any old run without
gem5, e.g. after adding a metric:

    python3 -m tiered_memory.fsreport results/fs/a-chase-64
    python3 -m tiered_memory.fsreport --csv results/gem5_fs.csv results/fs/*   # rebuild the CSV

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
import sys
from pathlib import Path

from .csvout import RESULTS, ResultsFile, write_rows
from .gem5stats import roi_block, summarize
from .result import parse_result, parse_tagged, throughput_cols

GUEST_OUTPUT = "guest_output.txt"
CONSOLE_FILE = "board.pc.com_1.device"
ROI_MARKER = "roi_done"  # empty file written by the ROI-end handler
ROI_STATS = "roi_stats.json"
FS_RESULT = "fs_result.json"
RUN_INFO = "run.json"
PID_FILE = "gem5.pid"  # written while a gem5 process owns the directory

FS_DIR = RESULTS / "fs"  # committed full-system runs, one directory each
FS_CSV = RESULTS / "gem5_fs.csv"

# Outputs of an earlier run that would be mistaken for this run's if left in place: a stale
# roi_done in particular makes an unfinished run look complete.
STALE_OUTPUTS = (ROI_MARKER, ROI_STATS, FS_RESULT, GUEST_OUTPUT, RUN_INFO, "stats.txt")


def roi_completed(outdir):
    """True if the run reached ROI end: configs/fsboard.py writes ROI_MARKER right after the dump.

    Without it, block 1 of stats.txt is gem5's end-of-run dump of the whole run, not the ROI.
    """
    return (Path(outdir) / ROI_MARKER).exists()


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
    res = parse_result(text)
    workload = workload or res.get("name", "")
    row = {"workload": workload}
    if res.get("checksum"):
        row["checksum"] = res["checksum"]
    sp = outdir / "stats.txt"
    row["roi_completed"] = roi_completed(outdir)
    if row["roi_completed"]:
        st = roi_stats(outdir)
        row.update(summarize(st))
        if sp.exists():
            (outdir / ROI_STATS).write_text(json.dumps(st, indent=1, sort_keys=True))
        if res and row.get("sim_sec"):
            row.update(throughput_cols(workload, res.get("work"), row["sim_sec"]))
            row["roi_sec_guest"] = res.get("roi_sec", "")
    row["guest"] = parse_tagged(text, "GUESTINFO")
    row.update(parse_tagged(text, "LINUXSTATS"))
    (outdir / FS_RESULT).write_text(json.dumps(row, indent=1, sort_keys=True))
    return row


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
