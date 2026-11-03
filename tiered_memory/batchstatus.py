"""Status of the gem5 runs a batch has in flight, read from each run's gem5.log.

configs/run_all_working_sets.py starts gem5 with its output in <outdir>/gem5.log. The configs print
a line at each stage (configs/fsboard.py) and, during the ROI, a progress line about every
--progress-sec wall seconds (tiered_memory/progress.py). LogWatch follows one log by reading only
what was appended since the last poll; status_line() turns that into one line per run. Kept free of
gem5 imports so pytest can check the parsing.
"""

import re

from tiered_memory.progress import fmt_wall

# Stages in the order a run passes through them, with the log text that starts each one.
STAGES = (
    ("booting", None),
    ("setup", "guest logged in"),
    ("ROI", "ROI begin"),
    ("after ROI", "ROI end"),
)
_PROGRESS = re.compile(
    r"^\[[\d.]+s sim\] ROI \+(?P<sim>[\d.]+ \w+) \| (?P<wall>[^|]+) wall"
    r" \| (?P<insts>[\d.]+)M insts \((?P<rate>[\d.]+)M insts/s\)"
)
_WORK = re.compile(r"\| work (?P<pct>\d+)%")
_WALL = re.compile(r"^(?:(?P<h>\d+)h)?(?:(?P<m>\d+)m)?(?:(?P<s>\d+)s)?$")


def parse_wall(text):
    """Seconds from fmt_wall's '42s', '3m05s' or '1h02m'."""
    m = _WALL.match(text.strip())
    if not m or not any(m.groups()):
        raise ValueError(f"not a wall time: {text!r}")
    return int(m["h"] or 0) * 3600 + int(m["m"] or 0) * 60 + int(m["s"] or 0)


class LogWatch:
    """Follows one gem5.log; feed() it the text appended since the last call."""

    def __init__(self):
        self.offset = 0  # bytes of the log already read
        self.stage = "booting"
        self.roi_sim = None  # simulated time into the ROI, as printed ("12.40 ms")
        self.roi_minsts = 0.0  # millions of instructions since ROI begin
        self.rate = None  # recent simulation speed, millions of instructions per wall second
        self.roi_wall = None  # wall seconds since ROI begin, at the last progress line
        self.work_pct = None  # percent of the ROI's work the workload reports done
        self._partial = ""

    def feed(self, text):
        lines = (self._partial + text).split("\n")
        self._partial = lines.pop()  # an unfinished last line waits for the next poll
        for line in lines:
            for stage, marker in STAGES[1:]:
                if marker in line:
                    self.stage = stage
            m = _PROGRESS.match(line)
            if m:
                self.stage = "ROI"
                self.roi_sim = m["sim"]
                self.roi_minsts = float(m["insts"])
                self.rate = float(m["rate"])
                self.roi_wall = parse_wall(m["wall"])
                w = _WORK.search(line)
                self.work_pct = int(w["pct"]) if w else None

    def poll(self, path):
        """Read whatever was appended to the log at `path` since the last poll."""
        try:
            with open(path, "rb") as f:
                f.seek(self.offset)
                data = f.read()
        except FileNotFoundError:
            return
        self.offset += len(data)
        self.feed(data.decode(errors="replace"))

    def remaining_min(self, est_minsts, elapsed_min, est_min):
        """Rough minutes left in the run.

        In the ROI: from the work the workload reports done, at the average pace so far; before
        its first percent (or for sort, which reports none), from the instruction estimate at the
        current speed. Before the ROI: the upfront estimate minus the time so far.
        """
        if self.stage == "ROI" and self.work_pct and self.roi_wall:
            return self.roi_wall * (100 - self.work_pct) / self.work_pct / 60
        if self.stage == "ROI" and self.rate:
            return max(est_minsts - self.roi_minsts, 0) / self.rate / 60
        if self.stage == "after ROI":
            return 0.5
        return max(est_min - elapsed_min, 1.0)


def status_line(name, watch, elapsed_sec, est_minsts, est_min):
    """'  b-graph-over-448  12m05s  ROI +3.21 s sim, work 58%, 843M insts, ~8m left'.

    Without a work percentage yet: '..., 843M of ~1406M insts (60%), ~9m left' (estimated).
    """
    left = watch.remaining_min(est_minsts, elapsed_sec / 60, est_min)
    parts = [watch.stage]
    if watch.stage == "ROI" and watch.roi_sim and watch.work_pct is not None:
        parts = [f"ROI +{watch.roi_sim} sim, work {watch.work_pct}%, {watch.roi_minsts:.0f}M insts"]
    elif watch.stage == "ROI" and watch.roi_sim:
        pct = watch.roi_minsts / est_minsts * 100 if est_minsts else 0
        pct_s = f"{pct:.0f}%" if pct <= 100 else ">100%, past the estimate"
        parts = [
            f"ROI +{watch.roi_sim} sim, {watch.roi_minsts:.0f}M of ~{est_minsts}M insts ({pct_s})"
        ]
    return f"  {name}  {fmt_wall(elapsed_sec)}  {', '.join(parts)}, ~{fmt_wall(left * 60)} left"


def batch_left_h(running_left_min, queued_min, jobs):
    """Hours until the batch ends: queued runs fill whichever slot frees up first."""
    slots = sorted(running_left_min) + [0.0] * max(jobs - len(running_left_min), 0)
    slots = slots[: max(jobs, 1)] or [0.0]
    for m in queued_min:
        i = slots.index(min(slots))
        slots[i] += m
    return max(slots) / 60
