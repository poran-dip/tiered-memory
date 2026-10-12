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

The gem5 side currently runs in syscall-emulation (SE) mode with a single DRAM channel. SE mode has no OS, so NUMA placement, paging, swap and migration only appear once Phase 1 moves to full-system mode.

## Repo layout

```
workloads/          C++ benchmarks with gem5 ROI markers (shared harness: common.h)
configs/run.py      gem5 config: one workload, configurable DRAM
configs/sweep.py    gem5 sweep over DRAM size -> results/gem5_sweep.csv
native/sweep_ram.py real machine: cgroup memory limit with swap, finds the slowdown knee
native/bench.py     real machine: working-set size sweep
tiered_memory/      measurement code shared by both sweeps
tests/              pytest suite for tiered_memory/ and the sweeps
scripts/setup.sh    fetches gem5 at a pinned commit and builds everything
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
scripts/setup.sh m5 wl          # any subset of: fetch gem5 m5 wl
```

`GEM5_REF` is the reproducibility pin for every result: change it deliberately, and override it for a one-off with `GEM5_REF=<commit> scripts/setup.sh`. `GEM5_REPO`, `GEM5_DIR` and `JOBS` can be overridden the same way.

The workloads build into `workloads/build/m5/` (static, with ROI markers, for gem5) and `workloads/build/native/` (for real-machine runs).

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
