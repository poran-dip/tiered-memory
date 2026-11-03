"""Check gem5's PCIe link model against what tiered_memory/pcie.py says it should do.

No OS and no disk image: a traffic generator drives the slow tier alone (the PCIe link in front of
its DRAM, exactly as config B builds it), and the script compares what the link's stats show with
the expected numbers. Seconds of host time per check; run it after rebuilding gem5.

  third_party/gem5/build/X86/gem5.opt -d m5out/link-lat configs/link_check.py --mode latency
  third_party/gem5/build/X86/gem5.opt -d m5out/link-bw configs/link_check.py --mode bandwidth \\
      --link gen3x4

latency:   sparse reads, one at a time, so nothing queues. The link's round trip minus the DRAM
           controller's own latency should be the link's expected unloaded latency.
bandwidth: reads (or writes with --writes) as fast as the generator can issue them. On a link
           narrower than the DRAM the payload rate should sit at the link's limit; on a wider one
           the DRAM is the limit and the link's utilization stays below 1.

Prints one CHECK line per comparison and exits 1 if any is off by more than --tolerance.
"""

import argparse
import configparser
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import m5  # noqa: E402
from fsboard import LinkedMemory, make_link  # noqa: E402
from gem5.components.boards.test_board import TestBoard  # noqa: E402
from gem5.components.memory.single_channel import (  # noqa: E402
    SingleChannelDDR3_1600,
    SingleChannelDDR4_2400,
)
from gem5.components.processors.linear_generator import LinearGenerator  # noqa: E402
from gem5.simulate.simulator import Simulator  # noqa: E402

from tiered_memory import pcie  # noqa: E402
from tiered_memory.gem5stats import link_columns, memory_controllers, roi_block  # noqa: E402
from tiered_memory.schema import TICKS_PER_NS  # noqa: E402

p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
p.add_argument("--mode", choices=["latency", "bandwidth"], default="latency")
p.add_argument("--link", default=pcie.DEFAULT_LINK, help="genNxM (default %(default)s)")
p.add_argument("--link-latency", choices=list(pcie.LATENCY_PRESETS), default=pcie.DEFAULT_LATENCY)
p.add_argument("--slow-dram", choices=["ddr3-1600", "ddr4-2400"], default="ddr3-1600")
p.add_argument("--writes", action="store_true", help="bandwidth mode: writes instead of reads")
p.add_argument("--duration", default=None, help="generator run time (default by mode)")
p.add_argument("--tolerance", type=float, default=0.10, help="allowed relative error")
args = p.parse_args()

cfg = pcie.link_config(args.link, args.link_latency)
if cfg is None:
    p.error("--link none has no link to check")

SIZE = 64
MiB = 1 << 20
dram_cls = SingleChannelDDR3_1600 if args.slow_dram == "ddr3-1600" else SingleChannelDDR4_2400
memory = LinkedMemory(dram_cls(size="256MiB"), make_link(cfg))

if args.mode == "latency":
    # One 64 B read about every 2 us: each finishes long before the next starts.
    gen = LinearGenerator(
        duration=args.duration or "2ms",
        rate="32MiB/s",
        block_size=SIZE,
        min_addr=0,
        max_addr=64 * MiB,
        rd_perc=100,
    )
else:
    gen = LinearGenerator(
        duration=args.duration or "200us",
        rate="200GiB/s",
        block_size=SIZE,
        min_addr=0,
        max_addr=64 * MiB,
        rd_perc=0 if args.writes else 100,
    )

board = TestBoard(clk_freq="3GHz", generator=gen, memory=memory, cache_hierarchy=None)
sim = Simulator(board=board)
sim.run()
m5.stats.dump()

st = roi_block(Path(m5.options.outdir) / "stats.txt")
sim_sec = st.get("simSeconds", 0.0)
link = link_columns(st, sim_sec)
ctrls = memory_controllers(st)
if not link or not ctrls:
    sys.exit("no link or DRAM stats in stats.txt: is this the gem5 fork with PCIeMemLink?")
dram = next(iter(ctrls.values()))

checks = []


def check(what, measured, expected, unit):
    err = abs(measured - expected) / expected if expected else float("inf")
    ok = err <= args.tolerance
    checks.append(ok)
    print(
        f"CHECK {'ok  ' if ok else 'FAIL'} {what}: measured {measured:.2f} {unit}, "
        f"expected {expected:.2f} {unit} ({err:+.1%} off, tolerance {args.tolerance:.0%})"
    )


print(
    f"link {cfg.spec.name}, {cfg.latency_name} latencies {tuple(cfg.latency)} ns, {args.slow_dram}"
)


def ctrl_static_ns():
    """The DRAM controller's fixed front-end + back-end latency, from gem5's config.ini.

    It is inside the link's round trip but not in the controller's avgMemAccLat.
    """
    ini = configparser.ConfigParser(interpolation=None, strict=False)
    ini.read(Path(m5.options.outdir) / "config.ini")
    for sec in ini.sections():
        if sec.endswith("mem_ctrl") and ini.has_option(sec, "static_frontend_latency"):
            ticks = int(ini[sec]["static_frontend_latency"]) + int(
                ini[sec]["static_backend_latency"]
            )
            return ticks / TICKS_PER_NS
    return 0.0


if args.mode == "latency":
    dram_ns = (dram["lat_ps"] or 0) / TICKS_PER_NS
    static_ns = ctrl_static_ns()
    added = link["link_rd_lat_ns"] - dram_ns - static_ns
    print(
        f"read round trip at the link {link['link_rd_lat_ns']} ns; DRAM controller "
        f"{dram_ns:.1f} ns + {static_ns:.1f} ns fixed"
    )
    check("link's added read latency", added, pcie.unloaded_read_ns(cfg, SIZE), "ns")
else:
    payload = link["link_down_bytes"] if args.writes else link["link_up_bytes"]
    req, cpl = pcie.read_wire_bytes(SIZE)
    wire = pcie.write_wire_bytes(SIZE) if args.writes else cpl
    measured_gbs = payload * SIZE / wire / sim_sec / 1e9
    limit_gbs = pcie.max_write_gbs(cfg, SIZE) if args.writes else pcie.max_read_gbs(cfg, SIZE)
    util = link["link_down_util"] if args.writes else link["link_up_util"]
    print(
        f"payload {measured_gbs:.2f} GB/s, link limit {limit_gbs:.2f} GB/s, "
        f"{'down' if args.writes else 'up'} utilization {util:.3f}, "
        f"credit stalls {link['link_credit_stall_frac']:.3f}, efficiency {link['link_efficiency']}"
    )
    if util > 0.9:
        check("payload bandwidth at the link limit", measured_gbs, limit_gbs, "GB/s")
    else:
        print(
            "the link is not the bottleneck here (utilization < 0.9): DRAM or the generator is; "
            "rerun with a narrower --link to check the link's limit"
        )
sys.exit(0 if all(checks) else 1)
