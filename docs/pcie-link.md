# The PCIe link in front of config B's slow tier

Config B's node 1 is DDR3 behind a PCIe link. The link is a gem5 SimObject in our gem5 fork,
`PCIeMemLink` (`src/mem/pcie_mem_link.{hh,cc}`, `src/mem/PCIeMemLink.py`). It sits between the
memory bus and the slow DRAM's controller, so node 1 is still ordinary cacheable system memory to
Linux (one SRAT node, no driver), but every access to it pays for the trip:

```
CPU caches -> memory bus -> root port --MRd/MWr TLPs--> device controller -> DDR3 controller
                                      <----CplD TLPs---                    <-
```

`--link none` takes the link out (DDR3 straight on the memory bus, as config B was before the
link existed). That run isolates how much of node 1's cost comes from the DRAM's timing alone.

The gem5-free mirror of the model is `tiered_memory/pcie.py`. It holds the link specs, the
latency presets, and the expected idle latency and peak bandwidth that every B run records
(`link_expect_rd_ns`, `link_expect_rd_gbs`).

## What is modeled

| Part              | Model                                                                                                                                                                                                                                                                | Parameter (CLI)                                                         | Default                         |
| ----------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------- | ------------------------------- |
| Link rate         | Each direction is a serializer at lanes x GT/s, less line encoding (8b/10b for Gen1-2, 128b/130b for Gen3-5) and data-link-layer overhead. Requests go down, completions come up, both at once (full duplex).                                                        | `--link genNxM`                                                         | `gen4x8`                        |
| DLL overhead      | ACK/NAK and flow-control-update DLLPs plus ordered sets, as a fraction of the link rate.                                                                                                                                                                             | `dll_overhead`                                                          | 8%                              |
| TLPs              | MRd (header only) down, CplD (header + data) up; MWr (header + data) down. Headers are 3 DW (12 B) below 4 GiB and 4 DW above. Each TLP adds 8 B: 2 B framing, 2 B sequence number, 4 B LCRC. ECRC is optional (+4 B).                                               | `tlp_overhead`, `ecrc`                                                  | 8 B, no ECRC                    |
| Splitting         | Data split at Max Payload Size, read requests at Max Read Request Size. A 64 B line is always one TLP.                                                                                                                                                               | `max_payload`, `max_read_req`                                           | 256 B, 512 B                    |
| Fixed latency     | Root port, paid once by a request going out and once by its completion coming back. PHY + wire, paid on each one-way trip. Device controller, paid once each way.                                                                                                    | `--link-latency`, `--rc-latency`, `--phy-latency`, `--dev-latency` (ns) | preset `pcie`: 100 / 30 / 80 ns |
| Outstanding reads | Tags. A read is refused (the bus retries) when all tags are in use.                                                                                                                                                                                                  | `--link-tags`                                                           | 256 (8-bit extended tags)       |
| Flow control      | Each request needs a device buffer credit, returned one link trip after the device hands it to the DDR3 controller. With no credit the request waits at the root port (`creditStallTicks`). Completions need none: root ports advertise infinite completion credits. | `--link-credits`                                                        | 64                              |
| Root port queue   | Requests queued at the root port before it back-pressures the memory bus.                                                                                                                                                                                            | `host_queue`                                                            | 128                             |
| Posted writes     | Cache writebacks need no response, so they cost downstream bandwidth and a credit but no tag or completion.                                                                                                                                                          |                                                                         |                                 |

The device's DRAM is a separate choice: `--slow-dram ddr3-1600` (default; gem5 has no DDR2
model), `ddr3-2133`, or `ddr4-2400`. With `ddr4-2400`, node 1's DRAM is identical to node 0's,
so the link is the only difference.

## Latency presets and their sources

| Preset | rc / phy / dev (ns) | Added idle 64 B read (Gen4 x8) | Anchored to                                                                                                                                                                                                                                                                                                                           |
| ------ | ------------------- | ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `pcie` | 100 / 30 / 80       | 427 ns                         | Neugebauer et al., [_Understanding PCIe performance for end host networking_](https://www.cl.cam.ac.uk/research/srg/netos/projects/pcie-bench/), SIGCOMM 2018: a 64 B DMA read on Gen3 x8 takes 520 ns minimum and 547 ns median. That includes roughly 100 ns of host DRAM, which leaves about 420-450 ns for PCIe and the endpoint. |
| `cxl`  | 35 / 15 / 25        | 157 ns                         | CXL-DMSim ([arXiv 2411.02282](https://arxiv.org/abs/2411.02282)), calibrated against real silicon: 284 ns for a CXL ASIC expander vs 130 ns for local DDR5, so +154 ns. Use it as the best case for a purpose-built device.                                                                                                           |

The rc/phy/dev split within a preset is a modeling choice. Only the round trip is anchored, and
only the round trip shows up in the results.

TLP sizes, framing bytes, the DLL overhead fraction, MPS 256 B, MRRS 512 B and the 64 B read
completion boundary all come from the same Neugebauer et al. paper (Sec. 3, their bandwidth
model). With those, the model's Gen3 x8 rate is 7.25 GB/s per direction, against their 7.24 GB/s
(57.88 Gb/s) at the TLP layer.

## What to expect (from `tiered_memory/pcie.py`)

| Link      | Preset | Usable rate per direction (GB/s) | Added idle read (ns) | Max read payload (GB/s) |
| --------- | ------ | -------------------------------: | -------------------: | ----------------------: |
| `gen3x4`  | pcie   |                             3.62 |                  449 |                    2.76 |
| `gen3x8`  | pcie   |                             7.25 |                  434 |                    5.52 |
| `gen4x8`  | pcie   |                            14.49 |                  427 |                   11.04 |
| `gen4x16` | pcie   |                            28.99 |                  424 |                   22.09 |
| `gen5x16` | pcie   |                            57.97 |                  422 |                   44.17 |
| `gen4x8`  | cxl    |                            14.49 |                  157 |                   11.04 |
| `gen5x16` | cxl    |                            57.97 |                  152 |                   44.17 |

A 64 B read crosses as 20 B down and 84 B up, so at most 76% of the upstream rate is payload. A
single DDR3-1600 channel peaks at 12.8 GB/s, so the default Gen4 x8 link is the narrower of the
two, but only just. Gen3 x4 is clearly link-bound, and Gen4 x16 and up are DRAM-bound.

## Checking it

1. **The link alone** (seconds, no OS): `configs/link_check.py` drives the link and its DDR3 with
   a traffic generator and compares the link's stats with the expected numbers above.

   ```bash
   G=third_party/gem5/build/X86/gem5.opt
   $G -d m5out/link-lat     configs/link_check.py --mode latency
   $G -d m5out/link-lat-cxl configs/link_check.py --mode latency --link gen5x16 --link-latency cxl
   $G -d m5out/link-bw      configs/link_check.py --mode bandwidth --link gen3x4
   $G -d m5out/link-bw-wr   configs/link_check.py --mode bandwidth --link gen3x4 --writes
   ```

   Each prints `CHECK ok` or `CHECK FAIL` and exits non-zero on a failure (tolerance 10%).

2. **Phase 2 in full system**: `scripts/fs_runs.sh '^b-chase-64-node|^b-stream-320-node'` binds
   `chase` and `stream` to one node with `numactl`, with the link and with `--link none`:
   - `chase` `ns_per_work` (ns per hop): node1 minus node0 should be roughly `link_expect_rd_ns`
     plus the DDR3-vs-DDR4 gap. The `-nolink` run measures that gap on its own.
   - `stream` throughput on node 1 should sit below the link's limit (`link_expect_rd_gbs`; stream
     also writes, which share the downstream direction). `link_up_util` near 1 means the link is
     the bottleneck.
   - `link_rd_lat_ns` is the link's own measured round trip, DRAM included.

## Columns

Every B run with a link adds these columns. Runs without a link (A, C, D, `--link none`, and
every run from before the link) have none of them:

- `link_down_bytes`, `link_up_bytes`, `link_tlps`, `link_bw_gbs`: what crossed the wire,
  headers and framing included.
- `link_efficiency`: the payload share of those bytes.
- `link_down_util`, `link_up_util`: the share of the ROI each direction was transmitting.
- `link_rd_lat_ns`: the average round trip of non-posted requests, measured at the link.
- `link_credit_stall_frac`: the share of the ROI a ready request waited for a device credit.
- `link_refusals`: requests the bus had to retry (root-port queue full or out of tags).

They come from the run's settings: `link`, `link_latency`, `link_rc_ns`, `link_phy_ns`,
`link_dev_ns`, `link_tags`, `link_credits`, `link_expect_rd_ns`, `link_expect_rd_gbs`,
`slow_dram`.

`pcie_rd_bytes` / `pcie_wr_bytes` / `pcie_bw_gbs` keep their old meaning for every run: payload at
the slow controller. Rows from before the link and rows after stay comparable.

## Not modeled

- **Coherent, cacheable access over plain PCIe.** A real non-CXL PCIe card exposes its memory
  through a BAR, normally mapped uncached or write-combining, and Linux cannot treat it as a
  normal NUMA node without a driver. The model gives node 1 CXL.mem-style semantics (cacheable,
  hot-pluggable system RAM) with PCIe's costs. That is the best case for B, and it belongs in the
  report's limitations.
- **Non-posted writes.** Writes that need a response (DMA `WriteReq`/`WriteLineReq` from the
  disk, rare in the ROI) wait for a header-only completion. Real PCIe posts them. Cache
  writebacks, the bulk of write traffic, are posted correctly.
- **Errors and replays**, SKP ordered sets beyond the DLL overhead fraction, link power states,
  PCIe switches and retimers (add their latency to `--phy-latency`), and Gen6 FLIT mode.
- **HMAT.** No ACPI HMAT is generated, so the stock kernel puts node 1 in the same memory tier as
  node 0 (`guest_memory_tiers`). Demotion and promotion stay off. With no SLIT
  either, the kernel's only sign that node 1 is far is its default distance of 20. Describing the link's latency and
  bandwidth in an HMAT is the next step if Phase 3 should test the kernel's tiering path.
- **Atomic mode** (KVM boot, fast-forward) uses the idle latency without queueing, and the atomic
  CPU's backdoor skips the link. Neither is ever inside the ROI.
