"""Runs one workload inside a gem5 full-system guest and reports the Linux-side counters.

Copied into the disk image by scripts/make_disk.sh and started by the run script that the config
passes to the guest through gem5's readfile:

    cd /opt/tiered-memory && python3 -m tiered_memory.guest_run chase -s 64 -i 1

Everything goes to stdout, which the guest's serial console sends to the host
(m5out/board.pc.com_1.device). The host parses three kinds of lines:

    GUESTINFO {...}     kernel, NUMA nodes and their sizes, before the run
    RESULT ...          the workload's own result line (tiered_memory.result.parse_result)
    LINUXSTATS {...}    linuxstats delta and CPU columns over the whole run (allocation + ROI)

Only the standard library is used: the guest has python3 but nothing from requirements.
"""

import json
import os
import platform
import re
import resource
import subprocess
import sys
import time
from pathlib import Path

from tiered_memory.linuxstats import NODE_DIR, cpu_columns, delta_columns, snapshot

BIN_DIR = Path(__file__).resolve().parents[1] / "bin"
GUESTINFO, LINUXSTATS = "GUESTINFO", "LINUXSTATS"


def _meminfo_kib(path, key):
    try:
        m = re.search(rf"{key}:\s+(\d+) kB", Path(path).read_text())
    except OSError:
        return None
    return int(m.group(1)) if m else None


def guest_info(node_dir=NODE_DIR):
    """Kernel release, MemTotal, and each NUMA node's CPUs and MemTotal (KiB)."""
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
        "nodes": nodes,
    }


def emit(tag, obj):
    print(f"{tag} {json.dumps(obj, sort_keys=True)}", flush=True)


def main(argv):
    if not argv:
        print(
            "usage: python3 -m tiered_memory.guest_run <workload> [workload args]", file=sys.stderr
        )
        return 2
    name, args = argv[0], argv[1:]
    exe = BIN_DIR / name
    emit(GUESTINFO, guest_info())

    before, ru_before = snapshot(), resource.getrusage(resource.RUSAGE_CHILDREN)
    t0 = time.monotonic()
    proc = subprocess.run([str(exe), *args], stdout=subprocess.PIPE, text=True)
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
