"""Parser tests on SYNTHETIC stats text (not real gem5 output): stat names follow gem5's layout,
numbers are chosen so the expected answers are easy to check by hand."""

import pytest

from tiered_memory.gem5stats import memory_controllers, roi_block, summarize

HEADER = "---------- Begin Simulation Statistics ----------\n"
FOOTER = "---------- End Simulation Statistics   ----------\n"


def block(**stats):
    return HEADER + "".join(f"{k}  {v}  # c\n" for k, v in stats.items()) + FOOTER


COMMON = {
    "simSeconds": 0.001,
    "simInsts": 2000000,
    "board.processor.cores.core.numCycles": 4000000,
    "board.cache_hierarchy.l2cache.overallMisses::total": 500,
}

ONE_TIER = {
    **COMMON,
    "board.memory.mem_ctrl.readBursts": 1000,
    "board.memory.mem_ctrl.writeBursts": 500,
    "board.memory.mem_ctrl.dram.readBursts": 1000,
    "board.memory.mem_ctrl.dram.writeBursts": 500,
    "board.memory.mem_ctrl.dram.avgMemAccLat": 50000,
}

TWO_TIER = {
    **COMMON,
    "board.memory.mem_ctrl.dram.readBursts": 600,
    "board.memory.mem_ctrl.dram.writeBursts": 200,
    "board.memory.mem_ctrl.dram.avgMemAccLat": 40000,
    "board.memory.mem_ctrl_slow.dram.readBursts": 300,
    "board.memory.mem_ctrl_slow.dram.writeBursts": 100,
    "board.memory.mem_ctrl_slow.dram.avgMemAccLat": 160000,
}


def parse(tmp_path, text):
    p = tmp_path / "stats.txt"
    p.write_text(text)
    return roi_block(p)


def test_roi_block_takes_first_block_only(tmp_path):
    st = parse(tmp_path, block(simSeconds=1.0, a=1) + block(simSeconds=9.0, a=2))
    assert st["simSeconds"] == 1.0
    assert st["a"] == 1


def test_roi_block_skips_non_numeric_and_comments(tmp_path):
    st = parse(tmp_path, HEADER + "x  notanumber  # c\n# only comment\ny  3  # c\n" + FOOTER)
    assert st == {"y": 3.0}


def test_single_controller_not_double_counted():
    ctrls = memory_controllers(ONE_TIER)
    assert list(ctrls) == ["board.memory.mem_ctrl.dram"]


def test_single_tier_has_no_remote_traffic():
    row = summarize(ONE_TIER)
    assert row["ipc"] == 0.5
    assert row["dram_rd_bursts"] == 1000
    assert row["slow_rd_bursts"] == 0
    assert row["remote_access_frac"] == 0
    assert row["pcie_bw_gbs"] == 0
    assert row["avg_mem_lat_ns"] == 50.0
    # 1500 bursts * 64 B over 1 ms = 0.096 GB/s
    assert row["mem_bw_gbs"] == pytest.approx(0.096)


def test_two_tiers_split_by_controller_name():
    row = summarize(TWO_TIER)
    assert (row["fast_rd_bursts"], row["fast_wr_bursts"]) == (600, 200)
    assert (row["slow_rd_bursts"], row["slow_wr_bursts"]) == (300, 100)
    assert row["remote_access_frac"] == pytest.approx(400 / 1200, abs=1e-4)
    assert row["fast_lat_ns"] == 40.0
    assert row["slow_lat_ns"] == 160.0
    # read-weighted: (600*40 + 300*160) / 900 = 80 ns
    assert row["avg_mem_lat_ns"] == 80.0
    assert row["pcie_rd_bytes"] == 300 * 64
    assert row["pcie_wr_bytes"] == 100 * 64
    assert row["pcie_bw_gbs"] == pytest.approx(400 * 64 / 0.001 / 1e9)


def test_custom_slow_pattern():
    row = summarize(TWO_TIER, slow_pattern="mem_ctrl\\.dram")
    assert row["slow_rd_bursts"] == 600


def test_cpu_busy_frac_only_when_idle_stat_exists():
    assert summarize(ONE_TIER)["cpu_busy_frac"] == ""
    st = {**ONE_TIER, "board.processor.cores.core.idleCycles": 1000000}
    assert summarize(st)["cpu_busy_frac"] == 0.75


def test_no_memory_stats_still_gives_cpu_columns():
    row = summarize(COMMON)
    assert row["ipc"] == 0.5
    assert "mem_bw_gbs" not in row


def test_cpu_busy_frac_from_the_timing_cpus_thread_idle_cycles():
    st = {
        **ONE_TIER,
        "board.processor.switch.core.exec_context.thread_0.numIdleCycles": 1000000,
    }
    row = summarize(st)
    assert row["cpu_busy_frac"] == round(1 - 1000000 / row["cycles"], 4)


def test_disk_dma_counts_ide_lines_through_the_io_cache():
    st = {
        **ONE_TIER,
        "board.cache_hierarchy.iocache.WriteLineReq.accesses::pc.south_bridge.ide": 128,
        "board.cache_hierarchy.iocache.ReadReq.accesses::pc.south_bridge.ide": 64,
        "board.cache_hierarchy.iocache.ReadReq.accesses::total": 64,  # not double counted
    }
    row = summarize(st)
    assert row["disk_in_bytes"] == 128 * 64
    assert row["disk_out_bytes"] == 64 * 64


def test_disk_dma_is_zero_when_idle_and_blank_without_an_io_cache():
    idle = {**ONE_TIER, "board.cache_hierarchy.iocache.tags.occupancies::total": 0.0}
    assert summarize(idle)["disk_in_bytes"] == 0
    assert summarize(ONE_TIER)["disk_in_bytes"] == ""
