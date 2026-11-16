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

import json
import math
import re
import shlex
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
    return v if math.isfinite(v) and v > 0 else None


def completed(row):
    """A finished ROI with a measured simulated time (a censored run never sets a reference)."""
    return (
        str(row.get("roi_completed")).lower() == "true"
        and _num(row, "sim_sec") is not None
        and row.get("outcome") in (None, "", "completed")
        and str(row.get("returncode", 0)) == "0"
    )


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


def cap_options(
    cfg,
    wl,
    tier,
    rows,
    spec=DEFAULT_SPEC,
    *,
    workload_args=None,
    reference_names=None,
    identity=None,
    provenance=False,
):
    """gem5 config options for this run's caps; [] when it has none (fits tier, A and C).

    Raises MissingReference when the cap needs a reference run that has not finished: running D
    before B has finished would silently drop its cap.
    """
    if tier != "over" or cfg in ("a", "c"):
        return []
    if any(not math.isfinite(v) or v < 0 for v in spec):
        raise ValueError("cap multipliers must be finite and nonnegative")
    if workload_args is not None:
        rows = [r for r in rows if shlex.split(r.get("workload_args", "")) == list(workload_args)]
    if identity:
        rows = [r for r in rows if all(str(r.get(k, "")) == str(v) for k, v in identity.items())]
    if reference_names is not None:
        rows = [r for r in rows if r.get("run") in reference_names]
        complete = {r.get("run") for r in rows if completed(r)}
        missing = set(reference_names) - complete
        if missing:
            raise MissingReference(
                "unfinished or incompatible references: " + ", ".join(sorted(missing))
            )
    opts = []
    used = []
    if cfg == "d":
        if spec.d_x_b:
            bs = b_rows(rows, wl, tier)
            if not bs:
                raise MissingReference(f"no finished config B run for {wl}/{tier} to set D's cap")
            opts += ["--roi-cap-sec", f"{spec.d_x_b * max(_num(r, 'sim_sec') for r in bs):.6g}"]
            used.extend(bs)
        if spec.d_insts_x_c:
            c = c_row(rows, wl, tier)
            insts = _num(c, "insts") if c else None
            if insts is None:
                raise MissingReference(
                    f"no finished config C run for {wl}/{tier} (instruction cap)"
                )
            opts += ["--roi-cap-insts", f"{spec.d_insts_x_c * insts:.0f}"]
            used.append(c)
    elif cfg == "b" and spec.b_x_c:
        c = c_row(rows, wl, tier)
        sec = _num(c, "sim_sec") if c else None
        if sec is None:
            raise MissingReference(f"no finished config C run for {wl}/{tier} to set B's cap")
        opts += ["--roi-cap-sec", f"{spec.b_x_c * sec:.6g}"]
        used.append(c)
    if opts and provenance:
        keys = (
            "run",
            "sim_sec",
            "insts",
            "workload_args",
            "kernel_sha256",
            "repo_commit",
            "gem5_commit",
        )
        refs = [{k: r.get(k, "") for k in keys} for r in used]
        opts += ["--cap-references", json.dumps(refs, sort_keys=True)]
    return opts
