"""Config B's PCIe link: link specs, latency presets, and what the link should cost.

Kept free of gem5 imports so pytest can check it. configs/b_proposed.py turns a LinkConfig into
gem5's PCIeMemLink (src/mem/pcie_mem_link.cc in the gem5 fork); the formulas here mirror that
model, so the expected unloaded latency and peak bandwidth of a run can be written next to its
results and checked against what the run measured (configs/link_check.py, Phase 2).

The model, per 64 B cache-line read:

  root port (rc) -> MRd TLP down the link (serialize + phy) -> device controller (dev) -> DRAM
  DRAM -> device controller (dev) -> CplD TLP up the link (serialize + phy) -> root port (rc)

Sources for the defaults are in docs/pcie-link.md.
"""

import math
import re
from typing import NamedTuple

# Transfer rate per lane (GT/s) per PCIe generation, and the line code it uses.
GT_PER_S = {1: 2.5, 2: 5.0, 3: 8.0, 4: 16.0, 5: 32.0}
LANES = (1, 2, 4, 8, 16, 32)

# Share of the link taken by ACK/NAK and flow-control DLLPs and ordered sets: 8-10% measured by
# Neugebauer et al. (SIGCOMM 2018) on Gen3 x8. PCIeMemLink's dll_overhead.
DLL_OVERHEAD = 0.08
# Per TLP: 2 B framing + 2 B sequence number + 4 B LCRC (Neugebauer et al., Sec. 3).
TLP_OVERHEAD = 8
MAX_PAYLOAD = 256  # MPS, typical (Neugebauer et al.)
MAX_READ_REQ = 512  # MRRS, typical (Neugebauer et al.)
CPL_HEADER = 12  # completion header, 3 DW

DEFAULT_LINK = "gen4x8"
DEFAULT_LATENCY = "pcie"
DEFAULT_TAGS = 256  # 8-bit extended tags
DEFAULT_CREDITS = 64
DEFAULT_HOST_QUEUE = 128


class Latency(NamedTuple):
    """Fixed latencies in ns: root port and device controller per traversal, PHY one-way."""

    rc: float
    phy: float
    dev: float


# Named latency presets. Each adds 2 * (rc + phy + dev) ns to an idle read, plus serialization.
#   pcie: a plain PCIe endpoint, the system the project asks about. ~420 ns added round trip,
#         from the 520-547 ns 64 B Gen3 x8 DMA read latency of Neugebauer et al. (SIGCOMM 2018)
#         less ~100 ns of host DRAM access inside that number.
#   cxl:  a CXL.mem ASIC expander, as a lower bound on what a purpose-built device achieves.
#         ~150 ns added, from CXL-DMSim's silicon-calibrated 284 ns vs 130 ns local DDR5
#         (arXiv 2411.02282, Table/Fig. on LMbench lat_mem_rd).
LATENCY_PRESETS = {
    "pcie": Latency(rc=100.0, phy=30.0, dev=80.0),
    "cxl": Latency(rc=35.0, phy=15.0, dev=25.0),
}


class LinkSpec(NamedTuple):
    gen: int
    lanes: int

    @property
    def name(self):
        return f"gen{self.gen}x{self.lanes}"


def parse_link(text):
    """'gen4x8' -> LinkSpec(4, 8); 'none' -> None (slow DRAM attached directly, no link)."""
    t = str(text).strip().lower()
    if t == "none":
        return None
    m = re.fullmatch(r"gen([1-5])x(\d+)", t)
    if not m or int(m.group(2)) not in LANES:
        raise ValueError(
            f"bad link {text!r}: use genNxM with N in 1-5 and M in {', '.join(map(str, LANES))} "
            "(e.g. gen4x8), or none"
        )
    return LinkSpec(int(m.group(1)), int(m.group(2)))


class LinkConfig(NamedTuple):
    """Everything config B passes to PCIeMemLink, plus the preset name for the record."""

    spec: LinkSpec
    latency: Latency
    latency_name: str  # preset name, or "custom" if any latency was overridden
    tags: int = DEFAULT_TAGS
    credits: int = DEFAULT_CREDITS
    host_queue: int = DEFAULT_HOST_QUEUE


def link_config(link, latency=DEFAULT_LATENCY, rc=None, phy=None, dev=None, **kw):
    """Build a LinkConfig from CLI-style values; None for `link` 'none'."""
    spec = parse_link(link) if not isinstance(link, LinkSpec) else link
    if spec is None:
        return None
    if latency not in LATENCY_PRESETS:
        raise ValueError(f"unknown latency preset {latency!r}: {', '.join(LATENCY_PRESETS)}")
    base = LATENCY_PRESETS[latency]
    lat = Latency(
        rc=base.rc if rc is None else float(rc),
        phy=base.phy if phy is None else float(phy),
        dev=base.dev if dev is None else float(dev),
    )
    if min(lat) < 0:
        raise ValueError("link latencies must not be negative")
    name = latency if lat == base else "custom"
    return LinkConfig(spec, lat, name, **kw)


def bytes_per_sec(spec, dll_overhead=DLL_OVERHEAD):
    """One direction's usable rate: lanes x GT/s, less line encoding and DLL overhead."""
    encoding = 8 / 10 if spec.gen <= 2 else 128 / 130
    return spec.lanes * GT_PER_S[spec.gen] * 1e9 * encoding / 8 * (1 - dll_overhead)


def request_header(addr):
    """MRd/MWr header bytes: 3 DW below 4 GiB, 4 DW above."""
    return 16 if addr >= 1 << 32 else 12


def read_wire_bytes(size=64, addr=0):
    """(request bytes downstream, completion bytes upstream) for one read of `size` bytes."""
    req_tlps = max(1, math.ceil(size / MAX_READ_REQ))
    cpl_tlps = max(1, math.ceil(size / MAX_PAYLOAD))
    req = req_tlps * (request_header(addr) + TLP_OVERHEAD)
    cpl = cpl_tlps * (CPL_HEADER + TLP_OVERHEAD) + size
    return req, cpl


def write_wire_bytes(size=64, addr=0):
    """Bytes downstream for one (posted) write of `size` bytes."""
    tlps = max(1, math.ceil(size / MAX_PAYLOAD))
    return tlps * (request_header(addr) + TLP_OVERHEAD) + size


def unloaded_read_ns(cfg, size=64):
    """Latency an idle link adds to one read, excluding the DRAM access itself."""
    req, cpl = read_wire_bytes(size)
    ser_ns = (req + cpl) / bytes_per_sec(cfg.spec) * 1e9
    lat = cfg.latency
    return 2 * (lat.rc + lat.phy + lat.dev) + ser_ns


def max_read_gbs(cfg, size=64):
    """Read payload bandwidth when the upstream (completion) direction is saturated."""
    _, cpl = read_wire_bytes(size)
    return bytes_per_sec(cfg.spec) * size / cpl / 1e9


def max_write_gbs(cfg, size=64):
    """Write payload bandwidth when the downstream direction is saturated."""
    return bytes_per_sec(cfg.spec) * size / write_wire_bytes(size) / 1e9


def settings(cfg):
    """The link's run.json settings (one CSV column each). Blank-free: 'none' for no link."""
    if cfg is None:
        return {"link": "none"}
    return {
        "link": cfg.spec.name,
        "link_latency": cfg.latency_name,
        "link_rc_ns": cfg.latency.rc,
        "link_phy_ns": cfg.latency.phy,
        "link_dev_ns": cfg.latency.dev,
        "link_tags": cfg.tags,
        "link_credits": cfg.credits,
        # what the model should show on an idle link (Phase 2 checks against these)
        "link_expect_rd_ns": round(unloaded_read_ns(cfg), 1),
        "link_expect_rd_gbs": round(max_read_gbs(cfg), 2),
    }


def variant_tag(cfg):
    """Short run-name tag for a non-default link, e.g. 'gen3x4' or 'gen4x8-cxl'; '' for default."""
    if cfg is None:
        return "nolink"
    tag = "" if cfg.spec.name == DEFAULT_LINK else cfg.spec.name
    if cfg.latency_name != DEFAULT_LATENCY:
        tag = f"{tag or cfg.spec.name}-{cfg.latency_name}"
    return tag
