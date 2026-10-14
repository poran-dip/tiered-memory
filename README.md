# Software-Managed PCIe DRAM Memory Tier

Can a commodity PC with no CXL support benefit from a large, slow DRAM tier attached over PCIe, managed by **unmodified Linux** memory management? This repo models that system in [gem5](https://www.gem5.org/) and measures whether it ever beats the same machine without the extra memory.

## The question

A typical commodity system has a fixed amount of fast DRAM (DDR4/DDR5). CXL lets servers add more memory on a separate device, but most consumer PCs don't support it. The simpler alternative studied here: put cheap, older DRAM (DDR3/DDR2 class) behind PCIe and let Linux use it as a second NUMA node.

CXL is the inspiration, not the target. There is no custom page-placement algorithm at first; the point is to find out what the existing Linux NUMA and memory-tiering mechanisms do with the extra capacity.

```
                 CPU
                  │
          Linux VM subsystem
                  │
        ┌─────────┴─────────┐
   NUMA node 0          NUMA node 1
   Fast DRAM, 8 GB      Slow DRAM, 32 GB
   DDR4/5               PCIe + DDR3/DDR2
```

| Config             | Memory                           | Role                           |
| ------------------ | -------------------------------- | ------------------------------ |
| **A** Baseline     | 8 GB fast                        | What a commodity PC has        |
| **B** Proposed     | 8 GB fast + 32 GB slow PCIe DRAM | System under test              |
| **C** Upper bound  | 40 GB fast                       | Ceiling for B                  |
| **D** Conventional | 8 GB fast + NVMe/SSD swap        | Is PCIe DRAM better than swap? |

**Hypothesis.** H₀: adding the slow tier gives no measurable application-level benefit. H₁: for some workloads, especially those with working sets larger than 8 GB, B beats A. The first goal is narrow: find whether even one meaningful workload/configuration exists where B beats A. If none does, the investigation stops there.

## Status

| Phase | What                                                     | Status |
| ----- | -------------------------------------------------------- | ------ |
| 0     | Workloads, measurement reporting, native RAM-limit sweep | Done   |
| 1     | gem5 full-system: Linux sees fast node 0 and slow node 1 | Next   |
| 2     | Validate that node 1 is measurably slower                |        |
| 3     | Workload experiments: A vs B, then C and D               |        |
| 4     | PCIe modeling: bandwidth, latency, transaction overhead  |        |
| 5     | Analysis: whether and when B beats A                     |        |

The gem5 sweep (`configs/sweep.py`) runs in syscall-emulation (SE) mode with a single DRAM channel. SE mode has no OS, so NUMA placement, paging, swap and migration need full-system mode. Phase 1 adds it: `configs/a_baseline.py` boots the stock Ubuntu 24.04 kernel on X86 with config A's fast DRAM. Config B needs gem5 to describe the second NUMA node to Linux (an ACPI SRAT table), which comes next in the gem5 fork.

## Repo layout

```
workloads/          C++ benchmarks with gem5 ROI markers (shared harness: common.h)
configs/run.py      gem5 SE config: one workload, configurable DRAM
configs/sweep.py    gem5 SE sweep over DRAM size -> results/gem5_sweep.csv
configs/fsboard.py  shared gem5 full-system setup (X86, Ubuntu 24.04, KVM boot, ROI CPU switch)
configs/a_baseline.py  config A: 512 MiB fast DRAM, full-system
configs/c_ideal.py  config C: 2.5 GiB fast DRAM (B's total capacity)
configs/d_swap.py   config D: config A + 2 GiB swap on a second simulated disk
scripts/fs_runs.sh  runs the committed full-system set into results/fs/ (parallel with -j)
native/sweep_ram.py real machine: cgroup memory limit with swap, finds the slowdown knee
native/bench.py     real machine: working-set size sweep
tiered_memory/      measurement code shared by both sweeps
tests/              pytest suite for tiered_memory/ and the sweeps
scripts/setup.sh    fetches gem5 at a pinned commit, builds everything, prepares the FS disk
scripts/make_disk.sh  copies the Ubuntu disk image and adds the workloads (needs sudo)
docs/               measurements.md, literature-review.md
results/            committed result CSVs (native_sweep.csv, gem5_sweep.csv, ...)
findings/           what the results show, one numbered file per finding
third_party/gem5    gem5 checkout (not committed, see Setup)
```

## Workloads

Each workload takes `-s <MiB>` (working set), `-i <iters>` and `-r <seed>`, and prints one `RESULT` line ending in a checksum so any two runs can be confirmed to have done identical work.

| Workload   | Brief class      | Access pattern                                                                        | `work` unit |
| ---------- | ---------------- | ------------------------------------------------------------------------------------- | ----------- |
| `stream`   | bandwidth        | Sequential bandwidth (STREAM triad)                                                   | bytes       |
| `sort`     | random/irregular | Sorting 16-byte records, mixed access                                                 | compares    |
| `gemm`     | compute/matrix   | Blocked matrix multiply, cache-resident (compute control)                             | flops       |
| `chase`    | random/irregular | Dependent pointer chase, pure latency                                                 | hops        |
| `kvdecode` | CPU AI/LLM       | LLM decode: per-layer weight streaming plus a growing KV cache with softmax attention | bytes       |
| `graph`    | graph            | R-MAT power-law graph in CSR: BFS, then pull PageRank; hub vertices are hot           | edges       |
| `kvstore`  | database (OLTP)  | YCSB-B-style hash-table point lookups/updates, scrambled Zipfian keys (`-z`)          | ops         |
| `hashjoin` | database (OLAP)  | No-partition hash join: random build, sequential probe stream + random lookups        | tuples      |

`kvstore` also takes `-z <theta>` (Zipfian skew, default 0.99; `0` = uniform). The list of workloads, their units and default sizes lives in [`tiered_memory/workloads.py`](tiered_memory/workloads.py); every script reads it.

Still to add, both needing full-system Linux (Phase 1): compilation (e.g. `gcc -O2` on a large translation unit) and a real CPU LLM run (e.g. llama.cpp with a small model).

## Measurements

Every measurement in the project brief has a CSV column: execution time, throughput, IPC, memory bandwidth and latency, local vs remote accesses, page migration/promotion/demotion, swap, PCIe traffic and CPU utilization. [`docs/measurements.md`](docs/measurements.md) maps each one to its columns, where the number comes from, and its caveats. A blank cell means the counter is unavailable; `0` means it was measured and nothing happened.

## Setup

gem5 is not committed to this repo. `scripts/setup.sh` clones it into `third_party/gem5` at the commit pinned in the script (`GEM5_REF`), builds `gem5.opt` and the `m5` library, then builds the workloads:

```bash
scripts/setup.sh                # everything (the gem5 build takes a while)
scripts/setup.sh fetch          # only fetch gem5 at the pinned commit
scripts/setup.sh m5 wl          # any subset of: fetch gem5 m5 wl resources disk
```

`GEM5_REF` is the reproducibility pin for every result: change it deliberately, and override it for a one-off with `GEM5_REF=<commit> scripts/setup.sh`. `GEM5_REPO`, `GEM5_DIR` and `JOBS` can be overridden the same way.

The workloads build into `workloads/build/m5/` (static, with ROI markers, for gem5 SE mode), `workloads/build/m5-fs/` (static, ROI markers as address-mode hypercalls, for gem5 full-system) and `workloads/build/native/` (for real-machine runs).

Full-system runs also need the guest kernel and disk. `scripts/setup.sh resources` downloads the pinned gem5-resources Ubuntu 24.04 kernel and disk image (pinned in `configs/fetch_resources.py`) and records their paths in `third_party/resources.json`. `scripts/setup.sh disk` copies the disk to `third_party/disks/x86-ubuntu-24.04-tm.img` and installs the `m5-fs` workloads and `tiered_memory/` under `/opt/tiered-memory`; rerun it after changing either. The stock image is never modified.

KVM makes the boot take seconds instead of hours. Your user needs read/write access to `/dev/kvm` (`sudo usermod -aG kvm $USER`, then log in again; on WSL2 run `wsl --shutdown` from Windows). Without KVM, pass `--boot-cpu atomic`.

Python tooling (tests, sweeps outside gem5):

```bash
pip install pytest
python3 -m pytest
```

## Running

gem5 sweep (from the repo root):

```bash
python3 configs/sweep.py                       # all workloads, default DRAM sizes
python3 configs/sweep.py --workloads chase --mem 16 32
python3 configs/sweep.py --reparse             # rebuild the CSV from existing m5out/ runs
```

A single gem5 run:

```bash
third_party/gem5/build/X86/gem5.opt -d m5out/stream configs/run.py \
    --binary workloads/build/m5/stream --mem-size 64MiB -- -s 8 -i 2
```

A full-system run (config A, 512 MiB fast DRAM):

```bash
third_party/gem5/build/X86/gem5.opt -d results/fs/a-chase-64 configs/a_baseline.py \
    --workload chase --kvm-no-perf -- -s 64 -i 1
```

Runs whose `-d` directory is under `results/fs/` are the ones kept in git: each also adds a row to `results/gem5_fs.csv`, and `results/fs/.gitignore` keeps only the small files (`fs_result.json`, `roi_stats.json`, `guest_output.txt`, `run.json`). Use any other directory (e.g. `m5out/`) for scratch runs. `scripts/fs_runs.sh` runs the whole committed set, skipping runs that are already done:

```bash
scripts/fs_runs.sh -n            # list what would run
scripts/fs_runs.sh -j 3          # run, up to 3 gem5 processes at once
scripts/fs_runs.sh 'stream-448'  # only runs whose name matches
python3 -m tiered_memory.fsreport --csv results/gem5_fs.csv results/fs/*/   # rebuild the CSV
```

`configs/run_all_working_sets.py` runs every workload at each of its working-set tiers under configs A, C and D, into `results/fs/<config>-<workload>-<tier>-<MiB>`. The tiers (sizes in `tiered_memory/workloads.py`, `FS_TIERS`) are **fits** (a sanity size, at most 64 MiB), **near** (just fits in config A, 336–384 MiB) and **over** (past config A's capacity, 448 MiB). The fits tier runs first, then everything else longest-first, with a per-run wall-time cap:

```bash
python3 configs/run_all_working_sets.py -n -j 3      # list the runs and the time estimate
python3 configs/run_all_working_sets.py -j 3         # run them (finished runs are skipped)
python3 configs/run_all_working_sets.py --tiers fits # only the sanity sizes
```

The terminal prints the milestones (kernel booted, run script started, ROI begin/end) and a final `FS_RESULT {...}` line. With KVM the boot takes a few seconds of simulated time. Outputs in the `-d` directory:

- `fs_result.json`: the report (gem5 ROI columns, guest Linux columns, `guest` kernel and NUMA nodes). Rebuild it without gem5: `python3 -m tiered_memory.fsreport results/fs/a-chase-64` (works from `roi_stats.json` when `stats.txt` is absent, as in a fresh clone).
- `run.json`: how the run was started: config, workload arguments, config settings, gem5 and repo commits, host CPU/kernel/RAM, start time and wall time.
- `stats.txt`: block 1 is the ROI (all of it is in `roi_stats.json`); `roi_done` marks that the ROI completed.
- `guest_output.txt`: the guest's program output, copied out with gem5's `writefile`.
- `board.pc.com_1.device`: the guest's serial console, i.e. the boot log. Look here first when a run stops early.

Options:

- `--kvm-no-perf` is needed where the host has no hardware perf counters (WSL2, most VMs; check for `cpu` in `/sys/bus/event_source/devices/`).
- `--fast-size` sets the fast DRAM size (A: 512MiB, C: 2560MiB by default).
- `--max-sim-sec` (default 600) ends a run after that much simulated time with no progress. A guest kernel panic cannot stop gem5 while the KVM CPU runs, so this is what ends a failed run.
- `--progress-sec` (default 15): during the ROI, print a progress line about every this many wall-clock seconds: simulated time into the ROI, wall time, instructions so far and the current simulation speed (config D adds how much of the swap file has been written). `0` turns it off. Progress lines don't touch the stats.
- `--overwrite` replaces an earlier run's outputs in the `-d` directory. Without it a run refuses to start there, and it always refuses a directory another gem5 process is still writing to.
- `--interactive` boots to a shell instead of running the workload.

Config C (`configs/c_ideal.py`) and D (`configs/d_swap.py`) take the same options and workload arguments. D adds a 2 GiB swap device the guest sees as `/dev/sdb`, set up by the run script before the workload:

```bash
third_party/gem5/build/X86/gem5.opt -d m5out/d-chase-640 configs/d_swap.py \
    --workload chase --kvm-no-perf -- -s 640 -i 1
```

- `--swap-size` (default 2GiB, B's slow-tier size, so B and D add the same capacity).
- `--swap-delay` (default 80us): fixed latency per swap-disk request, standing in for an NVMe SSD's 4 KiB random read. gem5's IDE disk is not an NVMe model, so treat D as context for A vs B rather than a calibrated SSD.
- D needs the disk image rebuilt (`scripts/setup.sh disk`) after this change: it adds a sudoers rule so the guest's unprivileged user can run `mkswap`/`swapon`. If swap setup fails, the run stops before the workload with `SWAP SETUP FAILED` in `guest_output.txt`, rather than silently running as config A. The `guest` entry in `fs_result.json` reports `swap_total_kib`.

Native RAM-limit sweep (needs sudo for the cgroup; run on an otherwise idle machine, since the Linux counters are system-wide):

```bash
sudo python3 native/sweep_ram.py --descend
```

## Development

- Python: ruff + black (line length 100). C++: clang-format, clang-tidy. Markdown/JSON: Prettier, Biome (`pnpm format`).
- `pre-commit install` runs the formatters and linters on each commit.

## Known limitations

- gem5 simulates a few million instructions per second, so working sets may need to be scaled down from the 8 GB / 32 GB target, holding ratios constant rather than absolute sizes.
- Caches are kept small so that megabyte-scale working sets reach DRAM.
- Results are relative performance on a modeled system, not predictions for any specific product.

## License

Released under the [Apache License 2.0](LICENSE).

gem5 is fetched at build time and remains under its own BSD-3-Clause license.
