"""Download the X86 Ubuntu 24.04 kernel and disk image from gem5-resources and record their paths.

Run through gem5 (it uses gem5's resource downloader), normally via `scripts/setup.sh resources`:

  third_party/gem5/build/X86/gem5.opt configs/fetch_resources.py

Writes third_party/resources.json with the local kernel path, base disk path and root partition.
The resource ID and version below are the reproducibility pin for the guest OS, like GEM5_REF is
for gem5: change them deliberately.
"""

import json
from pathlib import Path

from gem5.resources.resource import obtain_resource

# Boots the stock Ubuntu 24.04 generic kernel (6.8) on X86; gem5's own X86 examples use this.
WORKLOAD_ID = "x86-ubuntu-24.04-boot-with-systemd"
WORKLOAD_VERSION = "5.0.0"

OUT = Path(__file__).resolve().parents[1] / "third_party/resources.json"

wl = obtain_resource(WORKLOAD_ID, resource_version=WORKLOAD_VERSION)
params = wl.get_parameters()
kernel, disk = params["kernel"], params["disk_image"]
info = {
    "workload": f"{WORKLOAD_ID}@{WORKLOAD_VERSION}",
    "kernel_id": f"{kernel.get_id()}@{kernel.get_resource_version()}",
    "kernel": kernel.get_local_path(),  # downloads if needed
    "disk_id": f"{disk.get_id()}@{disk.get_resource_version()}",
    "base_disk": disk.get_local_path(),
    # Metadata only; it is wrong for x86-ubuntu-24.04-img 4.0.0 (says 1, root is 2).
    # scripts/make_disk.sh detects the real one and records it as disk_root_partition.
    "root_partition": disk.get_root_partition(),
}
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(info, indent=1) + "\n")
print(json.dumps(info, indent=1))
print(f"wrote {OUT}")
