"""Run naming and config options of configs/run_all_working_sets.py (no gem5 needed)."""

import argparse
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "configs/run_all_working_sets.py"
spec = importlib.util.spec_from_file_location("run_all_working_sets", SCRIPT)
ra = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ra)


def args(**kw):
    base = {
        "tiers": ["fits"],
        "configs": ["a"],
        "workloads": ["chase"],
        "only": None,
        "b_links": ["gen4x8"],
        "b_latency": ["pcie"],
        "overwrite": False,
        "gem5_args": "",
    }
    return argparse.Namespace(**{**base, **kw})


def test_a_c_d_names_unchanged():
    runs = ra.plan(args(configs=["a", "c", "d"]))
    ws = ra.FS_TIERS["chase"]["fits"].ws
    assert [r[0] for r in runs] == [f"{c}-chase-fits-{ws}" for c in "acd"]
    assert all(r[6] == [] for r in runs)


def test_b_variants_and_names():
    runs = ra.plan(
        args(configs=["b"], b_links=["gen4x8", "gen3x4", "none"], b_latency=["pcie", "cxl"])
    )
    ws = ra.FS_TIERS["chase"]["fits"].ws
    names = {r[0]: r[6] for r in runs}
    assert names == {
        f"b-chase-fits-{ws}": ["--link", "gen4x8", "--link-latency", "pcie"],
        f"b_gen4x8-cxl-chase-fits-{ws}": ["--link", "gen4x8", "--link-latency", "cxl"],
        f"b_gen3x4-chase-fits-{ws}": ["--link", "gen3x4", "--link-latency", "pcie"],
        f"b_gen3x4-cxl-chase-fits-{ws}": ["--link", "gen3x4", "--link-latency", "cxl"],
        f"b_nolink-chase-fits-{ws}": ["--link", "none"],
    }


def test_gem5_cmd_puts_config_options_before_workload_args():
    a = args()
    cmd = ra.gem5_cmd("b-x", "b", "chase", ["-s", "64"], a, ["--link", "gen3x4"])
    assert cmd[cmd.index("--link") + 1] == "gen3x4"
    assert cmd.index("--link") < cmd.index("--") < cmd.index("-s")


def test_print_status_reads_each_running_log(tmp_path, capsys):
    log = tmp_path / "gem5.log"
    log.write_text("[9.80s sim] guest logged in, starting the run script\n")
    with open(log, "a") as f:
        name = "b-chase-fits-64"
        running = {name: (None, ra.time.monotonic() - 120, f)}
        watches = {name: (ra.LogWatch(), 6, ra.estimate_min("b", "chase", "fits"))}
        queued = ra.plan(args(configs=["a"]))
        ra.print_status(running, watches, queued, 4, args(jobs=2))
    out = capsys.readouterr().out.splitlines()
    assert "1 running, 1 queued, 4 finished" in out[0]
    assert out[1].startswith("  b-chase-fits-64  2m0") and "setup" in out[1]


def test_estimate_minsts_includes_d_paging():
    tier = ra.FS_TIERS["stream"]["over"]
    assert ra.estimate_minsts("a", "stream", "over") == 0
    assert ra.estimate_minsts("c", "stream", "over") == tier.est_minsts
    assert ra.estimate_minsts("d", "stream", "over") > tier.est_minsts
