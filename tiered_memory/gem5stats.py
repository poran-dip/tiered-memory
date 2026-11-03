"""Turn the ROI block of a gem5 stats.txt into the report columns.

Memory controllers are told apart by their stat path, so a two-tier config only has to name its
slow controller to match SLOW_TIER_PATTERN (e.g. "mem_ctrl_slow"). With a single controller,
every slow_* column is 0, which is the correct answer for an all-local system. A PCIe link in
front of the slow tier (the gem5 fork's PCIeMemLink) adds the link_* columns; runs without one
get none of those keys.
"""

import re
from contextlib import suppress

from .schema import BURST_BYTES, SLOW_TIER_PATTERN, TICKS_PER_NS, TICKS_PER_SEC

_READ_BURSTS = re.compile(r"^(?P<prefix>.+)\.readBursts$")


def roi_block(path):
    """First stats block of stats.txt (the ROI) as {stat name: float}.

    common.h resets stats at ROI begin and dumps at ROI end, so block 1 is the ROI; the trailing
    block is post-ROI teardown and is ignored.
    """
    stats, started = {}, False
    with open(path) as fh:
        for line in fh:
            if line.startswith("---------- Begin"):
                if started:
                    break
                started = True
                continue
            if line.startswith("---------- End"):
                break
            fields = line.split()
            if started and len(fields) >= 2 and not line.startswith("#"):
                with suppress(ValueError):
                    stats[fields[0]] = float(fields[1])
    return stats


def memory_controllers(st):
    """{stat path prefix: {rd, wr, lat_ps}} for each DRAM/memory interface in the stats."""
    found = {}
    for key in st:
        m = _READ_BURSTS.match(key)
        if m and f"{m['prefix']}.writeBursts" in st:
            p = m["prefix"]
            found[p] = {
                "rd": st[key],
                "wr": st[f"{p}.writeBursts"],
                "lat_ps": st.get(f"{p}.avgMemAccLat"),
            }
    # mem_ctrl and mem_ctrl.dram both report bursts; keep only the deepest so nothing double counts
    return {p: v for p, v in found.items() if not any(q.startswith(p + ".") for q in found)}


_IDLE = (".idleCycles", ".numIdleCycles")

# The IDE controller's DMA goes through the I/O cache in whole cache lines: a WriteLineReq per
# line read from disk into memory, a ReadReq per line read from memory to go to disk.
_IDE_IN = re.compile(r"iocache\.WriteLineReq\.accesses::.*\bide$")
_IDE_OUT = re.compile(r"iocache\.ReadReq\.accesses::.*\bide$")
CACHE_LINE = 64


def disk_dma(st):
    """Bytes the IDE disks moved during the ROI: disk -> memory (in) and memory -> disk (out).

    Counted at the I/O cache, so it is the ROI alone, unlike the guest's /proc/vmstat counters,
    which cover the whole process. In config D, during the ROI, this is swap traffic. Blank when
    the stats have no I/O cache (SE mode); 0 when the disks were idle.
    """
    if not any(k.startswith(("board.cache_hierarchy.iocache.", "system.iocache.")) for k in st):
        return {"disk_in_bytes": "", "disk_out_bytes": ""}
    lines_in = sum(v for k, v in st.items() if _IDE_IN.search(k))
    lines_out = sum(v for k, v in st.items() if _IDE_OUT.search(k))
    return {
        "disk_in_bytes": int(lines_in * CACHE_LINE),
        "disk_out_bytes": int(lines_out * CACHE_LINE),
    }


_LINK = re.compile(r"^(?P<prefix>.+)\.downWireBytes$")


def pcie_links(st):
    """Stat path prefixes of the PCIeMemLinks (gem5 fork) in the stats; empty if none."""
    return sorted(m["prefix"] for k in st if (m := _LINK.match(k)))


def link_columns(st, sim_sec):
    """The PCIe link's own columns: wire traffic (with TLP overhead), utilization, latency.

    Only for runs that have a link (config B since the link model); other runs get none of these
    keys, so their reports are unchanged. Sums over links if a config ever has more than one.
    """
    links = pcie_links(st)
    if not links:
        return {}

    def tot(name):
        return sum(st.get(f"{p}.{name}", 0.0) for p in links)

    down, up = tot("downWireBytes"), tot("upWireBytes")
    ticks = sim_sec * TICKS_PER_SEC if sim_sec else 0
    done = tot("respondedReqs")
    payload = tot("downPayloadBytes") + tot("upPayloadBytes")
    return {
        # bytes on the wire each way: headers, framing and payload (pcie_* stays payload only)
        "link_down_bytes": int(down),
        "link_up_bytes": int(up),
        "link_tlps": int(tot("downTLPs") + tot("upTLPs")),
        "link_bw_gbs": _gbs(down + up, sim_sec),
        # payload share of what crossed the link: what the protocol overhead costs
        "link_efficiency": round(payload / (down + up), 4) if down + up else "",
        "link_down_util": round(tot("downBusyTicks") / ticks, 4) if ticks else "",
        "link_up_util": round(tot("upBusyTicks") / ticks, 4) if ticks else "",
        # round trip of a non-posted request (mostly reads) at the link, root port in to root
        # port out: includes the device's DRAM access
        "link_rd_lat_ns": round(tot("totRespLatency") / done / TICKS_PER_NS, 2) if done else "",
        "link_credit_stall_frac": round(tot("creditStallTicks") / ticks, 4) if ticks else "",
        "link_refusals": int(tot("hostQueueFullRetries") + tot("tagLimitRetries")),
    }


def _total(st, suffix):
    return sum(v for k, v in st.items() if k.endswith(suffix))


def _has(st, suffix):
    return any(k.endswith(suffix) for k in st)


def _tier(ctrls):
    rd = sum(c["rd"] for c in ctrls)
    wr = sum(c["wr"] for c in ctrls)
    timed = [c for c in ctrls if c["lat_ps"] is not None and c["rd"] > 0]
    weight = sum(c["rd"] for c in timed)
    lat_ns = sum(c["lat_ps"] * c["rd"] for c in timed) / weight / TICKS_PER_NS if weight else ""
    return rd, wr, lat_ns


def _gbs(nbytes, sim_sec):
    return nbytes / sim_sec / 1e9 if sim_sec else ""


def summarize(st, slow_pattern=SLOW_TIER_PATTERN):
    """Report columns for one run from its ROI stats."""
    sim_sec = st.get("simSeconds", 0.0)
    insts = st.get("simInsts") or _total(st, ".committedInsts") or _total(st, ".numInsts")
    cycles = _total(st, ".numCycles")
    l2m = [v for k, v in st.items() if "l2" in k and k.endswith("overallMisses::total")]

    row = {
        "sim_sec": sim_sec if "simSeconds" in st else "",
        "insts": insts,
        "cycles": cycles,
        "ipc": round(insts / cycles, 4) if cycles else "",
        # share of cycles the CPU was not idle; only reported if the CPU model counts idle cycles
        # (as idleCycles, or numIdleCycles under exec_context.thread_N for the timing CPU)
        "cpu_busy_frac": (
            round(1 - sum(_total(st, s) for s in _IDLE) / cycles, 4)
            if cycles and any(_has(st, s) for s in _IDLE)
            else ""
        ),
        "l2_misses": sum(l2m) if l2m else "",
        **disk_dma(st),
    }

    ctrls = memory_controllers(st)
    if not ctrls:
        return row
    slow_re = re.compile(slow_pattern, re.IGNORECASE)
    slow = [c for p, c in ctrls.items() if slow_re.search(p)]
    fast = [c for p, c in ctrls.items() if not slow_re.search(p)]
    f_rd, f_wr, f_lat = _tier(fast)
    s_rd, s_wr, s_lat = _tier(slow)
    _, _, all_lat = _tier(list(ctrls.values()))
    tot_rd, tot_wr = f_rd + s_rd, f_wr + s_wr

    row.update(
        {
            "dram_rd_bursts": tot_rd,
            "dram_wr_bursts": tot_wr,
            "mem_rd_bw_gbs": _gbs(tot_rd * BURST_BYTES, sim_sec),
            "mem_wr_bw_gbs": _gbs(tot_wr * BURST_BYTES, sim_sec),
            "mem_bw_gbs": _gbs((tot_rd + tot_wr) * BURST_BYTES, sim_sec),
            "avg_mem_lat_ns": round(all_lat, 2) if all_lat != "" else "",
            # local vs remote memory accesses, counted at the memory controllers
            "fast_rd_bursts": f_rd,
            "fast_wr_bursts": f_wr,
            "fast_lat_ns": round(f_lat, 2) if f_lat != "" else "",
            "slow_rd_bursts": s_rd,
            "slow_wr_bursts": s_wr,
            "slow_lat_ns": round(s_lat, 2) if s_lat != "" else "",
            "remote_access_frac": (
                round((s_rd + s_wr) / (tot_rd + tot_wr), 4) if tot_rd + tot_wr else ""
            ),
            # PCIe traffic: payload bytes crossing to the slow tier, counted at its controller.
            # Kept with this meaning for every run so rows stay comparable; the link's wire bytes
            # (with protocol/transaction overhead) are the link_* columns.
            "pcie_rd_bytes": s_rd * BURST_BYTES,
            "pcie_wr_bytes": s_wr * BURST_BYTES,
            "pcie_bw_gbs": _gbs((s_rd + s_wr) * BURST_BYTES, sim_sec),
        }
    )
    row.update(link_columns(st, sim_sec))
    return row
