"""The shell script a full-system guest runs after boot, handed to it through gem5's readfile.

Kept free of gem5 imports so pytest can check it; configs/fsboard.py passes it to the board.

The workload's output goes to the console and to a file that `gem5-bridge writefile` copies into the
host outdir as guest_output.txt. The report reads that file, because on the serial console guest
kernel messages land in the middle of program output lines.

The run script executes as the image's unprivileged `gem5` user. Swap setup (config D) uses
`sudo -n` on exactly mkswap/swapon, which scripts/make_disk.sh allows in /etc/sudoers.d.
"""

import shlex

from .fsreport import GUEST_OUTPUT

GUEST_DIR = "/opt/tiered-memory"  # where make_disk.sh puts bin/ and tiered_memory/ in the image
SWAP_FAILED = "SWAP SETUP FAILED"


def swap_setup(device):
    """Shell lines that format and enable `device` as swap, or stop the run if that fails.

    Without the stop, a failed swapon would leave config D silently identical to config A.
    """
    dev = shlex.quote(device)
    return [
        f"if sudo -n /usr/sbin/mkswap {dev} >/dev/null && sudo -n /usr/sbin/swapon {dev}; then",
        f"    echo SWAP_ON {dev}",
        "else",
        f'    echo "{SWAP_FAILED} ({dev})"',
        "    exit 1",
        "fi",
    ]


def run_script(workload, wl_args, swap_device=None):
    """The guest's run script: optional swap setup, then the workload under guest_run."""
    cmd = shlex.join(["python3", "-m", "tiered_memory.guest_run", workload, *wl_args])
    body = swap_setup(swap_device) if swap_device else []
    body.append(cmd)
    inner = "\n".join(f"    {line}" for line in body)
    return (
        "#!/bin/bash\n"
        f"cd {GUEST_DIR}\n"
        # The braces run as one subshell, so an `exit 1` in swap setup skips the workload but
        # still reaches the writefile below, and the failure message lands in guest_output.txt.
        "{\n"
        f"{inner}\n"
        f"}} 2>&1 | tee /tmp/{GUEST_OUTPUT}\n"
        f"gem5-bridge writefile /tmp/{GUEST_OUTPUT} {GUEST_OUTPUT}\n"
    )
