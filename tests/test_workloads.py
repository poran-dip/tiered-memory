"""Workload registry matches the Makefile; built workloads are deterministic and seed-sensitive."""

import re
import subprocess
from pathlib import Path

import pytest

from tiered_memory.result import parse_result
from tiered_memory.workloads import FS_TIERS, NAMES, TIERS, WORK_UNIT

REPO = Path(__file__).resolve().parents[1]
BIN = REPO / "workloads/build/native"


def makefile_names():
    text = (REPO / "workloads/Makefile").read_text()
    return re.search(r"^WL\s*=\s*(.+)$", text, re.M).group(1).split()


def test_registry_matches_makefile():
    assert sorted(makefile_names()) == sorted(NAMES)


def test_every_workload_has_fs_tiers():
    assert sorted(FS_TIERS) == sorted(NAMES)
    for name, tiers in FS_TIERS.items():
        assert set(tiers) == set(TIERS), name
        assert tiers["fits"] and tiers["fits"].ws <= 64, name  # every workload has a sanity size
        if tiers["near"] and tiers["over"]:
            assert tiers["fits"].ws < tiers["near"].ws < tiers["over"].ws, name


def test_every_workload_has_a_source_file():
    for name in NAMES:
        assert (REPO / f"workloads/{name}.cpp").exists(), name


def run(name, *args):
    exe = BIN / name
    if not exe.exists():
        pytest.skip(f"{exe} not built (make -C workloads native)")
    out = subprocess.run([str(exe), *args], capture_output=True, text=True, check=True, timeout=60)
    return parse_result(out.stdout)


@pytest.mark.parametrize("name", NAMES)
def test_deterministic_per_seed(name):
    args = ["-s", "2", "-i", "1"]
    a, b = run(name, *args, "-r", "1"), run(name, *args, "-r", "1")
    assert a["checksum"] == b["checksum"]
    assert a["work"] == b["work"]
    assert float(a["work"]) > 0
    assert WORK_UNIT[name]


# gemm's checksum is a rounded sum the seed barely moves; chase visits every node once per cycle,
# so its sum of node indices is the same for any permutation.
@pytest.mark.parametrize("name", [n for n in NAMES if n not in ("gemm", "chase")])
def test_seed_changes_the_data(name):
    args = ["-s", "2", "-i", "1"]
    assert run(name, *args, "-r", "1")["checksum"] != run(name, *args, "-r", "2")["checksum"]
