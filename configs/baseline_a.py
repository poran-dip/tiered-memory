import os

from gem5.components.boards.arm_board import ArmBoard
from gem5.components.cachehierarchies.classic.private_l1_private_l2_cache_hierarchy import (
    PrivateL1PrivateL2CacheHierarchy,
)
from gem5.components.memory import SingleChannelDDR4_2400
from gem5.components.processors.cpu_types import CPUTypes
from gem5.components.processors.simple_processor import SimpleProcessor
from gem5.isas import ISA
from gem5.resources.resource import CustomDiskImageResource, CustomResource
from gem5.simulate.simulator import Simulator
from gem5.utils.requires import requires
from m5.objects import ArmDefaultRelease, VExpress_GEM5_V1

requires(isa_required=ISA.ARM)

cache = PrivateL1PrivateL2CacheHierarchy(l1d_size="32KiB", l1i_size="32KiB", l2_size="256KiB")

# Config A: one fast memory region (512MiB for testing; change to 8GiB later)
memory = SingleChannelDDR4_2400(size="512MiB")

processor = SimpleProcessor(cpu_type=CPUTypes.ATOMIC, isa=ISA.ARM, num_cores=1)

board = ArmBoard(
    clk_freq="3GHz",
    processor=processor,
    memory=memory,
    cache_hierarchy=cache,
    release=ArmDefaultRelease(),
    platform=VExpress_GEM5_V1(),
)

files = os.path.expanduser("~/gem5-files")

board.set_kernel_disk_workload(
    kernel=CustomResource(f"{files}/newbin/binaries/vmlinux.arm64"),
    bootloader=CustomResource(f"{files}/newbin/binaries/boot.arm64"),
    disk_image=CustomDiskImageResource(
        f"{files}/ubuntu-18.04-arm64-docker.img", root_partition="1"
    ),
)

Simulator(board=board).run()
