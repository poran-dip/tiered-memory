"""Config C, ideal upper bound: B's total capacity, all of it fast (scaled: 2.5 GiB for 40 GB).

Same machine as config A with more fast DRAM: one node of DDR4-2400 sized to config B's fast + slow
memory (512 MiB + 2 GiB by default), so C bounds what B could achieve. Shared setup is in
configs/fsboard.py; see configs/a_baseline.py for outputs.

  third_party/gem5/build/X86/gem5.opt -d results/fs/c-chase-64 configs/c_ideal.py \\
      --workload chase --kvm-no-perf -- -s 64 -i 1
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fsboard import add_common_args, build_board, parse_args, run  # noqa: E402
from gem5.components.memory.single_channel import SingleChannelDDR4_2400  # noqa: E402

from tiered_memory.layout import (  # noqa: E402
    DEFAULT_FAST,
    DEFAULT_SLOW,
    format_size,
    layout,
    parse_size,
)

# B's fast + slow, so C tracks B's capacity if the defaults change.
DEFAULT_IDEAL = format_size(parse_size(DEFAULT_FAST) + parse_size(DEFAULT_SLOW))

p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
p.add_argument(
    "--fast-size",
    default=DEFAULT_IDEAL,
    help=f"node 0 fast DRAM (DDR4-2400), default {DEFAULT_IDEAL} = config B's total",
)
add_common_args(p)
args = parse_args(p)

(fast,) = layout(args.fast_size)  # validates the size and that it fits below the I/O hole
memory = SingleChannelDDR4_2400(size=format_size(fast.size))
board = build_board(args, memory)
run(board, args, "C", settings={"fast_size": format_size(fast.size)})
