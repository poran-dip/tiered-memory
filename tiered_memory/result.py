"""Parsing of the RESULT line every workload prints, plus throughput derived from it."""

import json

from .workloads import WORK_UNIT


def parse_result(text):
    """Fields of the last RESULT line in text (name, ws_mb, iters, roi_sec, work, checksum)."""
    last = None
    for line in text.splitlines():
        if line.startswith("RESULT"):
            last = line
    if last is None:
        return {}
    return dict(kv.split("=", 1) for kv in last.split()[1:] if "=" in kv)


def parse_tagged(text, tag):
    """JSON payload of the last '<tag> {...}' line in text, or {} (see tiered_memory.guest_run).

    Full-system output read from the guest's serial console (the fallback) ends lines in '\\r'.
    """
    last = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith(tag + " "):
            last = line[len(tag) + 1 :]
    if last is None:
        return {}
    try:
        return json.loads(last)
    except ValueError:
        return {}


def throughput_cols(name, work, seconds):
    """work, its unit, work/s, and ns per unit of work (ns per hop for chase = latency).

    seconds must be time for the same region the work was counted over (the ROI).
    """
    try:
        work, seconds = float(work), float(seconds)
    except (TypeError, ValueError):
        return {}
    cols = {"work": work, "work_unit": WORK_UNIT.get(name, "")}
    if seconds > 0:
        cols["throughput"] = work / seconds
    if work > 0:
        cols["ns_per_work"] = seconds * 1e9 / work
    return cols
