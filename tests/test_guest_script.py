"""The run script handed to full-system guests (configs A-D)."""

import subprocess

import pytest

from tiered_memory.fsreport import GUEST_OUTPUT
from tiered_memory.guest_script import GUEST_DIR, SWAP_FAILED, run_script


def test_plain_run_has_no_swap_setup():
    s = run_script("chase", ["-s", "64", "-i", "1"])
    assert s.startswith("#!/bin/bash\n")
    assert f"cd {GUEST_DIR}\n" in s
    assert "python3 -m tiered_memory.guest_run chase -s 64 -i 1" in s
    assert "swapon" not in s
    assert f"gem5-bridge writefile /tmp/{GUEST_OUTPUT} {GUEST_OUTPUT}" in s


def test_swap_setup_runs_before_the_workload_and_stops_on_failure():
    s = run_script("chase", ["-s", "640"], swap_device="/dev/sdb")
    assert s.index("mkswap /dev/sdb") < s.index("swapon /dev/sdb") < s.index("guest_run chase")
    assert "sudo -n /usr/sbin/mkswap" in s  # matches the sudoers rule make_disk.sh installs
    assert SWAP_FAILED in s
    assert "exit 1" in s


def test_workload_args_are_shell_quoted():
    s = run_script("kvstore", ["-z", "0.99", "-s", "a b"])
    assert "-s 'a b'" in s


def test_numactl_options_reach_guest_run_as_one_argument():
    s = run_script("chase", ["-s", "64"], numactl="--membind=1")
    assert "guest_run --numactl=--membind=1 chase -s 64" in s
    s = run_script("chase", [], numactl="--cpunodebind=0 --membind=1")
    assert "'--numactl=--cpunodebind=0 --membind=1' chase" in s


def test_no_numactl_by_default():
    assert "--numactl" not in run_script("chase", [])


@pytest.mark.parametrize("swap", [None, "/dev/sdb"])
@pytest.mark.parametrize("numactl", [None, "--membind=1"])
def test_script_is_valid_bash(swap, numactl):
    script = run_script("chase", [], swap, numactl=numactl)
    out = subprocess.run(["bash", "-n"], input=script, text=True)
    assert out.returncode == 0
