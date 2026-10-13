"""Physical address map for the full-system configs (A, B, C, D).

Kept free of gem5 imports so pytest can check it. The configs in configs/ turn these ranges into
gem5 memory ranges, the E820 map, and (for config B) the NUMA node description.

Everything is placed below the X86 I/O hole that starts at 3 GiB. gem5's stock X86Board only
supports memory below the hole, and the scaled sizes (512 MiB fast, 2 GiB slow) fit with room
to spare.
"""

import re
from typing import NamedTuple

MiB = 1 << 20
GiB = 1 << 30

# Start of the X86 PCI/IO hole; gem5's X86Board maps devices from here up to 4 GiB.
IO_HOLE_START = 3 * GiB

DEFAULT_FAST = "512MiB"
DEFAULT_SLOW = "2GiB"

_UNITS = {"": 1, "B": 1, "KIB": 1 << 10, "MIB": MiB, "GIB": GiB}


def parse_size(text):
    """'512MiB' -> bytes. Binary units only (KiB, MiB, GiB), matching gem5's size strings."""
    m = re.fullmatch(r"\s*(\d+)\s*([KMG]iB|B)?\s*", str(text), re.IGNORECASE)
    if not m:
        raise ValueError(f"bad size {text!r}: use e.g. 512MiB or 2GiB")
    return int(m.group(1)) * _UNITS[(m.group(2) or "").upper()]


def format_size(nbytes):
    """Bytes -> the largest exact binary unit, in gem5's spelling ('512MiB')."""
    for unit, scale in (("GiB", GiB), ("MiB", MiB), ("KiB", 1 << 10)):
        if nbytes % scale == 0:
            return f"{nbytes // scale}{unit}"
    return f"{nbytes}B"


class Region(NamedTuple):
    """One contiguous range of physical memory belonging to one NUMA node."""

    node: int
    start: int
    size: int
    tier: str  # "fast" or "slow"

    @property
    def end(self):
        return self.start + self.size


def layout(fast, slow=0):
    """Regions for `fast` bytes of node-0 DRAM followed by `slow` bytes of node-1 DRAM.

    Config A and C: layout(fast). Config B: layout(fast, slow). Node 1 has no CPUs; that is
    expressed later in the firmware tables, not here.
    """
    fast, slow = parse_size(fast), parse_size(slow)
    if fast <= 0:
        raise ValueError("fast memory size must be positive")
    if fast % MiB or slow % MiB:
        raise ValueError("sizes must be whole MiB")
    regions = [Region(0, 0, fast, "fast")]
    if slow:
        regions.append(Region(1, fast, slow, "slow"))
    if regions[-1].end > IO_HOLE_START:
        raise ValueError(
            f"total memory {format_size(regions[-1].end)} runs into the X86 I/O hole at 3GiB; "
            "the configs only place memory below it"
        )
    return regions
