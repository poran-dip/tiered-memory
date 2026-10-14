"""Config D, conventional: config A's fast DRAM plus swap on a storage device (scaled: 512 MiB +
2 GiB swap for the brief's 8 GB + NVMe/SSD swap).

Same machine as config A, with a second IDE disk (/dev/sdb in the guest) that the run script turns
into swap before the workload starts. Swap size defaults to config B's slow-tier size, so B and D
add the same capacity and differ only in where it lives (PCIe DRAM vs storage). Shared setup is in
configs/fsboard.py; see configs/a_baseline.py for outputs.

  third_party/gem5/build/X86/gem5.opt -d results/fs/d-chase-64 configs/d_swap.py \\
      --workload chase --kvm-no-perf -- -s 64 -i 1

The swap disk's per-request delay (--swap-delay, default 80us) stands in for an NVMe SSD's 4 KiB
random-read latency. gem5's IDE model is not an NVMe model: queueing and bandwidth are the IDE
controller's, so treat D as context for A vs B, not as a calibrated SSD.
Needs a disk built by scripts/setup.sh disk after this change (it allows mkswap/swapon via sudo).
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import m5  # noqa: E402
from fsboard import (  # noqa: E402
    SWAP_DEVICE,
    add_common_args,
    add_swap_disk,
    build_board,
    parse_args,
    run,
)
from gem5.components.memory.single_channel import SingleChannelDDR4_2400  # noqa: E402

from tiered_memory.layout import (  # noqa: E402
    DEFAULT_FAST,
    DEFAULT_SLOW,
    MiB,
    format_size,
    layout,
    parse_size,
)

SWAP_IMAGE = "swap.img"  # sparse file in the outdir; a fresh one every run

p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
p.add_argument("--fast-size", default=DEFAULT_FAST, help="node 0 fast DRAM (DDR4-2400)")
p.add_argument(
    "--swap-size",
    default=DEFAULT_SLOW,
    help=f"swap device size, default {DEFAULT_SLOW} = config B's slow tier",
)
p.add_argument(
    "--swap-delay",
    default="80us",
    help="fixed latency per swap-disk request (gem5 IdeDisk.delay); ~NVMe 4 KiB random read",
)
add_common_args(p)
args = parse_args(p)

(fast,) = layout(args.fast_size)  # validates the size and that it fits below the I/O hole
swap_bytes = parse_size(args.swap_size)
if swap_bytes < MiB:
    p.error("--swap-size must be at least 1MiB")

# Sparse, so a 2 GiB swap file costs no disk space until the guest writes to it.
swap_image = Path(m5.options.outdir) / SWAP_IMAGE
swap_image.unlink(missing_ok=True)
with open(swap_image, "wb") as fh:
    fh.truncate(swap_bytes)
print(f"Swap disk: {swap_image} ({format_size(swap_bytes)}, {args.swap_delay} per request)")

memory = SingleChannelDDR4_2400(size=format_size(fast.size))
board = build_board(args, memory, swap_device=SWAP_DEVICE)
add_swap_disk(board, swap_image, args.swap_delay)


def swap_written():
    """Progress-line extra: how much of the sparse swap file the guest has written so far.

    It only grows while the guest swaps out to new slots, so it shows swapping has happened, not
    the current swap rate.
    """
    try:
        used = swap_image.stat().st_blocks * 512
    except OSError:
        return ""
    return f"swap file {used / MiB:.0f} MiB written"


run(
    board,
    args,
    "D",
    settings={
        "fast_size": format_size(fast.size),
        "swap_size": format_size(swap_bytes),
        "swap_delay": args.swap_delay,
    },
    progress_extra=swap_written,
)
