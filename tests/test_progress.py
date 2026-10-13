"""Progress lines printed during a full-system ROI."""

from tiered_memory.progress import (
    FIRST_INTERVAL,
    MAX_INTERVAL,
    MIN_INTERVAL,
    TICKS_PER_SEC,
    Progress,
    fmt_sim,
    fmt_wall,
)

MS = TICKS_PER_SEC // 1000


def test_formatting():
    assert fmt_sim(850 * 10**6) == "850 us"
    assert fmt_sim(12_400 * 10**6) == "12.40 ms"
    assert fmt_sim(3_210 * MS) == "3.21 s"
    assert fmt_wall(42) == "42s"
    assert fmt_wall(185) == "3m05s"
    assert fmt_wall(3720) == "1h02m"


def test_sample_reports_progress_since_roi_begin():
    p = Progress(wall_gap=15)
    p.start(tick=5 * TICKS_PER_SEC, insts=1_000, wall=100.0)
    assert p.active and p.interval == FIRST_INTERVAL
    line = p.sample(tick=5 * TICKS_PER_SEC + 2 * MS, insts=3_001_000, wall=110.0, extra="swap x")
    assert line.startswith("[5.00s sim] ROI +2.00 ms")
    assert "10s wall" in line
    assert "3.0M insts (0.30M insts/s)" in line
    assert line.endswith("| swap x")


def test_interval_adapts_to_wall_gap_and_is_clamped():
    p = Progress(wall_gap=15)
    p.start(tick=0, insts=0, wall=0.0)
    p.sample(tick=2 * MS, insts=0, wall=10.0)  # 0.2 ms sim per wall second
    assert p.interval == 3 * MS  # 15 s of wall at that rate
    p.sample(tick=2 * MS + 10 * TICKS_PER_SEC, insts=0, wall=11.0)  # disk waits: sim races ahead
    assert p.interval == MAX_INTERVAL
    p.sample(tick=2 * MS + 10 * TICKS_PER_SEC + 1, insts=0, wall=1000.0)
    assert p.interval == MIN_INTERVAL


def test_budget_and_stop():
    p = Progress(wall_gap=15, max_sim_sec=1)
    p.start(tick=10 * TICKS_PER_SEC, insts=0, wall=0.0)
    assert not p.over_budget(10 * TICKS_PER_SEC + TICKS_PER_SEC)
    assert p.over_budget(10 * TICKS_PER_SEC + TICKS_PER_SEC + 1)
    p.stop()
    assert not p.active


def test_no_budget_without_max_sim_sec():
    p = Progress()
    p.start(tick=0, insts=0, wall=0.0)
    assert not p.over_budget(10**20)
