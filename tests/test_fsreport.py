"""Full-system report built from a run directory, without gem5."""

import csv
import json
import os

from tiered_memory import fsreport
from tiered_memory.fsreport import (
    CONSOLE_FILE,
    FS_RESULT,
    GUEST_OUTPUT,
    PID_FILE,
    ROI_MARKER,
    ROI_STATS,
    RUN_INFO,
    add_to_csv,
    claim_outdir,
    collect,
    csv_row,
    write_csv,
)

STATS = """
---------- Begin Simulation Statistics ----------
simSeconds                                   0.100000
simInsts                                      1000000
board.processor.switch0.core.numCycles        3000000
board.memory.mem_ctrl.dram.readBursts           50000
board.memory.mem_ctrl.dram.writeBursts           1000
---------- End Simulation Statistics   ----------
"""

GUEST = (
    'GUESTINFO {"kernel": "6.8.0-52-generic", "mem_total_kib": 469524, "nodes": {}}\r\n'
    "RESULT name=chase ws_mb=64 iters=1 roi_sec=0.1 work=1000 checksum=7\r\n"
    'LINUXSTATS {"returncode": 0, "swap_out_pages": 0}\r\n'
)


def test_completed_run_reports_roi_guest_and_linux_columns(tmp_path):
    (tmp_path / "stats.txt").write_text(STATS)
    (tmp_path / ROI_MARKER).touch()
    (tmp_path / GUEST_OUTPUT).write_text(GUEST)
    row = collect(tmp_path, "chase")
    assert row["roi_completed"] is True
    assert row["checksum"] == "7"
    assert row["insts"] == 1000000
    assert row["ns_per_work"] == 0.1 * 1e9 / 1000
    assert row["guest"]["kernel"] == "6.8.0-52-generic"
    assert row["swap_out_pages"] == 0
    assert json.loads((tmp_path / "fs_result.json").read_text())["checksum"] == "7"
    assert (tmp_path / "roi_stats.json").exists()


def test_without_roi_marker_stats_are_not_read_as_roi(tmp_path):
    (tmp_path / "stats.txt").write_text(STATS)  # end-of-run dump of a run that died before ROI end
    (tmp_path / CONSOLE_FILE).write_text(GUEST.split("RESULT")[0])
    row = collect(tmp_path, "chase")
    assert row["roi_completed"] is False
    assert "insts" not in row
    assert "checksum" not in row
    assert row["guest"]["mem_total_kib"] == 469524


def test_console_is_the_fallback_and_workload_name_comes_from_result(tmp_path):
    (tmp_path / CONSOLE_FILE).write_text(GUEST)
    assert collect(tmp_path)["workload"] == "chase"


# --- committed runs: results/fs/<run>/ and results/gem5_fs.csv ---------------------------------

RUN_JSON = {
    "config": "D",
    "workload": "chase",
    "workload_args": "-s 64 -i 1",
    "settings": {"fast_size": "512MiB", "swap_delay": "80us"},
    "gem5_commit": "618cf87",
    "repo_commit": "abc123",
    "host": {"host_cpu": "Test CPU", "host_wsl": True},
    "started": "2026-10-14T00:00:00+00:00",
    "wall_sec": 12.5,
}

GUEST_TWO_NODES = GUEST.replace(
    '"nodes": {}',
    '"nodes": {"0": {"cpus": "0", "mem_kib": 500}, "1": {"cpus": "", "mem_kib": 2000}}',
)


def _finished_run(d, guest=GUEST):
    d.mkdir(parents=True, exist_ok=True)
    (d / "stats.txt").write_text(STATS)
    (d / ROI_MARKER).touch()
    (d / GUEST_OUTPUT).write_text(guest)
    (d / RUN_INFO).write_text(json.dumps(RUN_JSON))
    return collect(d, "chase")


def test_report_rebuilds_from_roi_stats_json_without_stats_txt(tmp_path):
    first = _finished_run(tmp_path)
    (tmp_path / "stats.txt").unlink()  # not committed
    again = collect(tmp_path, "chase")
    assert again["insts"] == first["insts"] == 1000000
    assert (tmp_path / ROI_STATS).exists()


def test_report_rebuilds_from_only_the_committed_files(tmp_path):
    first = _finished_run(tmp_path)
    for name in ("stats.txt", ROI_MARKER):  # a fresh clone has neither (results/fs/.gitignore)
        (tmp_path / name).unlink()
    again = collect(tmp_path)
    assert again["roi_completed"] is True
    assert again["insts"] == first["insts"] == 1000000
    assert again["workload"] == "chase"


def test_a_run_killed_before_the_roi_stays_unfinished_and_keeps_its_workload(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "stats.txt").write_text(STATS)  # end-of-run dump, no roi_done, no RESULT
    (tmp_path / RUN_INFO).write_text(json.dumps(RUN_JSON))
    row = collect(tmp_path)
    assert row["roi_completed"] is False
    assert "insts" not in row
    assert row["workload"] == RUN_JSON["workload"]


def test_claim_refuses_earlier_outputs_unless_overwrite(tmp_path):
    _finished_run(tmp_path)
    err = claim_outdir(tmp_path)
    assert err and "--overwrite" in err
    assert claim_outdir(tmp_path, overwrite=True) is None
    for name in (ROI_MARKER, FS_RESULT, ROI_STATS, GUEST_OUTPUT, RUN_INFO):
        assert not (tmp_path / name).exists()  # a stale roi_done would fake a finished ROI
    assert (tmp_path / PID_FILE).read_text().strip() == str(os.getpid())


def test_claim_refuses_a_directory_a_live_gem5_is_using(tmp_path, monkeypatch):
    (tmp_path / PID_FILE).write_text("4242\n")
    monkeypatch.setattr(fsreport, "_gem5_alive", lambda pid: pid == 4242)
    assert "in use" in claim_outdir(tmp_path, overwrite=True)
    monkeypatch.setattr(fsreport, "_gem5_alive", lambda pid: False)  # stale pid file
    assert claim_outdir(tmp_path) is None


def test_csv_row_flattens_run_info_guest_nodes_and_host(tmp_path):
    d = tmp_path / "d-chase-64"
    row = csv_row(d, _finished_run(d, GUEST_TWO_NODES))
    assert row["run"] == "d-chase-64"
    assert row["config"] == "D"
    assert row["workload_args"] == "-s 64 -i 1"
    assert row["swap_delay"] == "80us"
    assert row["guest_node1_mem_kib"] == 2000
    assert row["guest_node1_cpus"] == ""
    assert row["host_cpu"] == "Test CPU"
    assert row["gem5_commit"] == "618cf87"
    assert "guest" not in row


def test_csv_row_records_the_kernels_numa_policy(tmp_path):
    guest = GUEST_TWO_NODES.replace(
        '"nodes": {',
        '"numa_balancing": "1", "demotion_enabled": "false", '
        '"memory_tiers": {"4": "0-1", "1": "2"}, "nodes": {',
    )
    row = csv_row(tmp_path / "b-chase-64", _finished_run(tmp_path / "b-chase-64", guest))
    assert row["guest_numa_balancing"] == "1"
    assert row["guest_demotion_enabled"] == "false"
    assert row["guest_memory_tiers"] == "1:2 4:0-1"  # sorted by tier id


def test_csv_row_from_before_numa_policy_was_recorded_leaves_it_blank(tmp_path):
    row = csv_row(tmp_path / "a-chase-64", _finished_run(tmp_path / "a-chase-64"))
    assert row["guest_numa_balancing"] == ""
    assert "guest_memory_tiers" not in row


def test_add_to_csv_replaces_the_same_run_and_keeps_others(tmp_path):
    out = tmp_path / "gem5_fs.csv"
    add_to_csv({"run": "a-chase-64", "ns_per_work": 1}, out)
    add_to_csv({"run": "c-chase-64", "ns_per_work": 2}, out)
    add_to_csv({"run": "a-chase-64", "ns_per_work": 3}, out)
    with open(out) as fh:
        rows = {r["run"]: r["ns_per_work"] for r in csv.DictReader(fh)}
    assert rows == {"a-chase-64": "3", "c-chase-64": "2"}


def test_write_csv_rebuilds_from_run_directories(tmp_path):
    for name in ("c-chase-64", "a-chase-64"):
        _finished_run(tmp_path / name)
    out = tmp_path / "gem5_fs.csv"
    write_csv([tmp_path / "c-chase-64", tmp_path / "a-chase-64"], out)
    with open(out) as fh:
        assert [r["run"] for r in csv.DictReader(fh)] == ["a-chase-64", "c-chase-64"]


def test_only_runs_under_results_fs_are_committed(tmp_path):
    assert fsreport.is_committed_run(fsreport.FS_DIR / "a-chase-64")
    assert not fsreport.is_committed_run(tmp_path / "m5out" / "a-chase-64")
