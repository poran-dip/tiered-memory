# Findings

One file per result, numbered in the order they were found. Each states what was run, what the
data shows, what it does **not** show, and what it changes in the plan. Raw data lives in
`results/`; a finding points at the CSV and the command that produced it.

Status of a finding: **established** (the data supports it and the measurement is sound),
**tentative** (consistent with the data, but not yet tested directly), or **hypothesis** (an
expectation the data suggests, to be tested later).

| #                                   | Finding                                                                                                                                                                                            | Source                                         | Date       |
| ----------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------- | ---------- |
| [001](001-native-capacity-cliff.md) | With swap as the only overflow, workloads either degrade gradually or fall off a cliff once they no longer fit in memory                                                                           | `results/native_sweep.csv`                     | 2026-10-08 |
| [002](002-gem5-configs-a-c-d.md)    | gem5 A, C and D are identical while the working set fits; at 448 MiB A is OOM-killed, C runs at full speed and D completes ~10× slower; each simulated second of paging costs ~29 min of wall time | gem5 `m5out/*/fs_result.json`, native `stream` | 2026-10-13 |
