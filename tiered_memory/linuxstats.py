"""Linux-side counters: page migration/promotion/demotion, swap, NUMA allocation, CPU time.

Take a snapshot() before and after a run and pass both to delta_columns(). The same code works on
the host and inside a gem5 full-system guest. A blank column means the kernel does not expose that
counter; 0 means it does and nothing happened.
"""

import re
from pathlib import Path

VMSTAT = Path("/proc/vmstat")
NODE_DIR = Path("/sys/devices/system/node")


def read_vmstat(path=VMSTAT):
    try:
        text = Path(path).read_text()
    except OSError:
        return None
    return {k: int(v) for k, v in (line.split() for line in text.splitlines() if line.strip())}


def read_numastat(node_dir=NODE_DIR):
    """{node id: {numa_hit, numa_miss, local_node, other_node, ...}} in 4 KiB pages."""
    nodes = {}
    for d in sorted(Path(node_dir).glob("node[0-9]*")):
        try:
            text = (d / "numastat").read_text()
        except OSError:
            continue
        nodes[int(re.sub(r"\D", "", d.name))] = {
            k: int(v) for k, v in (line.split() for line in text.splitlines() if line.strip())
        }
    return nodes


def snapshot(vmstat_path=VMSTAT, node_dir=NODE_DIR):
    return {"vmstat": read_vmstat(vmstat_path), "numastat": read_numastat(node_dir)}


def _diff(before, after, key):
    if before is None or after is None or key not in before or key not in after:
        return ""
    return after[key] - before[key]


def _diff_prefix(before, after, prefix):
    if before is None or after is None:
        return ""
    keys = [k for k in after if k.startswith(prefix) and k in before]
    return sum(after[k] - before[k] for k in keys) if keys else ""


def delta_columns(before, after):
    """Report columns from two snapshots. Counters are system-wide, so run on a quiet machine."""
    b, a = before["vmstat"], after["vmstat"]
    cols = {
        "oom_kills": _diff(b, a, "oom_kill"),
        "promote_pages": _diff(b, a, "pgpromote_success"),
        "promote_candidates": _diff(b, a, "pgpromote_candidate"),
        "demote_pages": _diff_prefix(b, a, "pgdemote_"),
        "numa_pages_migrated": _diff(b, a, "numa_pages_migrated"),
        "migrate_success": _diff(b, a, "pgmigrate_success"),
        "migrate_fail": _diff(b, a, "pgmigrate_fail"),
        "numa_hint_faults": _diff(b, a, "numa_hint_faults"),
        "swap_in_pages": _diff(b, a, "pswpin"),
        "swap_out_pages": _diff(b, a, "pswpout"),
        "sys_majfault": _diff(b, a, "pgmajfault"),
    }
    # Pages allocated on the node the process ran on vs another node (allocation, not access).
    local = other = 0
    for node, stats in after["numastat"].items():
        prev = before["numastat"].get(node)
        if prev is None:
            continue
        d = {k: stats[k] - prev[k] for k in stats if k in prev}
        local += d.get("local_node", 0)
        other += d.get("other_node", 0)
        cols[f"node{node}_alloc_hit"] = d.get("numa_hit", 0)
        cols[f"node{node}_alloc_miss"] = d.get("numa_miss", 0)
    if after["numastat"]:
        cols["alloc_local_pages"] = local
        cols["alloc_other_pages"] = other
    return cols


def cpu_columns(ru_before, ru_after, wall_sec):
    """CPU time and utilization = (user+sys)/wall. Falls when the process blocks on swap or I/O."""
    user = ru_after.ru_utime - ru_before.ru_utime
    sys_ = ru_after.ru_stime - ru_before.ru_stime
    return {
        "user_sec": round(user, 3),
        "sys_sec": round(sys_, 3),
        "cpu_util": round((user + sys_) / wall_sec, 4) if wall_sec > 0 else "",
        "majflt": ru_after.ru_majflt - ru_before.ru_majflt,
        "minflt": ru_after.ru_minflt - ru_before.ru_minflt,
    }
