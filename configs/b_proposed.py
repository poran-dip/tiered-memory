"""Config B, the system under test: fast DRAM on node 0 plus slow DRAM on a CPU-less node 1.

Scaled: 512 MiB DDR4-2400 + 2 GiB DDR3-1600 for the brief's 8 GB + 32 GB. Node 1 has memory and
no CPUs, the way a PCIe or CXL memory expander appears to Linux. The guest kernel learns the two
nodes from an ACPI SRAT (gem5 fork commit 0d92c4d9). Nothing else is set up: placement,
fallback to node 1 and any page migration are left to the stock kernel. Node 1 is slower only by
its DRAM timing so far; PCIe latency, bandwidth and protocol overhead come later.

Shared setup is in configs/fsboard.py; see configs/a_baseline.py for outputs.

  third_party/gem5/build/X86/gem5.opt -d results/fs/b-chase-64 configs/b_proposed.py \\
      --workload chase --kvm-no-perf -- -s 64 -i 1

To check the nodes, bind a workload with numactl (validation only, not for experiment runs):

  third_party/gem5/build/X86/gem5.opt -d m5out/b-chase-64-node1 configs/b_proposed.py \\
      --workload chase --kvm-no-perf --numactl=--membind=1 -- -s 64 -i 1
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fsboard import (  # noqa: E402
    TwoTierMemory,
    add_common_args,
    add_srat,
    build_board,
    parse_args,
    run,
)
from gem5.components.memory.single_channel import (  # noqa: E402
    SingleChannelDDR3_1600,
    SingleChannelDDR4_2400,
)

from tiered_memory.layout import DEFAULT_FAST, DEFAULT_SLOW, format_size, layout  # noqa: E402

p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
p.add_argument("--fast-size", default=DEFAULT_FAST, help="node 0 fast DRAM (DDR4-2400)")
p.add_argument("--slow-size", default=DEFAULT_SLOW, help="node 1 slow DRAM (DDR3-1600)")
add_common_args(p)
args = parse_args(p)

regions = layout(args.fast_size, args.slow_size)  # validates sizes; node 1 sits above node 0
if len(regions) != 2:
    p.error("--slow-size must be positive (config A is the one without a slow node)")
fast, slow = regions
memory = TwoTierMemory(
    fast=SingleChannelDDR4_2400(size=format_size(fast.size)),
    slow=SingleChannelDDR3_1600(size=format_size(slow.size)),
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
        "slow_dram": "DDR3-1600",
    },
)
