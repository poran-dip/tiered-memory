"""Full-system report built from a run directory, without gem5."""

import json

from tiered_memory.fsreport import CONSOLE_FILE, GUEST_OUTPUT, ROI_MARKER, collect

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
