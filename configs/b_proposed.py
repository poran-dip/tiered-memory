"""Config B, the system under test: fast DRAM on node 0 plus slow DRAM behind PCIe on CPU-less node 1.

Scaled: 512 MiB DDR4-2400 + 2 GiB DDR3-1600 for the brief's 8 GB + 32 GB. Node 1 has memory and
no CPUs, the way a PCIe or CXL memory expander appears to Linux. The guest kernel learns the two
nodes from an ACPI SRAT (gem5 fork commit 0d92c4d9). Nothing else is set up: placement,
fallback to node 1 and any page migration are left to the stock kernel.

The slow DRAM sits behind a PCIe link (the gem5 fork's PCIeMemLink, see docs/pcie-link.md): every
access to node 1 goes root port -> link -> device controller -> DRAM and back, paying the link's
latencies, its bandwidth (generation x lanes, less encoding and DLL overhead) and TLP header and
framing bytes, with tags and device credits limiting how much is in flight. Defaults: Gen4 x8 with
plain-PCIe latencies (~430 ns added to an idle read). --link none attaches the slow DRAM directly
to the memory bus instead, so node 1 differs from node 0 only in DRAM timing (the config before
the link existed).

Shared setup is in configs/fsboard.py; see configs/a_baseline.py for outputs.

  third_party/gem5/build/X86/gem5.opt -d results/fs/b-chase-64 configs/b_proposed.py \\
      --workload chase --kvm-no-perf -- -s 64 -i 1

Link variants (Phase 4/5 sweeps), e.g. a narrow Gen3 link, or CXL-class latencies:

  ... configs/b_proposed.py --link gen3x4 --workload stream --kvm-no-perf -- -s 64 -i 1
  ... configs/b_proposed.py --link gen5x16 --link-latency cxl --workload chase ...

To check the nodes, bind a workload with numactl (validation only, not for experiment runs):

  third_party/gem5/build/X86/gem5.opt -d m5out/b-chase-64-node1 configs/b_proposed.py \\
      --workload chase --kvm-no-perf --numactl=--membind=1 -- -s 64 -i 1
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fsboard import (  # noqa: E402
    LinkedMemory,
    TwoTierMemory,
    add_common_args,
    add_srat,
    build_board,
    make_link,
    parse_args,
    run,
)
from gem5.components.memory.single_channel import (  # noqa: E402
    SingleChannelDDR3_1600,
    SingleChannelDDR3_2133,
    SingleChannelDDR4_2400,
)

from tiered_memory import pcie  # noqa: E402
from tiered_memory.layout import DEFAULT_FAST, DEFAULT_SLOW, format_size, layout  # noqa: E402

# Slow-tier DRAM choices. DDR3-1600 is the brief's "DDR3/DDR2 class" (gem5 has no DDR2 model);
# ddr4-2400 makes node 1's DRAM identical to node 0's, so a run isolates the link's cost.
SLOW_DRAM = {
    "ddr3-1600": SingleChannelDDR3_1600,
    "ddr3-2133": SingleChannelDDR3_2133,
    "ddr4-2400": SingleChannelDDR4_2400,
}

p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
p.add_argument("--fast-size", default=DEFAULT_FAST, help="node 0 fast DRAM (DDR4-2400)")
p.add_argument("--slow-size", default=DEFAULT_SLOW, help="node 1 slow DRAM")
p.add_argument("--slow-dram", choices=list(SLOW_DRAM), default="ddr3-1600", help="node 1 DRAM")
p.add_argument(
    "--link",
    default=pcie.DEFAULT_LINK,
    help="PCIe link in front of node 1: genNxM (N 1-5, M lanes), e.g. gen3x4, gen4x8, gen5x16; "
    "or none to attach the slow DRAM directly (timing-only node 1). Default %(default)s",
)
p.add_argument(
    "--link-latency",
    choices=list(pcie.LATENCY_PRESETS),
    default=pcie.DEFAULT_LATENCY,
    help="latency preset: pcie (plain PCIe endpoint, ~420 ns added round trip) or cxl "
    "(CXL.mem ASIC class, ~150 ns). Default %(default)s",
)
p.add_argument("--rc-latency", type=float, metavar="NS", help="root port ns per traversal")
p.add_argument("--phy-latency", type=float, metavar="NS", help="one-way PHY + wire ns")
p.add_argument("--dev-latency", type=float, metavar="NS", help="device controller ns/traversal")
p.add_argument("--link-tags", type=int, default=pcie.DEFAULT_TAGS, help="outstanding reads")
p.add_argument(
    "--link-credits", type=int, default=pcie.DEFAULT_CREDITS, help="device request buffer entries"
)
add_common_args(p)
args = parse_args(p)

regions = layout(args.fast_size, args.slow_size)  # validates sizes; node 1 sits above node 0
if len(regions) != 2:
    p.error("--slow-size must be positive (config A is the one without a slow node)")
try:
    link_cfg = pcie.link_config(
        args.link,
        args.link_latency,
        rc=args.rc_latency,
        phy=args.phy_latency,
        dev=args.dev_latency,
        tags=args.link_tags,
        credits=args.link_credits,
    )
except ValueError as e:
    p.error(str(e))

fast, slow = regions
slow_dram = SLOW_DRAM[args.slow_dram](size=format_size(slow.size))
memory = TwoTierMemory(
    fast=SingleChannelDDR4_2400(size=format_size(fast.size)),
    slow=slow_dram if link_cfg is None else LinkedMemory(slow_dram, make_link(link_cfg)),
)
board = build_board(args, memory)
add_srat(board, regions)
run(
    board,
    args,
    "B",
    settings={
        "fast_size": format_size(fast.size),
        "slow_size": format_size(slow.size),
        "slow_dram": args.slow_dram.upper(),
        **pcie.settings(link_cfg),
    },
)
