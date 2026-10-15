# J40 Mini R39.2.1 build, image and flash workflow

This packages the host-side workflow used for an Orin NX 16GB (P3767-0000)
on Seeed J40 Mini. All state lives below this directory in ignored `work/`,
`downloads/`, `sources/` and `analysis/`. No credentials or device measurements
are supplied. Rootfs assembly, image generation and physical flashing are
separate commands. Full JetPack SDK preinstallation is **disabled by default**.

The original workflow produced a package that was flashed and checked on
2026-10-15. The helpers here have been reorganized, given an independent
workspace and covered by host-only guard tests. This reorganization has not
triggered another flash. The sanitized [smoke-test summary](validation/nx16g-r39.2.1-smoke-test.json)
records that single device's boot/configuration checks, not full peripheral
acceptance or validation of the other inherited Seeed board profiles.

## Host preparation and inputs

Use an x86-64 Ubuntu host, Python 3.11+, Bash, Git and NVIDIA's AArch64 reference
toolchain. Native BSP prerequisites include `build-essential`, `flex`, `bison`,
`libssl-dev`, `device-tree-compiler`, `kmod`, `lbzip2`, `zstd`, `qemu-user-static`,
`cpio`, `abootimg`, `libxml2-utils`, `dosfstools`, `parted`, `udev`, `usbutils`,
`sshpass`, `nfs-kernel-server` and `rsync`. Reserve at least 150 GB for the BSP,
rootfs and raw/sparse intermediates. See NVIDIA's matching
[kernel build documentation](https://docs.nvidia.com/jetson/archives/r39.2.1/DeveloperGuide/SD/Kernel/KernelCustomization.html)
for toolchain requirements.

Download NVIDIA's matching **R39.2.1** archives yourself:

- `Jetson_Linux_R39.2.1_aarch64.tbz2`
- `public_sources.tbz2` from the R39.2.1 source release
- `Tegra_Linux_Sample-Root-Filesystem_R39.2.1_aarch64.tbz2`

From the fork checkout, use absolute paths to those downloads:

```bash
FLOW="$PWD/tools/j40mini/workflow"
bash "$FLOW/scripts/prepare_workspace.sh" \
  /absolute/path/Jetson_Linux_R39.2.1_aarch64.tbz2 \
  /absolute/path/public_sources.tbz2 \
  /absolute/path/Tegra_Linux_Sample-Root-Filesystem_R39.2.1_aarch64.tbz2
```

Preparation refuses an existing BSP. It extracts NVIDIA's BSP and five source
archives, checks the source archive SHA1 sidecars, applies **committed** files
from this fork with `git archive HEAD`, and creates the Ethernet source symlink.
Rootfs extraction remains a later root operation. Its saved rootfs SHA256 is a
local integrity record, not an independently authenticated NVIDIA checksum.
`work/seeed-j40mini-r39.2.1/source-revision.txt` records the overlay revision.
Commit local source changes before preparing a new workspace.

## Compile and stage

Set the reference toolchain prefix, then compile and stage without sudo:

```bash
export CROSS_COMPILE=/absolute/path/bin/aarch64-buildroot-linux-gnu-
bash "$FLOW/scripts/build_seeed_j40mini.sh"
bash "$FLOW/scripts/stage_seeed_j40mini.sh"
python3 tools/j40mini/verify_build.py \
  --bsp "$FLOW/work/seeed-j40mini-r39.2.1/Linux_for_Tegra" \
  --staged-rootfs "$FLOW/work/seeed-j40mini-r39.2.1/artifacts/rootfs"
```

Staging writes an owned-by-root kernel/module archive without requiring root.
It contains kernel files, not a complete root filesystem. Compilation uses
NVIDIA's native `nvbuild.sh`; no package, power mode or target device is changed.

## Local rootfs and headless profile

Review the example configs before assembly. They use user/hostname `unitree`,
SSH password authentication, Asia/Shanghai, NTP, and Ethernet `enP8p1s0` at
`192.168.123.18/24`, without a gateway. The validators intentionally check this
robot profile; update the validators too if changing it.

```bash
python3 "$FLOW/scripts/provision_seeed_j40mini_r3921.py" --configure-profile
sudo bash "$FLOW/scripts/assemble_seeed_j40mini_r3921.sh"
```

The first command prompts for a password and writes only a SHA512-crypt hash in
a private, ignored profile. There is no default or embedded password. Assembly
preserves ownership, applies NVIDIA's BSP packages, replaces stock modules with
the compiled modules, updates initrd, configures the account and network,
removes shared SSH host keys, and enables key generation on first boot.
Temporary QEMU, package caches and trigger backups are cleaned. Checkpoint
files allow a failed assembly to resume. Assembly uses a private mount namespace
and does not contact the target.

The BSP APT policy pins R39.2.1 and holds installed L4T packages. If deliberately
preinstalling the full SDK, opt in with
`sudo env J40MINI_PREINSTALL_JETPACK=1 bash "$FLOW/scripts/assemble_seeed_j40mini_r3921.sh"`.
This path checks the dependency plan, permits only known OpenCV replacements,
and rejects boot-chain package changes. SDK installation is not needed for a
bootable image. This port does not provide official J40 Mini OTA capsules.

## Recovery measurements and no-flash images

Connect only the intended J40 Mini and put it in Force Recovery. This command
reads EEPROM/fuses, boots a temporary RAM initrd, marks NVMe read-only, measures
its capacity, then generates and validates a local `--no-flash` package:

```bash
sudo bash "$FLOW/scripts/prepare_seeed_j40mini_images.sh"
```

No target storage is written by this entry point. Expect one APX `0955:7323`,
P3767-0000/D3, an unsigned NS module, and one NVMe namespace `nvme0n1` with
512-byte logical sectors. Capacity is measured, not hard-coded to the reference
SSD. APP is 64 GiB. The measured XML preserves the trailing newline required by
NVIDIA's shell filters. EEPROM/CID data and logs remain in ignored private state.

`--resume` reuses a completed measurement to regenerate images offline;
`--verify-only` verifies the existing package without booting or flashing.
Do not reuse another module's private measurement. The package contains
QSPI and NVMe indexes plus `external/system.img`, which is NVIDIA's zstd tar
payload, not a whole-disk image for `dd`. Validation checks partition bounds,
hashes, the normal-boot kernel/ramdisk and the delivered rootfs contents.

## Physical flash: explicit separate step

The following command **overwrites QSPI and the target NVMe system partitions**.
Put the same measured module back in Force Recovery before running it:

```bash
sudo bash "$FLOW/scripts/flash_seeed_j40mini.sh"
```

This entry point requires a successfully generated package, verifies its entire
manifest and stored arguments, and compares live BootROM ECID with the original
private measurement before calling native `--flash-only`. It never regenerates
images and refuses `--direct`, `--external-only` or erased-package overrides.
Keep power and USB connected until native flashing reports success.

## Read-only verification after boot

Wait for normal USB networking, then run:

```bash
python3 "$FLOW/scripts/verify_seeed_j40mini_r3921_installed.py"
```

It prompts for the Jetson account password; `--host` defaults to `192.168.55.1`.
SSH host keys are pinned in that flash operation's private directory. The target
commands read state only; reports are saved locally. Checks cover actual QSPI
and L4T R39.2.1, compiled kernel/DTB/all modules, NVMe root, clean dpkg state,
headless SSH, the Super config and default **40W / ID 4**, and persistent active
`192.168.123.18/24` without a wired default route. `MAXN_SUPER / ID 0` is available
but this workflow does not switch to it. Robot-peer communication and complete
peripheral acceptance require separate testing.

## Host-only regression checks

```bash
python3 -m unittest discover -s "$FLOW/tests" -v
```

These tests exercise archive preparation, damaged payload rejection, CID mismatch,
unsafe flash arguments, path traversal, OpenCV/boot-chain dependency rules and
normal boot image hashes. They do not use sudo, USB or a target connection.
