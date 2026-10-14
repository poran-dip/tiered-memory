"""Drives native/sweep_ram.descend with a scripted fake run(), so no sudo or systemd is needed."""

import csv
from types import SimpleNamespace

from native import sweep_ram
from tiered_memory.csvout import ResultsFile

ARGS = {
    "seed": 1,
    "baseline_reps": 2,
    "timeout": 900,
    "timeout_mult": 30,
    "min_timeout": 20,
    "factors": [2.0, 1.0, 0.5, 0.25],
    "stop_slowdown": 1.5,
    "past_knee": 1,
}


def fake_run(roi_by_limit, calls):
    def run(binary, ws, iters, seed, limit_mb, timeout):
        calls.append((limit_mb, timeout))
        roi = roi_by_limit[limit_mb]
        if roi is None:
            return {"status": "exit-9", "wall_sec": "1.00"}
        return {"status": "ok", "wall_sec": "1.00", "roi_sec": str(roi)}

    return run


def test_descend_stops_one_run_after_the_knee(tmp_path, monkeypatch):
    calls = []
    roi = {None: 1.0, 128: 1.0, 64: 1.1, 32: 3.0, 16: 50.0}
    monkeypatch.setattr(sweep_ram, "run", fake_run(roi, calls))
    a = SimpleNamespace(out=str(tmp_path / "o.csv"), **ARGS)
    results = ResultsFile(a.out, key=("workload", "ws_mb"))
    sweep_ram.descend("bin", 64, 1, a, results)
    rows = results.new
    limits = [c[0] for c in calls]
    assert limits == [None, None, 128, 64, 32, 16]  # 2 baselines, then down to one past the knee
    knee = [r for r in rows if r["bottleneck"]]
    assert knee[0]["factor"] == 0.5 and knee[0]["slowdown"] == 3.0
    with open(a.out) as fh:
        saved = list(csv.DictReader(fh))
    assert len(saved) == len(rows) == 5  # 1 baseline + 4 limited runs


def test_descend_stops_on_failure_and_caps_timeout(tmp_path, monkeypatch):
    calls = []
    roi = {None: 1.0, 128: 1.0, 64: None}
    monkeypatch.setattr(sweep_ram, "run", fake_run(roi, calls))
    a = SimpleNamespace(out=str(tmp_path / "o.csv"), **ARGS)
    results = ResultsFile(a.out, key=("workload", "ws_mb"))
    sweep_ram.descend("bin", 64, 1, a, results)
    rows = results.new
    assert [c[0] for c in calls] == [None, None, 128, 64]
    assert calls[-1][1] == 30  # 30 x 1.00 s baseline wall
    assert rows[-1]["bottleneck"] == "exit-9"


def test_descend_reports_failed_baseline(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(sweep_ram, "run", fake_run({None: None}, calls))
    a = SimpleNamespace(out=str(tmp_path / "o.csv"), **ARGS)
    results = ResultsFile(a.out, key=("workload", "ws_mb"))
    sweep_ram.descend("bin", 64, 1, a, results)
    rows = results.new
    assert all(r["bottleneck"] == "baseline failed" for r in rows)
    assert len(calls) == 2  # never goes on to limited runs


def test_run_passes_extra_workload_args_and_records_them(monkeypatch):
    seen = {}

    def fake_subprocess_run(cmd, **kw):
        seen["cmd"] = cmd
        return SimpleNamespace(
            returncode=0,
            stdout="RESULT name=stream ws_mb=448 iters=1 roi_sec=0.5 work=100 checksum=9\n",
        )

    monkeypatch.setattr(sweep_ram.subprocess, "run", fake_subprocess_run)
    row = sweep_ram.run("bin/stream", 448, 1, 42, None, 10, ["-d", "1"])
    assert seen["cmd"][-2:] == ["-d", "1"]
    assert row["wl_args"] == "-d 1"
    assert row["iters"] == 1
    assert "host_cpu" in row
