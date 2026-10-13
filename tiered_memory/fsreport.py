"""Report for one finished gem5 full-system run, from its output directory alone.

configs/fsboard.py calls collect() when a run ends. It can also be rerun on any old run without
gem5, e.g. after adding a metric:

    python3 -m tiered_memory.fsreport m5out/a-chase

Inputs in the run directory:
  guest_output.txt         the guest run script's stdout, copied to the host with gem5's writefile
                           at the end of the run (complete, unlike the console file, which gem5
                           only flushes when it exits)
  board.pc.com_1.device    the guest serial console; fallback when guest_output.txt is missing
  stats.txt                block 1 is the ROI only if the ROI completed (see roi_completed)
  roi_done                 empty marker written when the ROI's stats were dumped
"""

import json
import sys
from pathlib import Path

from .gem5stats import roi_block, summarize
from .result import parse_result, parse_tagged, throughput_cols

GUEST_OUTPUT = "guest_output.txt"
CONSOLE_FILE = "board.pc.com_1.device"
ROI_MARKER = "roi_done"  # empty file written by the ROI-end handler


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
        st = roi_block(sp)
        row.update(summarize(st))
        (outdir / "roi_stats.json").write_text(json.dumps(st, indent=1, sort_keys=True))
        if res and row.get("sim_sec"):
            row.update(throughput_cols(workload, res.get("work"), row["sim_sec"]))
            row["roi_sec_guest"] = res.get("roi_sec", "")
    row["guest"] = parse_tagged(text, "GUESTINFO")
    row.update(parse_tagged(text, "LINUXSTATS"))
    (outdir / "fs_result.json").write_text(json.dumps(row, indent=1, sort_keys=True))
    return row


def main(argv):
    if not argv:
        print("usage: python3 -m tiered_memory.fsreport <run dir> [<run dir> ...]", file=sys.stderr)
        return 2
    for d in argv:
        row = collect(d)
        print(f"{d}: " + json.dumps(row, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
