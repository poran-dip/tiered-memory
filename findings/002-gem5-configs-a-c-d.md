# 002 · gem5 configs A, C and D: identical while the working set fits; past it, A dies and D slows ~10×

_2026-10-13 · data: `fs_result.json` in each run directory below (gem5 `m5out/` is not committed;
the numbers are copied here) · gem5: poran-dip/gem5 `618cf87` (SRAT + page-walker fix), X86
full-system, Ubuntu 24.04 kernel 6.8.0-52, `PrivateL1PrivateL2WalkCacheHierarchy`_

## What was run

| Config | File                    | Memory                                                        |
| ------ | ----------------------- | ------------------------------------------------------------- |
| A      | `configs/a_baseline.py` | 512 MiB fast DDR4-2400                                        |
| C      | `configs/c_ideal.py`    | 2560 MiB fast DDR4-2400 (B's total capacity, all fast)        |
| D      | `configs/d_swap.py`     | 512 MiB fast + 2 GiB swap on a second IDE disk, 80 µs/request |

Same machine otherwise: one X86 core at 3 GHz, 32 KiB L1s, 256 KiB L2, 8 KiB walk caches. Boot and
workload setup on KVM (no perf counters, WSL2 host); ROI on the timing CPU.

```bash
third_party/gem5/build/X86/gem5.opt -d m5out/<run> configs/<config>.py \
    --workload <wl> --kvm-no-perf -- -s <MiB> -i 1 [-d 1]
```

`chase` is a dependent pointer chase (latency); `stream` is one STREAM-triad pass (`-i 1`); `-d 1`
sweeps `stream`'s ROI backwards. Every point is a single run. Each run's working set was checked
against the `ws_mb` in its own `RESULT` line.

For comparison, the same `stream` binary natively on the author's PC (WSL2), limited with
`systemd-run --scope -p MemoryMax=410M -p MemorySwapMax=4G`, swap counted from `/proc/vmstat`.

## Results

### While the working set fits

| Workload        | A            | C            | D            | Checksum (all three) |
| --------------- | ------------ | ------------ | ------------ | -------------------- |
| `chase -s 64`   | 103.1 ns/hop | 102.8 ns/hop | 102.8 ns/hop | `549755289600`       |
| `chase -s 256`  | 134.2 ns/hop | 134.5 ns/hop | 134.0 ns/hop | `8796090925056`      |
| `stream -s 320` | 6.67 GB/s    | 6.70 GB/s    | 6.66 GB/s    | `288509703`          |
| `stream -s 384` | 6.50 GB/s    | 6.31 GB/s    | 6.75 GB/s    | `346068554`          |

No swap traffic in any of these. ROIs: `chase -s 64` 0.108 s simulated / 8.2M instructions;
`chase -s 256` 0.56 s / 28–32M; `stream -s 320` 0.050 s / 54–56M; `stream -s 384` 0.06 s / 65–67M.
Before the walk caches were added, `chase -s 64` measured 113.5 ns/hop on A and C.

### At 448 MiB, past what A and D can hold

| Run                  | A                             | C (fits)                                 | D                                         |
| -------------------- | ----------------------------- | ---------------------------------------- | ----------------------------------------- |
| `stream -s 448`      | not run                       | **6.42 GB/s**, 0.073 s ROI, 77.4M insts  | not measured³                             |
| `stream -s 448 -d 1` | **killed** (`rc=-9`) in setup | **5.57 GB/s**, 0.084 s ROI, 105.8M insts | **0.589 GB/s**, 0.798 s ROI, 1.10 B insts |

³ Started twice; both attempts ended without a clean measurement (one stopped by hand after 35 min
of wall time, still in its ROI; one left running in an output directory that the other had reused,
so its statistics were lost). To rerun.

All completed runs at 448 MiB give checksum `403838933`. D backward's swap traffic over the whole
process (setup, ROI and the final checksum read): 38,026 pages in / 46,987 out (~149 / 184 MiB). It
also had `majflt` 5,125 (≈ 7.4 pages per fault, the kernel's
8-page swap readahead); guest `sys` 5.65 s vs `user` 0.23 s; `migrate_success` 3,941 on a
one-node machine; ROI IPC 0.46; 24 min wall in total.

Native, `stream -s 448 -i 1`, 410 MiB limit:

| Direction         |     ROI | Swapped in | Swapped out | Checksum    |
| ----------------- | ------: | ---------: | ----------: | ----------- |
| forward           | 1.267 s |     90 MiB |     189 MiB | `403838933` |
| backward (`-d 1`) | 0.478 s |     54 MiB |      91 MiB | `403838933` |

## Findings

**F1 · Established: while the working set fits, A, C and D are the same machine.** Across
`chase` at 64 and 256 MiB and `stream` at 320 and 384 MiB the three configs agree within 0.4% on
latency and within ±4% on bandwidth, with identical checksums and no swap traffic. Extra fast
memory (C) and an idle swap device (D) cost nothing. The ±4% on `stream` is run-to-run variation
between single runs; nothing in it orders the configs.

**F2 · Established: the walk caches cut `chase` latency by 9%.** 113.5 → ~102.9 ns/hop at 64 MiB,
because page-table walks now hit a cache instead of the L2. Full-system numbers measured before
the switch (A and C in #12/#13) are not comparable with these.

**F3 · Established: latency grows with the working set even when it fits.** `chase` goes from
~103 ns/hop at 64 MiB to ~134 ns/hop at 256 MiB on all three configs, consistent with more
page-walk and TLB misses over a larger footprint. (Native `chase` at 64 MiB measured 134 ns/hop in
finding 001; the simulated machine is not calibrated against native, so the coincidence means
nothing yet. Phase 2 does that calibration.)

**F4 · Established: just past capacity, A fails, D survives slowly, C runs at full speed.** At
448 MiB, A's guest kernel kills `stream` during setup (out of memory: no swap, no other tier). D
completes, and C runs it at the same speed as smaller sizes. This is the brief's capacity
argument in miniature: past fast-memory capacity, the baseline cannot run the job at all.

**F5 · Established: D's swap costs ~10× against the ideal.** Backward pass at 448 MiB: 5.57 →
0.589 GB/s (9.5×; C backward vs D backward, see F6). The simulated guest is slower than native
under a similar limit (0.80 s vs 0.48 s ROI, 1.7×); possible contributors are the 80 µs disk delay,
the IDE model's lack of queueing, and the guest's tighter memory (F10). This is the number config B
has to beat. The forward pass, which swaps more natively (F7), is not yet measured in gem5.

**F6 · Established (effect) / tentative (cause): `-d 1` is slower even without swapping.** In C,
the backward pass executes 37% more instructions (105.8M vs 77.4M) and is 13% slower (5.57 vs
6.42 GB/s). Most likely the compiler vectorises the backward loop less well. Backward runs must be
compared with backward runs.

**F7 · Established (native only): sweep direction changes swap cost.** Natively, forward was
2.7× slower than backward with about twice the swap traffic; setup fills the arrays front to back,
so a forward pass starts on the swapped-out front and keeps evicting pages it is about to need.
The gem5 forward run has not been measured cleanly yet (see ³).

**F8 · Established: the guest has ≥ ~386 MiB for a workload, and < 448 MiB.** `stream -s 384`
allocated 98,759 pages (~386 MiB) on D without swapping, and ran on A; at 448 MiB, A ran out and D
sent 33–38 MiB to swap during setup. The kernel, page cache, the Python runner and the shell take
the rest of the 512 MiB (`MemTotal` 469,524 KiB).

**F9 · Established: overflow ROIs cost ~10,000 guest instructions per page moved, and ~29 min of
wall time per simulated second.** D backward executed 1.10 B instructions in its ROI; the pass
itself needs ~106M (C backward), leaving ~1.0 B for the ~85,000 pages moved (an upper bound on
pages, since setup's paging is in the counts too). The guest CPU is busy the whole time with
kernel paging work (IPC 0.46), not idle waiting on the disk, and the timing CPU simulates
~0.75–1.0M instructions per wall second. A ROI that takes 1 s natively takes about half an hour
here.

**F10 · Tentative: the guest swaps 2–3× more than native at the "same" limit.** For the backward pass, ~149 / 184 MiB in
gem5 vs 54 / 91 MiB natively. Natively only the workload's cgroup is capped at 410 MiB; in
the guest the kernel, page cache and other processes share 512 MiB with it, so pressure is
system-wide. Not a like-for-like comparison.

**F11 · Hypothesis: transparent-huge-page compaction runs during the ROI.** `migrate_success`
3,941 on a single-node machine can only be memory compaction, most likely the kernel assembling
huge pages under pressure. It adds kernel work and will mix into config B's migration counters.

**F12 · Established: the first swapping runs exposed two gem5 page-walker problems, both fixed.**
(1) The X86 walker re-sent a read _response_ as its page-table-entry write, still flagged as
cache-supplied, tripping `CoherentXBar`'s `is_express_snoop == cache_responding` assertion; fixed
in gem5 `618cf87`, still present in upstream gem5 `develop`. (2) With
`PrivateL1PrivateL2CacheHierarchy` the walker sits uncached on the L2 bus, and gem5's classic
cache panics when its write hits a line the L1D holds Modified; fixed by switching to
`PrivateL1PrivateL2WalkCacheHierarchy`. Both only trigger when the guest rewrites page tables
during the ROI, so config B's page migration would have hit them too.

## What this does not show

- Anything about config B (no slow DRAM tier yet).
- Random-access or skewed workloads over capacity: only `stream` was run past 384 MiB.
- Whether 80 µs per request is a fair stand-in for an NVMe SSD; gem5's IDE model has no NVMe
  queueing or bandwidth model.
- Repeatability: every point is a single run, so differences under ~5% are not meaningful.

## Gaps in the measurement

- **Full-system results are not in `results/` yet.** They live in uncommitted
  `m5out/*/fs_result.json`; the numbers above were copied by hand. A collector into
  `results/gem5_fs.csv` is needed.
- **Guest-side counters cover the whole process** (allocation, ROI and the final checksum read),
  not only the ROI; F7 and F9 need ROI-only counters.
- **No native no-limit timing at 448 MiB**, and the native runs are not on the simulated machine,
  so native vs gem5 is an order-of-magnitude comparison only.
- **What a workload can actually use** (F8) is inferred, not recorded: `GUESTINFO` should report
  `MemAvailable` just before the workload starts.
- **When exactly A was killed** (allocation or first touch) is not recorded; only that it never
  reached the ROI.
- **D forward at 448 MiB is not measured** (see ³). Lesson from it: never reuse a run directory
  while a previous gem5 process might still be writing to it; check `pgrep -af gem5.opt` first.

## Changes to the plan

1. **Budget overflow runs in simulated seconds:** ~29 min of wall time per simulated second of busy
   guest CPU. Keep overflow ROIs to ~0.1–0.2 s simulated, and run configs in parallel (one core
   each; ~1 GB of host RAM per run).
2. **For "exceeds memory", prefer workloads with a hot subset** (`kvstore -z 0.99`, `graph`) and
   sizes modestly above ~400 MiB. A single cold sequential pass over capacity is the most
   expensive case to simulate.
3. **Decide the transparent-huge-page policy before config B** (e.g. `transparent_hugepage=never`
   on the kernel command line for all configs), so B's migration counters mean NUMA migration.
4. **Collect full-system results into a CSV**, add `MemAvailable` to `GUESTINFO`, and snapshot the
   Linux counters at ROI begin and end so ROI-only swap and migration traffic can be reported.
5. **Use the 448 MiB `stream` runs as the first A vs B vs C vs D comparison** once B exists: A
   fails, C = 5.57 GB/s and D = 0.589 GB/s backward. Where B lands between them is the project's
   first real answer.
6. **Measure D forward at 448 MiB cleanly** (own directory, probably 1–2 h of wall time).
