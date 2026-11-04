#!/usr/bin/env python3
"""Add full-system runs that are missing from results/gem5_fs.csv, without rewriting the rest.

Runs made on another machine (or another branch) arrive as results/fs/<run>/ directories only.
After merging those in, this adds each one's row to the CSV:

  python3 scripts/merge_fs_results.py        # add the missing rows, print what was added
  python3 scripts/merge_fs_results.py -n     # only list what would be added

A run gets a row when it is not in the CSV yet, or when its CSV row is from an unfinished ROI and
its fs_result.json now has a finished one (a rerun). Existing rows, their order and every run
directory stay as they are: only fs_result.json and run.json are read, so the guest details a
finished run recorded are never lost. That is the difference from
`python3 -m tiered_memory.fsreport --csv ...`, which rebuilds both the CSV and each run's
fs_result.json from what the directory holds now.
"""

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tiered_memory.csvout import read_rows  # noqa: E402
from tiered_memory.fsreport import FS_CSV, FS_DIR, FS_RESULT, add_to_csv, csv_row  # noqa: E402


def runs_to_add(fs_dir=FS_DIR, csv_path=FS_CSV):
    """Run directories under fs_dir whose row is missing from csv_path, or is replaced by a rerun."""
    have = {r["run"]: r for r in read_rows(csv_path)}
    out = []
    for d in sorted(Path(fs_dir).iterdir()):
        if not (d / FS_RESULT).is_file():
            continue
        finished = bool(json.loads((d / FS_RESULT).read_text()).get("roi_completed"))
        old = have.get(d.name)
        if old is None or (old.get("roi_completed") != "True" and finished):
            out.append(d)
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("-n", "--dry-run", action="store_true", help="list the runs, change nothing")
    p.add_argument("--fs-dir", type=Path, default=FS_DIR, help="run directories (%(default)s)")
    p.add_argument("--csv", type=Path, default=FS_CSV, help="CSV to add rows to (%(default)s)")
    a = p.parse_args(argv)
    todo = runs_to_add(a.fs_dir, a.csv)
    for d in todo:
        if not a.dry_run:
            add_to_csv(csv_row(d), a.csv)
        print(f"{'would add' if a.dry_run else 'added'} {d.name}")
    print(f"{len(todo)} run(s) {'to add' if a.dry_run else 'added'} to {a.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
