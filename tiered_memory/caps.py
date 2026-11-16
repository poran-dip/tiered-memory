"""ROI caps for runs that may take far longer than the configs they are compared with.

A cap stops the ROI early and keeps its stats (configs/fsboard.py: --roi-cap-sec / --roi-cap-insts),
so a run that would take days is reported as "at least this much slower" with a progress-based
estimate of its full ROI, instead of being killed by a wall-clock limit that records nothing.

Caps are derived from rows already in the dataset's CSV, so the order of the batches matters:

  config D (over tier)  cap_sec   = d_x_b * the longest ROI of any config B variant, same run
                        cap_insts = d_insts_x_c * config C's ROI instructions
  config B (over tier)  cap_sec   = b_x_c * config C's ROI

Pure Python (no gem5), so pytest can check it.
"""

import re
from typing import NamedTuple


class CapSpec(NamedTuple):
    d_x_b: float = 5.0  # D's cap: this many times the longest B ROI
    d_insts_x_c: float = 100.0  # D's instruction cap: this many times C's ROI instructions
    b_x_c: float = 20.0  # B's cap: this many times C's ROI (0 = no cap on B)


def _num(row, key):
    try:
        v = float(row.get(key, ""))
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def completed(row):
    """A finished ROI with a measured simulated time (a censored run never sets a reference)."""
    return str(row.get("roi_completed")).lower() == "true" and _num(row, "sim_sec") is not None


def run_name(prefix, wl, tier, ws):
    return f"{prefix}-{wl}-{tier}-{ws}"


def b_rows(rows, wl, tier):
    """Completed config B rows for one workload and tier (every link variant, tiering on or off)."""
    pat = re.compile(rf"^b(_[^-]+)?(-[a-z]+)*-{re.escape(wl)}-{tier}-\d+$")
    return [r for r in rows if pat.match(r.get("run", "")) and completed(r)]


def c_row(rows, wl, tier):
    pat = re.compile(rf"^c-{re.escape(wl)}-{tier}-\d+$")
    for r in rows:
        if pat.match(r.get("run", "")) and completed(r):
            return r
    return None


class MissingReference(Exception):
    """A cap needs a finished run that is not in the CSV yet."""


DEFAULT_SPEC = CapSpec()


def cap_options(cfg, wl, tier, rows, spec=DEFAULT_SPEC):
    """gem5 config options for this run's caps; [] when it has none (fits tier, A and C).

    Raises MissingReference when the cap needs a reference run that has not finished: running D
    before B has finished would silently drop its cap.
    """
    if tier != "over" or cfg in ("a", "c"):
        return []
    opts = []
    if cfg == "d":
        if spec.d_x_b:
            bs = b_rows(rows, wl, tier)
            if not bs:
                raise MissingReference(f"no finished config B run for {wl}/{tier} to set D's cap")
            opts += ["--roi-cap-sec", f"{spec.d_x_b * max(_num(r, 'sim_sec') for r in bs):.6g}"]
        if spec.d_insts_x_c:
            c = c_row(rows, wl, tier)
            insts = _num(c, "insts") if c else None
            if insts is None:
                raise MissingReference(
                    f"no finished config C run for {wl}/{tier} (instruction cap)"
                )
            opts += ["--roi-cap-insts", f"{spec.d_insts_x_c * insts:.0f}"]
    elif cfg == "b" and spec.b_x_c:
        c = c_row(rows, wl, tier)
        sec = _num(c, "sim_sec") if c else None
        if sec is None:
            raise MissingReference(f"no finished config C run for {wl}/{tier} to set B's cap")
        opts += ["--roi-cap-sec", f"{spec.b_x_c * sec:.6g}"]
    return opts
