"""Batch status read from gem5.log: stages, ROI progress lines, time left."""

from tiered_memory.batchstatus import LogWatch, batch_left_h, status_line
from tiered_memory.progress import TICKS_PER_SEC, Progress

LOG = """\
[2.71s sim] kernel booted
[9.80s sim] guest logged in, starting the run script
[10.42s sim] ROI begin: switched to the ROI CPU, stats reset
"""


def progress_line(minsts, wall):
    """A real line from tiered_memory.progress, so a format change there breaks this test."""
    p = Progress(wall_gap=15)
    p.start(tick=10 * TICKS_PER_SEC, insts=0, wall=0.0)
    return p.sample(tick=10 * TICKS_PER_SEC + 3 * 10**9, insts=int(minsts * 1e6), wall=wall)


def test_stages():
    w = LogWatch()
    assert w.stage == "booting"
    w.feed("[2.71s sim] kernel booted\n")
    assert w.stage == "booting"
    w.feed("[9.80s sim] guest logged in, starting the run script\n")
    assert w.stage == "setup"
    w.feed("[10.42s sim] ROI begin: switched to the ROI CPU, stats reset\n")
    assert w.stage == "ROI"
    w.feed("[12.00s sim] ROI end: stats dumped, back on the boot CPU\n")
    assert w.stage == "after ROI"


def test_progress_line_parsed_and_partial_lines_wait():
    w = LogWatch()
    line = progress_line(300, wall=300.0)  # 1.0M insts/s
    w.feed(LOG + line[:20])
    assert w.roi_minsts == 0  # half a line: not parsed yet
    w.feed(line[20:] + "\n")
    assert w.roi_minsts == 300.0
    assert w.rate == 1.0
    assert w.roi_sim == "3.00 ms"
    assert w.remaining_min(est_minsts=900, elapsed_min=7, est_min=16.5) == 10.0


def test_poll_reads_only_new_text(tmp_path):
    log = tmp_path / "gem5.log"
    w = LogWatch()
    w.poll(log)  # not created yet
    log.write_text(LOG)
    w.poll(log)
    assert w.stage == "ROI"
    with open(log, "a") as f:
        f.write(progress_line(120, wall=60.0) + "\n")
    w.poll(log)
    assert w.roi_minsts == 120.0
    assert w.offset == log.stat().st_size


def test_remaining_before_the_roi_uses_the_estimate():
    w = LogWatch()
    assert w.remaining_min(est_minsts=600, elapsed_min=2, est_min=11.5) == 9.5
    assert w.remaining_min(est_minsts=600, elapsed_min=30, est_min=11.5) == 1.0  # overdue


def test_status_line():
    w = LogWatch()
    w.feed(LOG + progress_line(843, wall=600.0) + "\n")
    line = status_line("b-graph-over-448", w, 725, est_minsts=1406, est_min=25)
    assert line.startswith("  b-graph-over-448  12m05s  ROI +3.00 ms sim, 843M of ~1406M insts")
    assert "(60%)" in line and line.endswith("left")
    w.feed(progress_line(2000, wall=600.0) + "\n")
    assert "past the estimate" in status_line("x", w, 725, est_minsts=1406, est_min=25)
    assert status_line("x", LogWatch(), 30, 100, 5).startswith("  x  30s  booting, ~")


def test_batch_left_fills_the_first_free_slot():
    assert batch_left_h([], [], 3) == 0
    assert batch_left_h([60, 30], [], 3) == 1.0
    # slots free at 30, 60 and 0 min; three 60-minute runs queued go to the 0, 30 and 60 slots
    assert batch_left_h([60, 30], [60, 60, 60], 3) == 2.0
