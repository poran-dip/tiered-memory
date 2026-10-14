"""Config A, baseline: one node of fast DRAM only (scaled: 512 MiB for the brief's 8 GB).

gem5 full-system, X86, stock Ubuntu 24.04 kernel and disk. Shared setup is in configs/fsboard.py.

  third_party/gem5/build/X86/gem5.opt -d results/fs/a-chase-64 configs/a_baseline.py \\
      --workload chase --kvm-no-perf -- -s 64 -i 1

Config C (ideal upper bound, all fast) is this config with --fast-size 2560MiB.
Outputs in the -d directory: stats.txt (block 1 = ROI), roi_stats.json, fs_result.json (the
report; rebuild with python3 -m tiered_memory.fsreport <dir>), guest_output.txt (the guest's
program output), run.json (config, arguments, gem5 and repo commits, host), and the guest's serial
console in board.pc.com_1.device (boot log; check it when a run stops early). A -d directory under
results/fs/ is a committed run: it is also added to results/gem5_fs.csv, and only its small files
are kept in git (results/fs/.gitignore). scripts/fs_runs.sh runs the whole committed set.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fsboard import add_common_args, build_board, parse_args, run  # noqa: E402
from gem5.components.memory.single_channel import SingleChannelDDR4_2400  # noqa: E402

from tiered_memory.layout import DEFAULT_FAST, format_size, layout  # noqa: E402

p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
p.add_argument("--fast-size", default=DEFAULT_FAST, help="node 0 fast DRAM (DDR4-2400)")
add_common_args(p)
args = parse_args(p)

(fast,) = layout(args.fast_size)  # validates the size and that it fits below the I/O hole
memory = SingleChannelDDR4_2400(size=format_size(fast.size))
board = build_board(args, memory)
run(board, args, "A", settings={"fast_size": format_size(fast.size)})
