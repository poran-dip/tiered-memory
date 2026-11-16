"""Progress lines during a full-system ROI, which otherwise prints nothing until it ends.

configs/fsboard.py schedules a gem5 tick exit, calls Progress.sample() when it fires, prints the
line and schedules the next exit Progress.interval ticks later. Kept free of gem5 imports so
pytest can check the pacing and formatting.

Work done: workloads that report it (all but sort) issue WORK_HYPERCALL once per percent of
their ROI work. fsboard.py calls Progress.work_step() for each, and progress lines show
"work N%" once the first percent is in (never for sort, which cannot report it).

Pacing: simulated time per wall-clock second varies by orders of magnitude within one run. It
crawls while the timing CPU executes the workload and races while the guest waits on a disk (each
swap request is a single long delay that is cheap to simulate). So the interval adapts: after each
sample it is set to the simulated time that the last stretch covered in `wall_gap` seconds of wall
time, clamped to [MIN_INTERVAL, MAX_INTERVAL].
"""

TICKS_PER_SEC = 10**12
# Hypercall the workloads issue once per percent of their ROI work (RoiProgress in
# workloads/common.h, kHypercallRoiProgress); configs/fsboard.py counts them into work_pct.
WORK_HYPERCALL = 100
FIRST_INTERVAL = 10**9  # 1 ms simulated before the first line
MIN_INTERVAL = 10**7  # 10 us
MAX_INTERVAL = 10**12  # 1 s


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def fmt_sim(ticks):
    """Simulated duration in the most readable unit: '850 us', '12.40 ms', '3.21 s'."""
    s = ticks / TICKS_PER_SEC
    if s < 1e-3:
        return f"{s * 1e6:.0f} us"
    if s < 1:
        return f"{s * 1e3:.2f} ms"
    return f"{s:.2f} s"


def fmt_wall(sec):
    """Wall-clock duration: '42s', '3m05s', '1h02m'."""
    sec = int(sec)
    if sec < 60:
        return f"{sec}s"
    if sec < 3600:
        return f"{sec // 60}m{sec % 60:02d}s"
    return f"{sec // 3600}h{sec % 3600 // 60:02d}m"


class Progress:
    """Tracks one ROI; the caller supplies ticks, committed instructions and wall time."""

    def __init__(self, wall_gap=15.0, max_sim_sec=None, cap_sec=None, cap_insts=None):
        self.wall_gap = wall_gap
        self.max_ticks = int(max_sim_sec * TICKS_PER_SEC) if max_sim_sec else None
        # Caps stop a ROI that is taking too long and keep what it measured (see cap_reason).
        self.cap_sec, self.cap_insts = cap_sec, cap_insts
        self.cap_ticks = int(cap_sec * TICKS_PER_SEC) if cap_sec else None
        self.active = False
        self.interval = FIRST_INTERVAL
        self.work_pct = None  # percent of the ROI's work done, once the workload reports any
        self.curve = []  # [sim s into the ROI, instructions into the ROI] at each percent of work

    def start(self, tick, insts, wall):
        self.active = True
        self.interval = FIRST_INTERVAL
        self.work_pct = None
        self.curve = []
        self.t0, self.i0, self.w0 = tick, insts, wall
        self.t_last, self.i_last, self.w_last = tick, insts, wall

    def stop(self):
        self.active = False

    def work_step(self, tick=None, insts=None):
        """The workload reached its next percent of work (one WORK_HYPERCALL).

        With the current tick and committed instructions, the point also goes on the progress
        curve, which is what lets a stopped run's total be estimated and the estimate be checked
        against runs that finished.
        """
        if self.active:
            self.work_pct = min((self.work_pct or 0) + 1, 100)
            if tick is not None and insts is not None:
                self.curve.append(
                    [round((tick - self.t0) / TICKS_PER_SEC, 9), int(insts - self.i0)]
                )

    def cap_reason(self, tick, insts):
        """'sim-time' or 'insts' once the ROI has used up a cap, else None.

        The check runs at each progress sample, so a run can overshoot a cap by up to one sample
        interval (see ticks_to_cap, which keeps the sim-time overshoot to nothing).
        """
        if self.cap_ticks is not None and tick - self.t0 >= self.cap_ticks:
            return "sim-time"
        if self.cap_insts is not None and insts - self.i0 >= self.cap_insts:
            return "insts"
        return None

    def ticks_to_cap(self, tick):
        """Ticks left before the sim-time cap (None without one), so the next sample can land on it."""
        if self.cap_ticks is None:
            return None
        return max(self.t0 + self.cap_ticks - tick, 1)

    def snapshot(self, tick, insts, reason=None):
        """What roi_capped.json / roi_progress.json record about the ROI so far."""
        return {
            "reason": reason,
            "sim_sec": round((tick - self.t0) / TICKS_PER_SEC, 9),
            "insts": int(insts - self.i0),
            "work_pct": self.work_pct,
            "cap_sec": self.cap_sec,
            "cap_insts": self.cap_insts,
            "curve": self.curve,
        }

    def over_budget(self, tick):
        """True once the ROI has used more simulated time than max_sim_sec allows."""
        return self.max_ticks is not None and tick - self.t0 > self.max_ticks

    def sample(self, tick, insts, wall, extra=""):
        """The progress line for this moment; also sets self.interval for the next sample."""
        d_tick, d_insts, d_wall = tick - self.t_last, insts - self.i_last, wall - self.w_last
        if d_wall > 0 and d_tick > 0:
            self.interval = _clamp(int(d_tick / d_wall * self.wall_gap), MIN_INTERVAL, MAX_INTERVAL)
        rate = f"{d_insts / d_wall / 1e6:.2f}M insts/s" if d_wall > 0 else "-"
        sim_per_wall = f"{fmt_sim(d_tick / d_wall)} sim per wall s" if d_wall > 0 else "-"
        line = (
            f"[{tick / TICKS_PER_SEC:.2f}s sim] ROI +{fmt_sim(tick - self.t0)}"
            f" | {fmt_wall(wall - self.w0)} wall"
            f" | {(insts - self.i0) / 1e6:.1f}M insts ({rate})"
            f" | {sim_per_wall}"
        )
        if self.work_pct is not None:
            line += f" | work {self.work_pct}%"
        if extra:
            line += f" | {extra}"
        self.t_last, self.i_last, self.w_last = tick, insts, wall
        return line
