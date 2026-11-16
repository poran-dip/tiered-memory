"""Firmware performance hints, not measurements of the timing simulation.

Use a 100 ns unloaded local DRAM baseline and a 150 ns DDR3 baseline.
The slow path adds the existing PCIe model's unloaded 64-byte read latency.
Bandwidth is bounded by the single-channel DRAM rate and link payload rate.
These explicit assumptions must be calibrated before quantitative tiering claims.
"""

import math

from . import pcie


def attributes(link, slow_dram):
    dram = {
        "ddr3-1600": (150, 12800),
        "ddr3-2133": (150, 17064),
        "ddr4-2400": (100, 19200),
    }
    slow_ns, slow_mb = dram[slow_dram]
    if link is not None:
        slow_ns += pcie.unloaded_read_ns(link)
        slow_mb = min(slow_mb, pcie.max_read_gbs(link) * 1000, pcie.max_write_gbs(link) * 1000)
    if not math.isfinite(slow_ns) or not 1 <= round(slow_ns) < 65535:
        raise ValueError(
            "HMAT latency must fit a nonzero 16-bit nanosecond entry (excluding 65535)"
        )
    return {
        "hmat": True,
        "hmat_fast_ns": 100,
        "hmat_slow_ns": round(slow_ns),
        "hmat_fast_mbps": 19200,
        "hmat_slow_mbps": max(1, round(slow_mb)),
        "hmat_profile": "dram-baseline-v1",
    }
