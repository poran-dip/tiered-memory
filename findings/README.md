# Findings

One file per result, numbered in the order they were found. Each states what was run, what the data shows, its gaps, and the work it leaves. Raw data lives in `results/`; a finding points at the CSV (and, for gem5 full-system runs, the run directories in `results/fs/`) and the command that produced it.

Status of a finding: **established** (the data supports it and the measurement is sound), **tentative** (consistent with the data, but not yet tested directly), or **hypothesis** (an expectation the data suggests, to be tested later).

Markdown here is written one line per paragraph (or list item); renderers handle the wrapping.

| #                                   | Finding                                                                                                                                                                                                                                                                  | Source                                                | Date       |
| ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------- | ---------- |
| [001](001-native-capacity-cliff.md) | With swap as the only overflow, workloads either degrade gradually or fall off a cliff once they no longer fit in memory                                                                                                                                                 | `results/native_sweep.csv`                            | 2026-10-08 |
| [002](002-gem5-configs-a-c-d.md)    | gem5 A, C and D are identical while the working set fits; at 448 MiB A is OOM-killed, C runs at full speed and D completes 10× (backward) to 51× (forward) slower; paging costs ~64,000–74,000 guest instructions per page and ~30 min of wall time per simulated second | `results/gem5_fs.csv`, `results/native_stream448.csv` | 2026-10-15 |
