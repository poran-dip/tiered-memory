"""Which machine and which code produced a result, recorded next to every result.

Standard library only, so it also works inside gem5's Python and in the full-system guest.
"""

import platform
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GEM5_DIR = REPO / "third_party/gem5"


def _read(path):
    try:
        return Path(path).read_text()
    except OSError:
        return ""


def cpu_model(cpuinfo="/proc/cpuinfo"):
    m = re.search(r"^model name\s*:\s*(.+)$", _read(cpuinfo), re.MULTILINE)
    return m.group(1).strip() if m else platform.processor()


def mem_total_mib(meminfo="/proc/meminfo"):
    m = re.search(r"MemTotal:\s+(\d+) kB", _read(meminfo))
    return int(m.group(1)) // 1024 if m else ""


def is_wsl(release=None):
    release = release if release is not None else platform.release()
    return "microsoft" in release.lower() or "wsl" in release.lower()


def host_columns():
    """host_* columns: CPU model, kernel release, WSL or not, RAM."""
    release = platform.release()
    return {
        "host_cpu": cpu_model(),
        "host_kernel": release,
        "host_wsl": is_wsl(release),
        "host_mem_mib": mem_total_mib(),
    }


def git_commit(path):
    """HEAD of the git checkout at path, with '-dirty' if it has uncommitted changes; '' if none."""
    try:
        head = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(path), "status", "--porcelain", "--untracked-files=no"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""
    return head + ("-dirty" if dirty else "")
