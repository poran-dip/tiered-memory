"""Guest-side runner output round-trips through the host-side parsers.

Runs on the host against the native binaries: the runner only needs /proc and /sys, which exist
on any Linux machine, so its output format is checked without booting gem5.
"""

from pathlib import Path

import pytest

from tiered_memory import guest_run
from tiered_memory.result import parse_result, parse_tagged

NATIVE = Path(__file__).resolve().parents[1] / "workloads/build/native"


def test_parse_tagged_takes_last_line_and_strips_console_cr():
    text = 'GUESTINFO {"a": 1}\r\nnoise\r\nGUESTINFO {"a": 2}\r\n'
    assert parse_tagged(text, "GUESTINFO") == {"a": 2}


def test_parse_tagged_missing_or_broken_is_empty():
    assert parse_tagged("RESULT name=x\n", "LINUXSTATS") == {}
    assert parse_tagged("LINUXSTATS {not json\n", "LINUXSTATS") == {}


def test_usage_without_workload(capsys):
    assert guest_run.main([]) == 2


def test_runner_output_parses(monkeypatch, capsys):
    if not (NATIVE / "chase").exists():
        pytest.skip("native workloads not built (make -C workloads native)")
    monkeypatch.setattr(guest_run, "BIN_DIR", NATIVE)
    assert guest_run.main(["chase", "-s", "2", "-i", "1"]) == 0
    out = capsys.readouterr().out
    assert parse_result(out)["name"] == "chase"
    info = parse_tagged(out, "GUESTINFO")
    assert info["mem_total_kib"] > 0
    stats = parse_tagged(out, "LINUXSTATS")
    assert stats["returncode"] == 0
    assert stats["wall_sec_guest"] > 0
    assert "user_sec" in stats
