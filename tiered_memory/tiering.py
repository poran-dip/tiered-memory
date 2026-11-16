"""The guest kernel's memory-tiering switches, set before a workload starts (stdlib only: it runs
inside the guest, where guest_run.py imports it).

Three modes, so that "tiering off" is never an accident of whatever the kernel image defaults to:

  default  leave Ubuntu's settings alone (NUMA balancing normally on, demotion off)
  on       demotion enabled and NUMA balancing in memory-tiering mode, which makes it promote hot
           pages from a lower tier to a higher one (kernel.numa_balancing=2)
  off      NUMA balancing and demotion both off

Whether node 1 is a *lower tier* than node 0 is the kernel's call, from the HMAT table; with the
two nodes in one tier (kernels before the CPU-less HMAT fix, or no HMAT) these switches have nothing
to move pages between. guest_run.py records the tiers it sees (guest_memory_tiers).
"""

import subprocess
from pathlib import Path

MODES = ("default", "on", "off")
NUMA_BALANCING = Path("/proc/sys/kernel/numa_balancing")
DEMOTION = Path("/sys/kernel/mm/numa/demotion_enabled")
# mode -> (kernel.numa_balancing, demotion_enabled)
SETTINGS = {"on": ("2", "1"), "off": ("0", "0")}
FAILED = "TIERING SETUP FAILED"


def writes(mode):
    """[(path, value)] a mode needs written; [] for default."""
    if mode not in MODES:
        raise ValueError(f"tiering mode must be one of {', '.join(MODES)}, not {mode!r}")
    if mode == "default":
        return []
    balancing, demotion = SETTINGS[mode]
    return [(NUMA_BALANCING, balancing), (DEMOTION, demotion)]


def sudo_write(path, value):
    """Write value to path as root. The image's sudoers rule allows exactly tee on these paths."""
    subprocess.run(
        ["sudo", "-n", "/usr/bin/tee", str(path)],
        input=value + "\n",
        text=True,
        check=True,
        stdout=subprocess.DEVNULL,
    )


def read(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def apply(mode, write=sudo_write, reader=read):
    """Apply a mode and read every setting back; returns [] when all took, else error strings.

    A setting the kernel silently ignores would make "on" identical to "default", so each one is
    checked, like config D checks its swap.
    """
    errors = []
    for path, value in writes(mode):
        try:
            write(path, value)
        except (OSError, subprocess.CalledProcessError) as e:
            errors.append(f"{path}: {e}")
            continue
        got = reader(path)
        if got != value:
            errors.append(f"{path}: wrote {value}, reads {got}")
    return errors
