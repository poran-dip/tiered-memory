# 002 · gem5 configs A, C and D: identical while the working set fits; past it, A dies and D slows 10–50×

_2026-10-15 · data: [`results/gem5_fs.csv`](../results/gem5_fs.csv) (one row per run; each run's `fs_result.json`, `roi_stats.json`, `guest_output.txt` and `run.json` are in [`results/fs/`](../results/fs/)) and [`results/native_stream448.csv`](../results/native_stream448.csv) · gem5: poran-dip/gem5 `618cf87` (SRAT + page-walker fix), X86 full-system, Ubuntu 24.04 kernel 6.8.0-52, `PrivateL1PrivateL2WalkCacheHierarchy`_

## What was run

| Config | File                    | Memory                                                        |
| ------ | ----------------------- | ------------------------------------------------------------- |
| A      | `configs/a_baseline.py` | 512 MiB fast DDR4-2400                                        |
| C      | `configs/c_ideal.py`    | 2560 MiB fast DDR4-2400 (B's total capacity, all fast)        |
| D      | `configs/d_swap.py`     | 512 MiB fast + 2 GiB swap on a second IDE disk, 80 µs/request |

Same machine otherwise: one X86 core at 3 GHz, 32 KiB L1s, 256 KiB L2, 8 KiB walk caches. Boot and workload setup on KVM (no perf counters, WSL2 host); ROI on the timing CPU.

Every run in this finding is listed in `scripts/fs_runs.sh` and was run with `scripts/fs_runs.sh -j 3` (three gem5 processes at once on a Gigabyte G5 ME laptop under WSL2, which matters only for the wall times). A single run is:

```bash
third_party/gem5/build/X86/gem5.opt -d results/fs/<run> configs/<config>.py \
    --workload <wl> --kvm-no-perf -- -s <MiB> -i 1 [-d 1]
```

`chase` is a dependent pointer chase (latency); `stream` is one STREAM-triad pass (`-i 1`); `-d 1` sweeps `stream`'s ROI backwards. Every point is a single run. Each run's working set was checked against the `ws_mb` in its own `RESULT` line.

For comparison, the same `stream` binary natively on the same laptop, with and without a 410 MiB cgroup limit, swap counted from `/proc/vmstat`:

```bash
sudo python3 native/sweep_ram.py --workloads stream --ws 448 --iters 1 \
    --limits none 410 --out results/native_stream448.csv [-- -d 1]
```

## Results

### While the working set fits

| Workload        | A            | C            | D            | Checksum (all three) |
| --------------- | ------------ | ------------ | ------------ | -------------------- |
| `chase -s 64`   | 103.1 ns/hop | 103.0 ns/hop | 102.6 ns/hop | `549755289600`       |
| `chase -s 256`  | 134.1 ns/hop | 134.0 ns/hop | 134.0 ns/hop | `8796090925056`      |
| `stream -s 320` | 6.68 GB/s    | 6.59 GB/s    | 6.70 GB/s    | `288509703`          |
| `stream -s 384` | 6.54 GB/s    | 6.62 GB/s    | 6.68 GB/s    | `346068554`          |

No swap traffic in any of these, and the CPU is never idle in the ROI (`cpu_busy_frac` 1.0). ROIs: `chase -s 64` 0.108 s simulated / 8.1–8.2M instructions; `chase -s 256` 0.562 s / 28.4M; `stream -s 320` 0.050–0.051 s / 53.6–54.0M; `stream -s 384` 0.060–0.062 s / 64.9–65.6M. Before the walk caches were added, `chase -s 64` measured 113.5 ns/hop on A and C.

### At 448 MiB, past what A and D can hold

| Run                  | A                             | C (fits)                                 | D                                        |
| -------------------- | ----------------------------- | ---------------------------------------- | ---------------------------------------- |
| `stream -s 448`      | **killed** (`rc=-9`) in setup | **6.52 GB/s**, 0.072 s ROI, 76.2M insts  | **0.127 GB/s**, 3.71 s ROI, 5.00B insts  |
| `stream -s 448 -d 1` | **killed** (`rc=-9`) in setup | **5.69 GB/s**, 0.083 s ROI, 103.7M insts | **0.565 GB/s**, 0.832 s ROI, 1.20B insts |

All completed runs at 448 MiB give checksum `403838933`. Neither A run reached its ROI (`roi_completed` false).

D's swap traffic, in the ROI alone (`disk_in_bytes` / `disk_out_bytes`, the swap disk's DMA counted by gem5) and over the whole process (the guest's `pswpin` / `pswpout`, which also cover setup and the final checksum read):

| D run    | ROI in / out (MiB) | Whole process in / out (pages) | `majflt` | `migrate_success` | ROI IPC | `cpu_busy_frac` |    Wall |
| -------- | -----------------: | -----------------------------: | -------: | ----------------: | ------: | --------------: | ------: |
| forward  |      149.3 / 149.3 |                38,597 / 46,589 |    5,185 |             1,094 |    0.45 |            0.72 | 100 min |
| backward |        29.0 / 29.0 |                37,729 / 46,296 |    5,140 |             2,077 |    0.48 |            0.77 |  26 min |

Native, `stream -s 448 -i 1` (swap over the whole process):

| Direction         | ROI, no limit | ROI, 410 MiB limit | Swapped in | Swapped out | `majflt` | Checksum    |
| ----------------- | ------------: | -----------------: | ---------: | ----------: | -------: | ----------- |
| forward           |      0.0238 s |            0.617 s |    107 MiB |     195 MiB |    8,457 | `403838933` |
| backward (`-d 1`) |      0.0260 s |            0.462 s |     53 MiB |      92 MiB |    2,962 | `403838933` |

No swap traffic without the limit.

## Findings

**F1 · Established: while the working set fits, A, C and D are the same machine.** Across `chase` at 64 and 256 MiB and `stream` at 320 and 384 MiB the three configs agree within 0.5% on latency and within 2.1% on bandwidth, with identical checksums and no swap traffic. Extra fast memory (C) and an idle swap device (D) cost nothing. The bandwidth spread is run-to-run variation between single runs; nothing in it orders the configs.

**F2 · Established: the walk caches cut `chase` latency by 9%.** 113.5 → ~102.9 ns/hop at 64 MiB, because page-table walks now hit a cache instead of the L2. Full-system numbers measured before the switch (A and C in #12/#13) are not comparable with these.

**F3 · Established: latency grows with the working set even when it fits.** `chase` goes from ~103 ns/hop at 64 MiB to ~134 ns/hop at 256 MiB on all three configs, consistent with more page-walk and TLB misses over a larger footprint. (Native `chase` at 64 MiB measured 134 ns/hop in finding 001; the simulated machine is not calibrated against native, so the coincidence means nothing yet. Phase 2 does that calibration.)

**F4 · Established: just past capacity, A fails, D survives slowly, C runs at full speed.** At 448 MiB, A's guest kernel kills `stream` during setup in both directions (out of memory: no swap, no other tier). D completes, and C runs it at the same speed as smaller sizes. This is the capacity argument of the project in miniature: past fast-memory capacity, the baseline cannot run the job at all.

**F5 · Established: D costs 10× against the ideal going backward and 51× going forward.** At 448 MiB, C → D: 5.69 → 0.565 GB/s backward (10.1×) and 6.52 → 0.127 GB/s forward (51×); compare each direction with itself (F6). Natively, the 410 MiB limit costs 17.7× backward and 25.9× forward against no limit, so the simulated guest pays more than native, by 1.8× backward and 6.0× forward in ROI time (0.832 vs 0.462 s, 3.71 vs 0.617 s). Possible contributors are the 80 µs disk delay, the IDE model's lack of queueing, and the guest's tighter memory (F10). These are the numbers config B has to beat.

**F6 · Established (effect) / tentative (cause): `-d 1` is slower even without swapping.** In C, the backward pass executes 36% more instructions (103.7M vs 76.2M) and is 13% slower (5.69 vs 6.52 GB/s); natively, without a limit, it is 9% slower (0.0260 vs 0.0238 s). Most likely the compiler vectorises the backward loop less well. Backward runs must be compared with backward runs.

**F7 · Established: sweep direction decides how much of the paging lands in the ROI, more strongly in gem5 than natively.** In D, the forward ROI moves 5.1× the swap traffic of the backward ROI (149 vs 29 MiB each way) and takes 4.5× longer (3.71 vs 0.832 s). Natively, under the limit, forward is 1.34× slower than backward with about twice the swap traffic. Setup fills the arrays front to back, so a forward pass starts on the swapped-out front and keeps evicting pages it is about to need; a backward pass starts on the resident end and evicts pages it has finished with.

**F8 · Tentative: backward defers its paging to after the ROI rather than avoiding it.** Over the whole process, D forward and backward swap almost the same amount (38,597 vs 37,729 pages in), so the difference is when. Forward swaps in only 385 pages outside its ROI, so setup itself swaps in almost nothing; backward swaps in ~30,300 pages outside its ROI, which leaves the final checksum read over A. Forward's ROI ends with A resident, so its checksum is cheap. This assumes setup pages the same way in both runs, which the identical code and seed make likely but which is not measured separately.

**F9 · Established: the guest has ≥ ~386 MiB for a workload, and < 448 MiB.** `stream -s 384` allocated 98,806 pages (~386 MiB) on D without swapping, and ran on A; at 448 MiB, A ran out, and D's setup sent ~8,400 pages (~33 MiB) to swap (forward's whole-process swap-out minus its ROI's). The kernel, page cache, the Python runner and the shell take the rest of the 512 MiB (`MemTotal` 469,524 KiB).

**F10 · Established: overflow ROIs cost ~65,000–75,000 guest instructions per page moved, and ~27–31 min of wall time per simulated second.** Counting the ROI's own swap traffic (`disk_in_bytes` + `disk_out_bytes`) and subtracting the pass itself (C's instructions in the same direction): forward 4.92B instructions for 76,434 pages moved (64,000 per page), backward 1.09B for 14,869 (74,000 per page). The CPU is busy 72–77% of these ROIs with kernel paging work, and idle the rest, waiting on the disk. The timing CPU simulates ~0.8M instructions per wall second in them (D forward: 5.0B instructions in 100 min, boot included), against ~0.3M for memory-bound user code that does not page (C forward at 448 MiB: 76M in 252 s, boot included), with three gem5 processes sharing the laptop. A ROI of 1 s simulated takes about half an hour of wall time, so overflow ROIs should be kept to ~0.1–0.2 s simulated, with configs run in parallel (one host core and ~1 GB of host RAM each).

**F11 · Tentative: under a similar limit, the guest swaps as much as or more than native.** Over the whole process, the guest swaps in 1.4× native going forward (151 vs 107 MiB) and 2.8× going backward (147 vs 53 MiB), and swaps out about the same going forward (182 vs 195 MiB) and 2.0× going backward (181 vs 92 MiB). Natively only the workload's cgroup is capped at 410 MiB; in the guest the kernel, page cache and other processes share 512 MiB with it, so pressure is system-wide. Not a like-for-like comparison.

**F12 · Hypothesis: transparent-huge-page compaction runs while the guest swaps.** `migrate_success` is 1,094 (D forward) and 2,077 (D backward) on a single-node machine, and 109 in A backward before it was killed. Without a second node that can only be memory compaction, most likely the kernel assembling huge pages under pressure. It adds kernel work and will mix into config B's migration counters.

**F13 · Established: the first swapping runs exposed two gem5 page-walker problems, both fixed.** (1) The X86 walker re-sent a read _response_ as its page-table-entry write, still flagged as cache-supplied, tripping `CoherentXBar`'s `is_express_snoop == cache_responding` assertion; fixed in gem5 `618cf87`, still present in upstream gem5 `develop`. (2) With `PrivateL1PrivateL2CacheHierarchy` the walker sits uncached on the L2 bus, and gem5's classic cache panics when its write hits a line the L1D holds Modified; fixed by switching to `PrivateL1PrivateL2WalkCacheHierarchy`. Both only trigger when the guest rewrites page tables during the ROI, so config B's page migration would have hit them too.

## Gaps in the measurement

- **The guest's Linux counters cover the whole process** (allocation, ROI and the final checksum read). gem5's `disk_*_bytes` give ROI-only swap traffic, but faults, migrations and native swap are still whole-process, which is why F8 rests on an assumption.
- **The native runs are not on the simulated machine**, so native vs gem5 is an order-of-magnitude comparison only.
- **What a workload can actually use** (F9) is inferred, not recorded: `GUESTINFO` should report `MemAvailable` just before the workload starts.
- **When exactly A was killed** (allocation or first touch) is not recorded; only that it never reached the ROI.
- **Wall times are with three gem5 processes sharing one laptop** and include boot; they are for planning, not results.

## Future work

- Config B (no slow DRAM tier yet). The 448 MiB `stream` runs are the first A vs B vs C vs D comparison once it exists: A fails, C gives 6.52 / 5.69 GB/s and D 0.127 / 0.565 GB/s (forward / backward), and where B lands between them is the first real answer.
- Random-access or skewed workloads over capacity: only `stream` was run past 384 MiB. Workloads with a hot subset (`kvstore -z 0.99`, `graph`) at sizes modestly above ~400 MiB are the cheaper ones to simulate; a single cold sequential pass over capacity is the most expensive case (F10).
- A fairer stand-in for an NVMe SSD than 80 µs per request: gem5's IDE model has no NVMe queueing or bandwidth model.
- Repeatability: every point is a single run, so differences under ~5% are not meaningful.
- Transparent-huge-page policy (e.g. `transparent_hugepage=never` on the kernel command line for all configs), decided before config B so its migration counters mean NUMA migration (F12).
- `MemAvailable` in `GUESTINFO`, and Linux counters snapshotted at ROI begin and end so faults and migrations can be reported for the ROI alone.
