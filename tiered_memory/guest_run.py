"""Runs one workload inside a gem5 full-system guest and reports the Linux-side counters.

Copied into the disk image by scripts/make_disk.sh and started by the run script that the config
passes to the guest through gem5's readfile:

    cd /opt/tiered-memory && python3 -m tiered_memory.guest_run chase -s 64 -i 1
    ... python3 -m tiered_memory.guest_run --numactl=--membind=1 chase -s 64 -i 1

With --numactl the workload alone runs under numactl (installed in the image by make_disk.sh), so
its memory policy is set before it allocates anything; the Linux counters are still read from
here, outside it.

Everything goes to stdout. The run script tees it to a file and copies that to the host as
<outdir>/guest_output.txt (gem5 writefile); tiered_memory.fsreport parses three kinds of lines:

    GUESTINFO {...}     kernel, memory and swap size, NUMA nodes and their sizes, the kernel's
                        NUMA balancing / demotion settings and memory tiers, before the run
    RESULT ...          the workload's own result line (tiered_memory.result.parse_result)
    LINUXSTATS {...}    linuxstats delta and CPU columns over the whole run (allocation + ROI)

Only the standard library is used: the guest has python3 but nothing from requirements.
"""

import json
import os
import platform
import re
import resource
import shlex
import subprocess
import sys
import time
from pathlib import Path

from tiered_memory.linuxstats import NODE_DIR, cpu_columns, delta_columns, snapshot

BIN_DIR = Path(__file__).resolve().parents[1] / "bin"
GUESTINFO, LINUXSTATS = "GUESTINFO", "LINUXSTATS"
NUMACTL = "numactl"
NUMA_BALANCING = Path("/proc/sys/kernel/numa_balancing")
DEMOTION = Path("/sys/kernel/mm/numa/demotion_enabled")
TIER_DIR = Path("/sys/devices/virtual/memory_tiering")


def _meminfo_kib(path, key):
    try:
        m = re.search(rf"{key}:\s+(\d+) kB", Path(path).read_text())
    except OSError:
        return None
    return int(m.group(1)) if m else None


def _read(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def memory_tiers(tier_dir=TIER_DIR):
    """Linux's memory tiers as {tier id: nodelist}, e.g. {"4": "0-1"}; {} without tiering support.

    Nodes in the same tier are never demoted to one another: on kernels before the CPU-less
    HMAT tier fix, node 1 lands in the DRAM tier with node 0 and demotion has nowhere to go.
    """
    tiers = {}
    for d in sorted(Path(tier_dir).glob("memory_tier*")):
        nodes = _read(d / "nodelist")
        if nodes is not None:
            tiers[re.sub(r"\D", "", d.name)] = nodes
    return tiers


def numa_policy(numa_balancing=NUMA_BALANCING, demotion=DEMOTION, tier_dir=TIER_DIR):
    """Which of the kernel's own placement mechanisms are on for this run (None = not present)."""
    return {
        "numa_balancing": _read(numa_balancing),
        "demotion_enabled": _read(demotion),
        "memory_tiers": memory_tiers(tier_dir),
    }


def guest_info(node_dir=NODE_DIR):
    """Kernel release, MemTotal, SwapTotal, each NUMA node's CPUs and MemTotal (KiB), and the
    kernel's NUMA placement settings."""
    nodes = {}
    for d in sorted(Path(node_dir).glob("node[0-9]*")):
        try:
            cpus = (d / "cpulist").read_text().strip()
        except OSError:
            cpus = ""
        nodes[int(re.sub(r"\D", "", d.name))] = {
            "cpus": cpus,
            "mem_kib": _meminfo_kib(d / "meminfo", r"Node \d+ MemTotal"),
        }
    return {
        "kernel": platform.release(),
        "mem_total_kib": _meminfo_kib("/proc/meminfo", "MemTotal"),
        "swap_total_kib": _meminfo_kib("/proc/meminfo", "SwapTotal"),
        "nodes": nodes,
        **numa_policy(),
    }


def emit(tag, obj):
    print(f"{tag} {json.dumps(obj, sort_keys=True)}", flush=True)


USAGE = "usage: python3 -m tiered_memory.guest_run [--numactl=OPTS] <workload> [workload args]"


def workload_command(exe, args, numactl=None):
    """The workload's argv, under numactl when given options for it."""
    cmd = [str(exe), *args]
    return [NUMACTL, *shlex.split(numactl), "--", *cmd] if numactl else cmd


def main(argv):
    numactl = None
    if argv and argv[0].startswith("--numactl="):
        numactl, argv = argv[0].split("=", 1)[1], argv[1:]
    if not argv:
        print(USAGE, file=sys.stderr)
        return 2
    name, args = argv[0], argv[1:]
    exe = BIN_DIR / name
    info = guest_info()
    info["numactl"] = numactl or "none"
    emit(GUESTINFO, info)

    before, ru_before = snapshot(), resource.getrusage(resource.RUSAGE_CHILDREN)
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            workload_command(exe, args, numactl), stdout=subprocess.PIPE, text=True
        )
    except FileNotFoundError as e:
        # most likely numactl on a disk built before make_disk.sh installed it
        print(f"cannot start the workload: {e} (rebuild the disk: scripts/setup.sh disk)")
        return 127
    wall = time.monotonic() - t0
    after, ru_after = snapshot(), resource.getrusage(resource.RUSAGE_CHILDREN)

    sys.stdout.write(proc.stdout)
    cols = {"returncode": proc.returncode, "wall_sec_guest": round(wall, 6)}
    cols.update(delta_columns(before, after))
    cols.update(cpu_columns(ru_before, ru_after, wall))
    emit(LINUXSTATS, cols)
    return proc.returncode


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    sys.exit(main(sys.argv[1:]))
