# HMAT dataset setup and remaining validation

The gem5 fork is pinned to `68e1deebddfb3186e61229ee9583255327acd846`.
This change wires its HMAT classes into config B; no additional simulator patch
is required. Legacy runs omit `--hmat` and retain the original resource kernel.

## Required before starting v2

1. Select a gem5-compatible x86 kernel ELF with CPU-less-node HMAT memory-tier
   support. The existing resource is Ubuntu 6.8 and is not the v2 kernel.
   A newer version string alone is not evidence of the required kernel config
   and support. The guest must expose HMAT and place node 1 in a more distant
   tier than node 0. Config B now rejects the run before the workload otherwise.
2. Rebuild the guest disk after these source changes: `scripts/setup.sh disk`.
   This copies the guest runner and installs the tiering sudoers permissions.
   The kernel override does not change the disk; a custom kernel must include
   the drivers needed to boot this disk without a separate initramfs.
3. Commit the application changes before collecting a dataset. The runner
   checks both repositories for uncommitted changes, excluding parent results.
   `--allow-dirty` is available for development, not reproducible collection.
4. Use an absolute kernel path for all stages:

   ```bash
   export TM_KERNEL=/absolute/path/to/vmlinux
   scripts/run_v2.sh -n pilot
   scripts/run_v2.sh pilot
   ```

The first command only lists the plan; the second launches simulations. Neither
was executed during implementation. Without `TM_KERNEL`, non-dry v2 runs refuse
to start. Kernel path and SHA-256 are recorded for every run. The general batch
runner and individual configs also accept `--kernel PATH` directly.

## Firmware assumptions

`tiered_memory/hmat.py` defines the `dram-baseline-v1` firmware hint profile:
100 ns local DDR4, 150 ns DDR3, plus the configured link's unloaded 64-byte read
latency on the remote path. Single-channel bandwidths are 19,200 MB/s for
DDR4-2400, 12,800 MB/s for DDR3-1600, and 17,064 MB/s for DDR3-2133. Remote
bandwidth is additionally bounded by the smaller link read/write payload rate.

These are explicit modeling assumptions, not measured memory latencies. They
are stored in each B result. HMAT publishes access latency and access bandwidth
for CPU domain 0 to memory domains 0 and 1, in both ACPI root tables. Calibration
against simulated memory behavior is still pending; changing the profile needs
a new dataset. The baseline B variants retain kernel-default tiering settings;
the two `-tiering` variants explicitly enable demotion and memory-tier promotion.

## Outcomes, caps, and resuming

- Outcomes distinguish `completed`, `censored`, `expected_oom`, `setup_failed`,
  and `failed`. Expected A-over OOM requires a killed workload and an increase
  in the guest OOM-kill counter. A setup failure is never an accepted result.
- Resuming skips only accepted reports. Inspect failed/incomplete runs, then
  explicitly rerun with the batch runner's `--overwrite --only RUN` options.
- General batch cap defaults are zero, preserving standalone A/C/D workflows.
  The v2 launcher enables B=20x C time; D=5x longest B time and 100x C instructions.
- References must match exact workload arguments, kernel checksum, simulator
  and application commits, ROI CPU, clock, L2, and NUMA policy. B references
  must also match HMAT mode. D waits for every selected B variant, including
  queued/running overwrites; old CSV rows cannot satisfy an in-flight reference.
  The selected measurements and source commits are recorded in `cap_references`.
- Censored B runs are not completed references. If any selected B is censored,
  D's time-based comparison is blocked. Either finish those B runs with a larger
  cap or explicitly run D with `--cap-d-x-b 0 --cap-d-insts-x-c 100` to use only
  C's instruction budget. Do not describe that as a 5x-B time cap.
- Explicit ROI caps supersede the legacy 600-second ROI budget. The watchdog
  between exit events still applies. Without explicit caps, reaching the ROI
  watchdog budget now preserves a censored result too.
- Completed and censored ROIs preserve progress curves even when progress
  logging is disabled. Censored runs also save console-derived guest output so
  report rebuilding survives removal of ignored console files. Sort still has
  no percentage progress, so it cannot produce a linear completion estimate.

## Triage and validation status

Implemented: boolean readback normalization, HMAT attachment and guest checks,
explicit kernel selection/provenance, outcome classification, cap dependencies,
reference matching/provenance, budget reconciliation, dataset/value validation,
both-repository clean checks, and portable capped artifacts.

Remaining before trusting results: select/provide the newer guest kernel,
rebuild the guest disk, and observe one successful pilot including HMAT discovery,
distinct tiers, tiering-on readback, expected A-over OOM, and a capped ROI.
Unit tests, lint, builds, and simulations were deliberately not run. HMAT hint
calibration and gem5 serialization coverage remain deferred.
