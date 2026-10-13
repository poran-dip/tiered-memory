"""Shared gem5 full-system setup for configs A-D (X86, stock Ubuntu 24.04 kernel and disk).

Imported by configs/<letter>_*.py, which run inside gem5:  gem5.opt configs/a_baseline.py ...

What a run does:
  1. Boot Linux on the KVM CPU (near-native speed, no timing) or, without KVM, the atomic CPU.
  2. The guest runs the script this config hands it through gem5's readfile:
     python3 -m tiered_memory.guest_run <workload> <args>  (from the disk image, see make_disk.sh)
  3. At ROI begin the workload issues hypercall 4: switch to the ROI CPU (timing or O3), reset stats.
  4. At ROI end it issues hypercall 5: dump stats (so block 1 of stats.txt is the ROI, exactly as in
     SE mode), switch back to the boot CPU for the teardown.
  5. The script copies its output to the host (gem5-bridge writefile -> <outdir>/guest_output.txt);
     then the image issues hypercall 3 and the simulation exits.
  6. tiered_memory.fsreport builds the report from the outdir (rerunnable without gem5).

Kernel and base disk come from gem5-resources (scripts/setup.sh resources writes their local paths to
third_party/resources.json); the disk actually booted is the copy with our binaries added
(scripts/setup.sh disk).
"""

import json
import sys
import time
from pathlib import Path

import m5
from gem5.components.boards.x86_board import X86Board
from gem5.components.cachehierarchies.classic.private_l1_private_l2_walk_cache_hierarchy import (
    PrivateL1PrivateL2WalkCacheHierarchy,
)
from gem5.components.processors.cpu_types import CPUTypes
from gem5.components.processors.simple_switchable_processor import SimpleSwitchableProcessor
from gem5.isas import ISA
from gem5.resources.resource import DiskImageResource, KernelResource
from gem5.simulate.exit_handler import (
    AfterBootExitHandler,
    KernelBootedExitHandler,
    ScheduledExitEventHandler,
    WorkBeginExitHandler,
    WorkEndExitHandler,
)
from gem5.simulate.simulator import Simulator
from gem5.utils.override import overrides
from gem5.utils.requires import requires
from m5.objects import IdeDisk, RawDiskImage

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tiered_memory.fsreport import CONSOLE_FILE, ROI_MARKER, collect  # noqa: E402
from tiered_memory.guest_script import run_script  # noqa: E402
from tiered_memory.progress import Progress  # noqa: E402
from tiered_memory.workloads import NAMES  # noqa: E402

RESOURCES_JSON = REPO / "third_party/resources.json"
DEFAULT_DISK = REPO / "third_party/disks/x86-ubuntu-24.04-tm.img"

CPU = {"kvm": CPUTypes.KVM, "atomic": CPUTypes.ATOMIC, "timing": CPUTypes.TIMING, "o3": CPUTypes.O3}

# This Ubuntu kernel drives gem5's IDE disk through libata, so it is sda, not X86Board's
# default /dev/hda (with hda the guest panics: "Cannot open root device /dev/hda2").
DISK_DEVICE = "/dev/sda"
# A second IDE disk (drive device1, primary slave) is the next libata disk.
SWAP_DEVICE = "/dev/sdb"

TICKS_PER_SEC = 10**12


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
    p.add_argument(
        "--max-sim-sec",
        type=float,
        default=600,
        help="give up after this much simulated time with no gem5 exit event (boot, ROI begin/end), "
        "or once the ROI itself has run this long. A guest kernel panic under KVM cannot stop gem5 "
        "by itself, so this is what ends a failed run",
    )
    p.add_argument(
        "--progress-sec",
        type=float,
        default=15,
        help="during the ROI, print a progress line about every this many wall-clock seconds "
        "(0 = off). Progress exits do not touch the stats",
    )
    p.add_argument("wl_args", nargs="*", help="workload arguments, after '--'")


def _resources():
    if not RESOURCES_JSON.exists():
        sys.exit(f"{RESOURCES_JSON} missing: run scripts/setup.sh resources disk")
    return json.loads(RESOURCES_JSON.read_text())


def build_board(args, memory, swap_device=None):
    """X86 board with a boot-CPU/ROI-CPU switchable core and the run script attached.

    swap_device: guest block device the run script turns into swap before the workload (config D).
    """
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
        # The walk-cache variant puts each page-table walker behind an 8 KiB cache. Without it the
        # walker sits directly on the L2 bus as an uncached requestor, and gem5's classic caches
        # panic when its page-table-entry write hits a line the L1D holds Modified (seen as soon as
        # the guest rewrites page tables during the ROI, e.g. while swapping).
        cache_hierarchy=PrivateL1PrivateL2WalkCacheHierarchy(
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
        # make_disk.sh records the partition it actually found the root filesystem on; the
        # gem5-resources metadata value can be wrong (it says 1 for x86-ubuntu-24.04-img 4.0.0).
        disk_image=DiskImageResource(
            local_path=str(disk),
            root_partition=res.get("disk_root_partition", res["root_partition"]),
        ),
        readfile_contents=run_script(args.workload, args.wl_args, swap_device=swap_device),
        kernel_args=kernel_args,
        disk_device=DISK_DEVICE,
    )
    return board


def add_swap_disk(board, image_path, delay):
    """Attach a second IDE disk (the guest sees /dev/sdb) backed by image_path, for swap.

    gem5's IDE disk adds a fixed `delay` per request on top of the modelled DMA transfer; the root
    disk keeps gem5's default (1us) so booting is not slowed. Writes go straight to image_path, a
    sparse file, so swapping does not grow gem5's memory.
    Call after build_board(): X86Board sets up the root disk in set_kernel_disk_workload.
    """
    swap = IdeDisk(driveID="device1", delay=delay)
    swap.image = RawDiskImage(image_file=str(image_path), read_only=False)
    ide = board.pc.south_bridge.ide
    disks = list(ide.disks)
    # X86Board made its [root disk] list a child of the controller. gem5 will not re-parent a list
    # that already has a parent (it only warns, and the new disk would stay orphaned), so detach
    # the old list first and assign the two-disk list fresh.
    ide.clear_child("disks")
    ide.disks = disks + [swap]
    return SWAP_DEVICE


PROGRESS_EXIT = "tiered-memory progress"  # exit string of the scheduled progress exits

# Set by run(): the ROI progress tracker, the board (for instruction counts) and an optional
# callable returning extra text for each progress line (e.g. config D's swap usage).
_progress = None
_board = None
_progress_extra = None


def _insts():
    """Instructions committed so far by the cores currently switched in."""
    return sum(core.get_simobject().totalInsts() for core in _board.get_processor().get_cores())


def _schedule_progress():
    m5.scheduleTickExitAbsolute(m5.curTick() + _progress.interval, PROGRESS_EXIT)


def _sim_sec():
    return m5.curTick() / TICKS_PER_SEC


def _install_handlers():
    """Override gem5's hypercall handlers: print boot progress, switch CPUs around the ROI."""

    class Booted(KernelBootedExitHandler):
        @overrides(KernelBootedExitHandler)
        def _process(self, simulator):
            print(f"[{_sim_sec():.2f}s sim] kernel booted")

    class AfterBoot(AfterBootExitHandler):
        @overrides(AfterBootExitHandler)
        def _process(self, simulator):
            print(f"[{_sim_sec():.2f}s sim] guest logged in, starting the run script")

    class RoiBegin(WorkBeginExitHandler):
        @overrides(WorkBeginExitHandler)
        def _process(self, simulator):
            simulator.switch_processor()
            m5.stats.reset()
            print(f"[{_sim_sec():.2f}s sim] ROI begin: switched to the ROI CPU, stats reset")
            if _progress is not None:
                _progress.start(m5.curTick(), _insts(), time.monotonic())
                _schedule_progress()

    class RoiEnd(WorkEndExitHandler):
        @overrides(WorkEndExitHandler)
        def _process(self, simulator):
            if _progress is not None:
                _progress.stop()
            m5.stats.dump()
            (Path(m5.options.outdir) / ROI_MARKER).touch()
            simulator.switch_processor()
            print(f"[{_sim_sec():.2f}s sim] ROI end: stats dumped, back on the boot CPU")

    class ProgressTick(ScheduledExitEventHandler):
        """Prints a progress line and schedules the next one; never touches the stats."""

        _stop = False

        @overrides(ScheduledExitEventHandler)
        def _process(self, simulator):
            if _progress is None or not _progress.active:
                return  # a leftover exit from a finished ROI: ignore it, schedule nothing
            extra = _progress_extra() if _progress_extra else ""
            print(_progress.sample(m5.curTick(), _insts(), time.monotonic(), extra), flush=True)
            if _progress.over_budget(m5.curTick()):
                print("Giving up: the ROI has run longer than --max-sim-sec of simulated time")
                self._stop = True
                return
            _schedule_progress()

        @overrides(ScheduledExitEventHandler)
        def _exit_simulation(self):
            return self._stop


def run(board, args, progress_extra=None):
    """Simulate to the end of the guest script, then print a one-line report from the outputs.

    progress_extra: optional callable returning extra text for each ROI progress line.
    """
    global _progress, _board, _progress_extra
    _board, _progress_extra = board, progress_extra
    if args.progress_sec > 0:
        _progress = Progress(wall_gap=args.progress_sec, max_sim_sec=args.max_sim_sec)
    _install_handlers()
    sim = Simulator(board=board)
    # Window per stretch between exit events, not a total budget: gem5 restarts it at each exit.
    sim.set_max_ticks(int(args.max_sim_sec * TICKS_PER_SEC))
    outdir = Path(m5.options.outdir)
    print(f"Booting; guest console: {outdir / CONSOLE_FILE}")
    sim.run()
    cause = sim.get_last_exit_event_cause()
    print(f"EXIT cause={cause} tick={sim.get_current_tick()} ({_sim_sec():.2f}s sim)")
    if "max tick" in str(cause).lower() or "simulate() limit" in str(cause).lower():
        print(
            f"Gave up after {args.max_sim_sec:g}s of simulated time with no progress. "
            f"The guest probably panicked or hung: tail {outdir / CONSOLE_FILE}"
        )
    row = collect(outdir, args.workload)
    if "checksum" not in row:
        print(f"WARNING: no RESULT line from the guest; check {outdir / CONSOLE_FILE}")
    print("FS_RESULT " + json.dumps(row, sort_keys=True))
