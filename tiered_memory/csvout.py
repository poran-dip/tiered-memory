"""Results CSVs: merge new rows into an existing file instead of overwriting it.

Rerunning one workload replaces only that workload's earlier rows (matched on `key` columns);
everything else in the file is kept. Files are written after every run so an interrupted sweep
keeps what it measured, and are handed back to the invoking user when run under sudo.
"""

import contextlib
import csv
import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RESULTS = REPO / "results"


def read_rows(path):
    """Rows of an existing CSV as dicts of strings; [] if the file does not exist."""
    p = Path(path)
    if not p.exists():
        return []
    with open(p, newline="") as f:
        return list(csv.DictReader(f))


def _key(row, key):
    return tuple(str(row.get(k, "")) for k in key)


def merge(old, new, key):
    """old rows whose key matches no new row, then all new rows (new ones win)."""
    replaced = {_key(r, key) for r in new}
    return [r for r in old if _key(r, key) not in replaced] + list(new)


def _give_back(path):
    """Under sudo, make path (and results dirs we created) owned by the real user."""
    uid, gid = os.environ.get("SUDO_UID"), os.environ.get("SUDO_GID")
    if os.geteuid() != 0 or not uid:
        return
    p = Path(path).resolve()
    for q in [p, *(d for d in p.parents if REPO in d.parents)]:  # the file + dirs inside repo
        with contextlib.suppress(OSError):
            os.chown(q, int(uid), int(gid or uid))


def write_rows(path, rows):
    """Write rows (columns = union in first-seen order; missing cells blank)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    keys = list(dict.fromkeys(k for r in rows for k in r))
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", newline="") as f:
        # "\n", not the csv module's default "\r\n": the committed CSVs use Unix line endings, so a
        # rewrite that changes one row shows in git as one row
        wr = csv.DictWriter(f, fieldnames=keys, restval="", lineterminator="\n")
        wr.writeheader()
        wr.writerows(rows)
    os.replace(tmp, p)  # never leaves a half-written file behind
    _give_back(p)


class ResultsFile:
    """Collects this run's rows and keeps `path` = earlier rows merged with them."""

    def __init__(self, path, key, fresh=False):
        self.path, self.key = Path(path), key
        self.old = [] if fresh else read_rows(path)
        self.new = []

    def add(self, row):
        self.new.append(row)
        write_rows(self.path, merge(self.old, self.new, self.key))
