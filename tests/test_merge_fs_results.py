"""scripts/merge_fs_results.py adds missing runs to the FS results CSV and leaves the rest alone."""

import importlib.util
import json
import shutil
from pathlib import Path

from tiered_memory.csvout import write_rows
from tiered_memory.fsreport import FS_DIR, FS_RESULT, csv_row

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/merge_fs_results.py"
spec = importlib.util.spec_from_file_location("merge_fs_results", SCRIPT)
mfr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mfr)

DONE = "b-graph-over-448"  # committed runs used as fixtures: one finished,
UNFINISHED = "c-sort-over-448"  # one whose ROI never completed


def setup(tmp_path, runs, in_csv):
    fs = tmp_path / "fs"
    fs.mkdir()
    for name, src in runs.items():
        shutil.copytree(FS_DIR / src, fs / name)
    csv = tmp_path / "gem5_fs.csv"
    write_rows(csv, [csv_row(fs / n) for n in in_csv])
    return fs, csv


def snapshot(fs):
    return {p: p.read_bytes() for p in sorted(fs.rglob("*")) if p.is_file()}


def test_nothing_new_leaves_the_csv_untouched(tmp_path):
    fs, csv = setup(tmp_path, {"x": DONE, "y": UNFINISHED}, in_csv=["x", "y"])
    before, mtime = csv.read_bytes(), csv.stat().st_mtime_ns
    assert mfr.main(["--fs-dir", str(fs), "--csv", str(csv)]) == 0
    assert csv.read_bytes() == before and csv.stat().st_mtime_ns == mtime


def test_new_run_is_appended_and_nothing_else_changes(tmp_path):
    fs, csv = setup(tmp_path, {"a": DONE, "b": UNFINISHED, "new": DONE}, in_csv=["a", "b"])
    before, dirs = csv.read_bytes(), snapshot(fs)
    mfr.main(["--fs-dir", str(fs), "--csv", str(csv)])
    after = csv.read_bytes()
    assert after.startswith(before)  # earlier rows byte-identical, same order
    added = after[len(before) :].decode()
    assert added.startswith("new,") and added.count("\n") == 1
    assert b"\r" not in after  # Unix line endings, like the committed CSV
    assert snapshot(fs) == dirs  # run directories only read, never rewritten


def test_finished_rerun_replaces_an_unfinished_row(tmp_path):
    fs, csv = setup(tmp_path, {"r": UNFINISHED, "u": UNFINISHED}, in_csv=["r", "u"])
    shutil.rmtree(fs / "r")
    shutil.copytree(FS_DIR / DONE, fs / "r")  # r was rerun and finished; u still has not
    assert [d.name for d in mfr.runs_to_add(fs, csv)] == ["r"]
    mfr.main(["--fs-dir", str(fs), "--csv", str(csv)])
    rows = {r["run"]: r for r in mfr.read_rows(csv)}
    assert rows["r"]["roi_completed"] == "True" and rows["u"]["roi_completed"] == "False"


def test_dry_run_changes_nothing(tmp_path, capsys):
    fs, csv = setup(tmp_path, {"a": DONE, "new": DONE}, in_csv=["a"])
    before = csv.read_bytes()
    mfr.main(["-n", "--fs-dir", str(fs), "--csv", str(csv)])
    assert csv.read_bytes() == before
    assert "would add new" in capsys.readouterr().out


def test_directories_without_a_result_are_skipped(tmp_path):
    fs, csv = setup(tmp_path, {"a": DONE}, in_csv=["a"])
    (fs / "in-progress").mkdir()  # a run still going: no fs_result.json yet
    (fs / "in-progress" / "gem5.log").write_text("booting\n")
    assert mfr.runs_to_add(fs, csv) == []


def test_committed_results_need_nothing_added():
    assert [d.name for d in mfr.runs_to_add()] == []
    assert json.loads((FS_DIR / DONE / FS_RESULT).read_text())["roi_completed"] is True
