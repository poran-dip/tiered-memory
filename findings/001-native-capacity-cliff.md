# 001 · Native capacity cliff: gradual vs cliff behaviour under swap

_2026-10-08 · data: [`results/native_sweep.csv`](../results/native_sweep.csv) · produced by `sudo python3 native/sweep_ram.py --descend`, with the fixed `kvdecode` and the new `graph`, `kvstore` and `hashjoin` workloads_

## What was run

Every workload at its default native working set (64 MiB; `gemm` 16 MiB), first with no memory limit (fastest of 2 runs = baseline), then under a cgroup v2 `MemoryMax` limit lowered step by step (2×, 1.5×, 1.25×, 1×, 0.75× … of the working set) with unlimited swap. The sweep stops one step after the first run that is ≥ 1.5× slower than baseline, fails, or times out.

**Machine:** a Gigabyte G5 ME laptop under WSL2 (Ubuntu 24.04). WSL2 is a Hyper-V VM: one NUMA node, and swap is a virtual disk file on the Windows drive.

This is, in effect, a small native version of **config D** (fast DRAM + storage-backed swap) at the 64 MiB scale. It says nothing directly about config B; there is no slow DRAM tier here.

## Baselines (no limit)

| Workload   | ROI (s) | Throughput       | Per unit of work |
| ---------- | ------: | ---------------- | ---------------- |
| `stream`   |   0.010 | 20.0 GB/s        | 0.05 ns/byte     |
| `sort`     |    0.47 | 195 M compares/s | 5.1 ns/compare   |
| `gemm`     |    1.29 | 2.37 GFLOP/s     | 0.42 ns/flop     |
| `chase`    |    0.28 | 7.5 M hops/s     | **134 ns/hop**   |
| `kvdecode` |    0.32 | 13.1 GB/s        | 0.077 ns/byte    |
| `graph`    |    0.15 | 452 M edges/s    | 2.2 ns/edge      |
| `kvstore`  |    0.28 | 11.4 M ops/s     | 88 ns/op         |
| `hashjoin` |    0.25 | 50.5 M tuples/s  | 20 ns/tuple      |

`chase`'s 134 ns/hop is the native end-to-end load latency for a 64 MiB random walk with 4 KiB pages (DRAM plus TLB misses). It is the reference number to compare gem5's fast node against in Phase 2.

## Slowdown as the limit tightens

Slowdown = ROI time / baseline ROI time. `OOM` = killed by the kernel (exit −9); `timeout` = hit the run cap (20 s here).

| Workload   |    2× | 1.5× | 1.25× |   1× |            0.75× | 0.5× | 0.375× | 0.25× |
| ---------- | ----: | ---: | ----: | ---: | ---------------: | ---: | -----: | ----: |
| `stream`   | 1.61¹ | 1.00 |       |      |                  |      |        |       |
| `sort`     |  1.03 | 1.02 |  0.99 | 1.63 |              OOM |      |        |       |
| `gemm`     |  1.01 | 0.98 |  1.00 | 0.96 |             1.27 | 1.31 |   1.76 |   OOM |
| `chase`    |  1.05 | 1.01 |  1.00 |  OOM |                  |      |        |       |
| `kvdecode` |  0.98 | 1.07 |  0.99 | 0.92 | timeout (> 49×)² |      |        |       |
| `graph`    |  1.00 | 1.01 |  1.04 | 1.05 |              OOM |      |        |       |
| `kvstore`  |  1.03 | 1.03 |  0.98 |  OOM |                  |      |        |       |
| `hashjoin` |  0.99 | 0.95 |  1.06 | 1.60 |             4.80 |      |        |       |

¹ Not a real knee; see F5. ² Wall time at the timeout (20.0 s) over the baseline wall time (0.41 s); the true slowdown is larger.

Swap traffic at the first limit that hurt:

| Workload   | Limit (MiB) | Swapped in | Swapped out | Outcome |
| ---------- | ----------: | ---------: | ----------: | ------- |
| `sort`     |          64 |    7.8 MiB |   118.6 MiB | 1.63×   |
| `gemm`     |          12 |    9.9 MiB |    10.8 MiB | 1.27×   |
| `hashjoin` |          64 |    3.3 MiB |    48.1 MiB | 1.60×   |
| `hashjoin` |          48 |  149.4 MiB |    48.1 MiB | 4.80×   |
| `kvdecode` |          48 | 2218.9 MiB |    64.7 MiB | timeout |
| `graph`    |          48 |  114.4 MiB |   141.2 MiB | OOM     |
| `chase`    |          64 |    9.4 MiB |    24.1 MiB | OOM     |
| `kvstore`  |          64 |    8.2 MiB |    12.4 MiB | OOM     |

## Findings

**F1 · Established: with enough memory, a tighter limit costs nothing.** At limits from 1.25× to 2× the working set, every workload except `stream` stays within 0.92–1.07× of baseline, with no major faults and no swap. That band is run-to-run noise at these ROI lengths (0.15–1.3 s). Differences under about ±8% in this data set are not meaningful.

**F2 · Established: once a workload no longer fits, it does one of two things.**

- **Degrades gradually**: `gemm`, `hashjoin`, `sort` (until 0.75×). They keep running with modest swap traffic, slowing from about 1.3× to 5×.
- **Falls off a cliff**: `chase`, `kvstore`, `graph` are OOM-killed at the first limit below their footprint; `kvdecode` keeps running but thrashes (2.2 GiB read back from swap in 20 s, about 35 complete re-reads of its 64 MiB) and times out.

**F3 · Tentative: the access pattern predicts which one.** The gradual group has either strong reuse in a small region (`gemm`'s blocked tiles) or a large part read sequentially once per pass (`hashjoin`'s probe relation is 40 of its 64 MiB and streams through swap with readahead; `sort`'s partitions are sequential). The cliff group touches the whole working set randomly (`chase`, `kvstore`, `graph`) or streams it cyclically (`kvdecode`: every token reads all weights in the same order). Cyclic streaming larger than memory is the worst case for LRU-style reclaim: each page is evicted just before it is needed again, so every token rereads the model from swap. This is a reading of the data, not yet tested by varying the pattern directly.

**F4 · Hypothesis (for Phase 3): the cliff group is where config B can beat config D by the most.** With swap as the only overflow, those workloads either die or run tens of times slower, because each fault costs a storage access and there is no locality to amortise it. A slow DRAM tier serves the same pages at DRAM-over-PCIe latency instead of storage latency. Two different mechanisms would be at work:

- `graph` and `kvstore` have skew (hub vertices, Zipfian keys), so Linux _promotion_ could keep the hot part in the fast tier.
- `kvdecode` has no hot subset: every weight is read once per token. Promotion cannot help; any benefit has to come from the slow tier's bandwidth itself. It is the cleanest test of "is PCIe DRAM fast enough to stream from".

The gradual group sets the bar config D already clears; B's advantage there should be smaller.

**F5 · Established (measurement problem): `stream`'s knee is noise.** Its ROI is 10 ms (3 passes of 64 MiB at 20 GB/s). The 1.61× at the 2× limit came with 7 major faults and no swap; the next, tighter limit (1.5×) measured 1.00×. Because 1.61× crossed the 1.5× threshold, the sweep stopped there, so `stream` was never tested near or below its footprint. It needs a longer ROI before it says anything.

**F6 · Established: native Linux tiering is invisible on this machine.** `promote_*`, `numa_pages_migrated` and `numa_hint_faults` are blank (the counters do not exist), demotions are 0 and every allocation is on node 0. WSL2 exposes one NUMA node and no tiering, so native runs can only ever characterise configs A and D. Tiering behaviour has to come from gem5 full-system mode.

**F7 · Established: OOM-kill happens even with unlimited swap.** Swap was never limited (`MemorySwapMax=infinity`), yet five runs were killed while swap had room: `chase`, `kvstore` and `graph` at their first limit below the footprint, `sort` at 0.75× and `gemm` at 0.25×. The cgroup's memory controller kills the process when its reclaim attempts fail to free enough memory, rather than waiting on swap indefinitely; in this data that hit the random-access workloads first. Practical consequence: at the native scale, "config A" (no swap) and "config D" (with swap) behave the same for the cliff group: the run fails. That makes a working config B, for those workloads, a capacity win before it is a speed win.

**F8 · Established: the `kvdecode` fix is in effect.** Throughput is 13.1 GB/s against 5.5 GB/s for the old code on the same machine, and the checksum (`3248353755`) matches the fixed build.

## Gaps in the measurement

- **The limit is compared against the nominal working set, not the real footprint.** Actual peak memory differs by workload: `chase` briefly needs ~72 MiB during setup (its permutation array), `kvstore`'s table is exactly 64 MiB plus process overhead, `kvdecode` uses ~63.2 MiB, `graph` leaves part of its BFS queue untouched. The "factor" column is therefore approximate. Recording each run's peak resident memory would fix this.
- **No host metadata in this CSV** (CPU, RAM, kernel, WSL vs bare metal): it predates the `host_*` columns `sweep_ram.py` now records.
- **Single runs** at each limit; only the baseline is best-of-2.

## Future work

- Configs B and C: there is no slow DRAM tier and no NUMA here.
- Scale: all runs are at 64 MiB; the gem5 full-system configs use a 512 MiB fast tier.
- Repeatability: repeat each limited point 3× and report the median, and record peak RSS per run.
- `stream`: raise its native iterations so the ROI is at least ~0.3 s, then rerun it.
- Past capacity under A, B and D: `kvdecode` (pure bandwidth test of the slow tier) and `graph` / `kvstore` (Linux promotion with skew) are the workloads F4 points at.
