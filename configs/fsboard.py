"""Shared gem5 full-system setup for configs A-D (X86, stock Ubuntu 24.04 kernel and disk).

Imported by configs/<letter>_*.py, which run inside gem5:  gem5.opt configs/a_baseline.py ...

What a run does:
  1. Boot Linux on the KVM CPU (near-native speed, no timing) or, without KVM, the atomic CPU.
  2. The guest runs the script this config hands it through gem5's readfile:
     python3 -m tiered_memory.guest_run <workload> <args>  (from the disk image, see make_disk.sh)
  3. At ROI begin the workload issues hypercall 4: switch to the ROI CPU (timing or O3), reset stats.
  4. At ROI end it issues hypercall 5: dump stats (so block 1 of stats.txt is the ROI, exactly as in
     SE mode), switch back to the boot CPU for the teardown.
  5. When the script finishes the image issues hypercall 3 and the simulation exits.

Kernel and base disk come from gem5-resources (scripts/setup.sh resources writes their local paths to
third_party/resources.json); the disk actually booted is the copy with our binaries added
(scripts/setup.sh disk).
"""

import json
import shlex
import sys
from pathlib import Path

import m5
from gem5.components.boards.x86_board import X86Board
from gem5.components.cachehierarchies.classic.private_l1_private_l2_cache_hierarchy import (
    PrivateL1PrivateL2CacheHierarchy,
)
from gem5.components.processors.cpu_types import CPUTypes
from gem5.components.processors.simple_switchable_processor import SimpleSwitchableProcessor
from gem5.isas import ISA
from gem5.resources.resource import DiskImageResource, KernelResource
from gem5.simulate.exit_handler import WorkBeginExitHandler, WorkEndExitHandler
from gem5.simulate.simulator import Simulator
from gem5.utils.override import overrides
from gem5.utils.requires import requires

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tiered_memory.gem5stats import roi_block, summarize  # noqa: E402
from tiered_memory.result import parse_result, parse_tagged, throughput_cols  # noqa: E402
from tiered_memory.workloads import NAMES  # noqa: E402

RESOURCES_JSON = REPO / "third_party/resources.json"
DEFAULT_DISK = REPO / "third_party/disks/x86-ubuntu-24.04-tm.img"
GUEST_DIR = "/opt/tiered-memory"  # where make_disk.sh puts bin/ and tiered_memory/ in the image
CONSOLE_FILE = "board.pc.com_1.device"  # the guest's serial console, written to the outdir

CPU = {"kvm": CPUTypes.KVM, "atomic": CPUTypes.ATOMIC, "timing": CPUTypes.TIMING, "o3": CPUTypes.O3}


def add_common_args(p):
    """Arguments every full-system config shares. Workload args go after '--'."""
    p.add_argument("--workload", required=True, choices=NAMES)
    p.add_argument(
        "--boot-cpu",
        choices=["kvm", "atomic"],
        default="kvm",
        help="CPU outside the ROI; atomic is the (much slower) fallback without /dev/kvm",
    )
    p.add_argument("--roi-cpu", choices=["timing", "o3"], default="timing")
    p.add_argument(
        "--kvm-no-perf",
        action="store_true",
        help="run KVM without hardware perf counters (needed on WSL2 and most VMs)",
    )
    p.add_argument("--clk", default="3GHz")
    p.add_argument("--l1d", default="32KiB")
    p.add_argument("--l1i", default="32KiB")
    p.add_argument("--l2", default="256KiB", help="kept small, as in configs/run.py")
    p.add_argument("--disk", default=str(DEFAULT_DISK), help="disk image with our binaries")
    p.add_argument(
        "--interactive",
        action="store_true",
        help="boot to a shell instead of running the workload (attach with m5term)",
    )
    p.add_argument("wl_args", nargs="*", help="workload arguments, after '--'")


def _resources():
    if not RESOURCES_JSON.exists():
        sys.exit(f"{RESOURCES_JSON} missing: run scripts/setup.sh resources disk")
    return json.loads(RESOURCES_JSON.read_text())


def run_script(workload, wl_args):
    """The shell script the guest runs after boot (gem5 readfile)."""
    cmd = shlex.join(["python3", "-m", "tiered_memory.guest_run", workload, *wl_args])
    return f"#!/bin/bash\ncd {GUEST_DIR}\n{cmd}\n"


def build_board(args, memory):
    """X86 board with a boot-CPU/ROI-CPU switchable core and the run script attached."""
    kvm = args.boot_cpu == "kvm"
    requires(isa_required=ISA.X86, kvm_required=kvm)

    processor = SimpleSwitchableProcessor(
        starting_core_type=CPU[args.boot_cpu],
        switch_core_type=CPU[args.roi_cpu],
        isa=ISA.X86,
        num_cores=1,
    )
    if kvm and args.kvm_no_perf:
        for core in processor.start:
            core.core.usePerf = False

    board = X86Board(
        clk_freq=args.clk,
        processor=processor,
        memory=memory,
        cache_hierarchy=PrivateL1PrivateL2CacheHierarchy(
            l1d_size=args.l1d, l1i_size=args.l1i, l2_size=args.l2
        ),
    )

    res = _resources()
    disk = Path(args.disk)
    if not disk.exists():
        sys.exit(f"{disk} missing: run scripts/setup.sh disk")
    # gem5_init.sh in the image skips systemd with no_systemd: less boot work and fewer
    # background services competing with the workload for the 512 MiB fast tier.
    kernel_args = board.get_default_kernel_args() + ["no_systemd"]
    if args.interactive:
        kernel_args.append("interactive")
    board.set_kernel_disk_workload(
        kernel=KernelResource(local_path=res["kernel"]),
        disk_image=DiskImageResource(local_path=str(disk), root_partition=res["root_partition"]),
        readfile_contents=run_script(args.workload, args.wl_args),
        kernel_args=kernel_args,
    )
    return board


def _install_roi_handlers():
    """Override gem5's hypercall 4/5 handlers to switch CPUs around the ROI."""

    class RoiBegin(WorkBeginExitHandler):
        @overrides(WorkBeginExitHandler)
        def _process(self, simulator):
            simulator.switch_processor()
            m5.stats.reset()
            print(f"ROI begin at tick {m5.curTick()}: switched to the ROI CPU, stats reset")

    class RoiEnd(WorkEndExitHandler):
        @overrides(WorkEndExitHandler)
        def _process(self, simulator):
            m5.stats.dump()
            simulator.switch_processor()
            print(f"ROI end at tick {m5.curTick()}: stats dumped, back on the boot CPU")


def run(board, args):
    """Simulate to the end of the guest script, then print a one-line report from the outputs."""
    _install_roi_handlers()
    sim = Simulator(board=board)
    sim.run()
    print(f"EXIT cause={sim.get_last_exit_event_cause()} tick={sim.get_current_tick()}")
    report(Path(m5.options.outdir), args.workload)


def report(outdir, workload):
    """Collect what the run produced; writes roi_stats.json and fs_result.json next to stats.txt."""
    console = outdir / CONSOLE_FILE
    text = console.read_text(errors="replace") if console.exists() else ""
    row = {"workload": workload}
    res = parse_result(text)
    if res.get("checksum"):
        row["checksum"] = res["checksum"]
    sp = outdir / "stats.txt"
    if sp.exists():
        st = roi_block(sp)
        row.update(summarize(st))
        (outdir / "roi_stats.json").write_text(json.dumps(st, indent=1, sort_keys=True))
        if res and row.get("sim_sec"):
            row.update(throughput_cols(workload, res.get("work"), row["sim_sec"]))
            row["roi_sec_guest"] = res.get("roi_sec", "")
    row["guest"] = parse_tagged(text, "GUESTINFO")
    row.update(parse_tagged(text, "LINUXSTATS"))
    (outdir / "fs_result.json").write_text(json.dumps(row, indent=1, sort_keys=True))
    if not res:
        print(f"WARNING: no RESULT line in {console}; check the guest console output")
    print("FS_RESULT " + json.dumps(row, sort_keys=True))
