# Measurements

Maps each measurement required by the project spec to the CSV columns that report it, where the
number comes from, and what it does and does not mean. Code: `tiered_memory/`. Sweeps:
`configs/sweep.py` (gem5, writes `results/gem5_sweep.csv`) and `native/sweep_ram.py` (real
machine, writes `results/native_sweep.csv`).

**Blank vs 0.** A blank cell means that counter is unavailable or the run did not get that far.
`0` means it was measured and nothing happened.

| Required measurement                  | gem5 columns                                                                                 | Native columns                                                                                                                      | Source and caveats                                                                                                                                                                                                                                                  |
| ------------------------------------- | -------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Application execution time            | `sim_sec`                                                                                    | `wall_sec`, `roi_sec`                                                                                                               | gem5: simulated seconds of the ROI (from `stats.txt`). Never compare gem5 and native seconds directly.                                                                                                                                                              |
| Throughput                            | `throughput`, `work`, `work_unit`                                                            | same                                                                                                                                | `work / time` over the ROI. The unit differs per workload (stream/kvdecode bytes, chase hops, gemm flops, sort compares, graph edges, kvstore ops, hashjoin tuples; see `tiered_memory/workloads.py`), so compare a workload only with itself.                      |
| IPC                                   | `ipc`, `insts`, `cycles`                                                                     | none yet                                                                                                                            | `insts / cycles` of the ROI. Native IPC needs `perf stat` and is not collected yet.                                                                                                                                                                                 |
| Memory bandwidth                      | `mem_bw_gbs`, `mem_rd_bw_gbs`, `mem_wr_bw_gbs`                                               | `throughput` (stream only)                                                                                                          | DRAM bursts x 64 B / `sim_sec`, summed over all memory controllers.                                                                                                                                                                                                 |
| Memory latency                        | `avg_mem_lat_ns`, `fast_lat_ns`, `slow_lat_ns`                                               | `ns_per_work` (chase only = ns per hop)                                                                                             | Controller-side `avgMemAccLat`, read-weighted. It is the DRAM-side latency, not what the CPU sees. `ns_per_work` for `chase` is the end-to-end latency and the number to validate the tier against.                                                                 |
| Local vs remote memory accesses       | `fast_rd_bursts`, `fast_wr_bursts`, `slow_rd_bursts`, `slow_wr_bursts`, `remote_access_frac` | `alloc_local_pages`, `alloc_other_pages`, `node<N>_alloc_hit`                                                                       | gem5: true accesses counted at each controller. Native: page allocations by the Linux allocator, not accesses (4 KiB pages).                                                                                                                                        |
| Page migration / demotion / promotion | not in SE mode (no OS)                                                                       | `promote_pages`, `promote_candidates`, `demote_pages`, `numa_pages_migrated`, `migrate_success`, `migrate_fail`, `numa_hint_faults` | `/proc/vmstat` deltas over the whole run (allocation + ROI), in pages. Blank when the kernel lacks the counter. Becomes available in gem5 full-system mode.                                                                                                         |
| Swap activity                         | not in SE mode                                                                               | `swap_in_pages`, `swap_out_pages`, `majflt`, `sys_majfault`                                                                         | `pswpin`/`pswpout` deltas. `majflt` is the process only; `sys_majfault` is system-wide.                                                                                                                                                                             |
| PCIe traffic                          | `pcie_rd_bytes`, `pcie_wr_bytes`, `pcie_bw_gbs`                                              | not applicable                                                                                                                      | **Proxy:** payload bytes that reach the slow-tier controller. Excludes protocol and transaction overhead until the link itself is modeled. 0 until a slow tier exists.                                                                                              |
| CPU utilization                       | `cpu_busy_frac`                                                                              | `user_sec`, `sys_sec`, `cpu_util`                                                                                                   | Native `cpu_util = (user + sys) / wall`; it drops when the process blocks on swap. gem5 `cpu_busy_frac` only appears if the CPU model exposes `idleCycles`. In SE mode a single thread is never idle, so expect about 1. It becomes meaningful in full-system mode. |

## Conventions

- **Slow tier detection.** A gem5 memory controller whose stat path matches `--slow-pattern`
  (default `slow|cxl|pcie`) is counted as the slow tier. Name the second tier's controller
  accordingly when the two-tier config is written. Everything else is the fast tier.
- **Raw stats kept.** Every gem5 run also writes `roi_stats.json` (all ROI stats) next to
  `stats.txt`, so a new metric can be added later with `--reparse` and no re-simulation.
- **System-wide counters.** The Linux counters cover the whole machine, so run native sweeps on
  an otherwise idle system.
- **Full-system runs.** `configs/a_baseline.py`, `c_ideal.py`, `d_swap.py` (and later B) write `fs_result.json` next to
  `stats.txt` (rebuild it without gem5: `python3 -m tiered_memory.fsreport <run dir>`): the same gem5 columns as the SE sweep, plus the Linux columns measured inside the
  guest by `tiered_memory/guest_run.py` and a `guest` entry (kernel, memory and swap size, NUMA nodes and their sizes).
  The guest-side Linux and CPU columns cover the whole workload process, as natively; the gem5
  columns cover only the ROI, which runs on the timing or O3 CPU while everything else runs on KVM.

## Not covered yet

- Native IPC (`perf stat`).
- ROI-only Linux counters. Today they span allocation plus ROI, which is where spill, swap and
  migration mostly happen anyway.
- The slow tier (config B, needs the SRAT table in the gem5 fork) and the PCIe link model. Those
  columns exist but stay blank or 0 until the corresponding piece is built.
- Swap in gem5 (config D) runs on gem5's IDE disk model with a fixed per-request delay
  (`--swap-delay`), not an NVMe model: latency is configurable, queueing and bandwidth are not.
