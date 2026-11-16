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


def test_work_percent_shown_once_reported():
    p = Progress(wall_gap=15)
    p.work_step()  # before the ROI: ignored
    p.start(tick=0, insts=0, wall=0.0)
    assert "work" not in p.sample(tick=MS, insts=10, wall=1.0)
    for _ in range(37):
        p.work_step()
    assert p.sample(tick=2 * MS, insts=20, wall=2.0).endswith("| work 37%")
    for _ in range(80):
        p.work_step()
    assert p.work_pct == 100
    p.start(tick=3 * MS, insts=0, wall=3.0)  # a new ROI starts from unknown again
    assert p.work_pct is None


def test_work_hypercall_matches_the_workloads():
    import re
    from pathlib import Path

    from tiered_memory.progress import WORK_HYPERCALL

    common = (Path(__file__).resolve().parents[1] / "workloads/common.h").read_text()
    m = re.search(r"kHypercallRoiProgress = (\d+);", common)
    assert m and int(m.group(1)) == WORK_HYPERCALL


def test_cap_reason_sim_time_and_insts():
    p = Progress(cap_sec=2.0, cap_insts=1000)
    p.start(10**12, 500, 0.0)
    assert p.cap_reason(2 * 10**12, 900) is None
    assert p.cap_reason(3 * 10**12, 900) == "sim-time"
    assert p.cap_reason(2 * 10**12, 1600) == "insts"


def test_no_caps_means_never():
    p = Progress()
    p.start(0, 0, 0.0)
    assert p.cap_reason(10**15, 10**12) is None
    assert p.ticks_to_cap(5) is None


def test_ticks_to_cap_lands_the_next_sample_on_the_cap():
    p = Progress(cap_sec=1.0)
    p.start(10**12, 0, 0.0)
    assert p.ticks_to_cap(10**12 + 4 * 10**11) == 6 * 10**11


def test_work_steps_build_the_progress_curve_and_snapshot():
    p = Progress(cap_sec=9.0)
    p.start(0, 100, 0.0)
    p.work_step(10**12, 600)
    p.work_step(3 * 10**12, 1100)
    snap = p.snapshot(4 * 10**12, 1500, "sim-time")
    assert snap["work_pct"] == 2
    assert snap["curve"] == [[1.0, 500], [3.0, 1000]]
    assert snap["sim_sec"] == 4.0 and snap["insts"] == 1400 and snap["reason"] == "sim-time"
