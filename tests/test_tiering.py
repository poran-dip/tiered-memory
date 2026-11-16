"""The guest's tiering switches (tiered_memory/tiering.py) and how the run script asks for them."""

import subprocess

import pytest

from tiered_memory import tiering
from tiered_memory.guest_script import run_script


def test_default_writes_nothing():
    assert tiering.writes("default") == []


def test_on_sets_memory_tiering_balancing_and_demotion():
    assert tiering.writes("on") == [
        (tiering.NUMA_BALANCING, "2"),
        (tiering.DEMOTION, "1"),
    ]


def test_off_turns_both_off():
    assert [v for _, v in tiering.writes("off")] == ["0", "0"]


def test_unknown_mode_is_an_error():
    with pytest.raises(ValueError):
        tiering.writes("maybe")


def test_apply_checks_every_setting_reads_back():
    state = {}
    errors = tiering.apply(
        "on", write=lambda p, v: state.__setitem__(p, v), reader=lambda p: state.get(p)
    )
    assert errors == []
    assert state[tiering.DEMOTION] == "1"


def test_a_setting_the_kernel_ignores_is_reported():
    # the kernel keeps balancing at 1 (ignoring the memory-tiering mode); demotion took
    errors = tiering.apply("on", write=lambda p, v: None, reader=lambda p: "1")
    assert len(errors) == 1 and "numa_balancing: wrote 2, reads 1" in errors[0]


def test_a_failed_write_is_reported():
    def boom(path, value):
        raise subprocess.CalledProcessError(1, "sudo")

    assert len(tiering.apply("off", write=boom, reader=lambda p: None)) == 2


def test_run_script_passes_tiering_only_when_asked():
    assert "--tiering" not in run_script("chase", ["-s", "64"])
    assert "--tiering=on" in run_script("chase", ["-s", "64"], tiering="on")
    with pytest.raises(ValueError):
        run_script("chase", [], tiering="maybe")
