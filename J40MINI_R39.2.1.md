# J40 Mini / Orin NX 16GB on L4T R39.2.1

This is an unofficial port of Seeed's `r39.2.0` branch, commit
`df17ed28201645fcaeb6c8e6fb82a0b4592faefb`, onto NVIDIA L4T R39.2.1.
The maintained branch is `r39.2.1-j40mini`. It is not a Seeed release.

## Port method

The port compares the original NVIDIA R39.2.0 BSP and source archives against
the pinned Seeed tree, then applies only the Seeed changes to pristine NVIDIA
R39.2.1. Files unchanged by Seeed use NVIDIA's R39.2.1 version, including the
boot firmware, UEFI and GPU driver sources. Old R39.2.0 Debian package files
are removed from this overlay; the R39.2.1 BSP supplies their replacements.

There are 602 Seeed-only files, 171 NVIDIA-updated tracked paths and one file
changed by both sides. The latter is the Thor overlay Makefile; its independent
overlay entries from both sides are retained. Forty new upstream archive paths were evaluated; 34 additional source files
are retained in Git. Generated Python/perf binaries and the build-time Ethernet
symlink remain untracked. The full kernel and DTB build covers the shared sources,
but board-specific validation in this branch targets J40 Mini with P3767-0000.

The J40 Mini configuration preserves Seeed's pinmux, pad voltage, USB/PCIe
topology, drivers and Super BPMP selection. The module's actual FAB, revision,
RAM code and SSD capacity must be supplied from hardware measurements.

## Build from matching NVIDIA downloads

Prepare NVIDIA's **R39.2.1** BSP, sample rootfs and public sources. Extract
`kernel_src.tbz2`, `kernel_oot_modules_src.tbz2`, `kernel_nvgpu_src.tbz2`,
`nvidia_kernel_display_driver_source.tbz2` and
`nvidia_unified_gpu_display_driver_source.tbz2` into `Linux_for_Tegra/source`.
Check their supplied SHA1 sidecars before applying the overlay.

Copy this repository over that BSP, excluding `.git`. Install the reference
AArch64 toolchain and the build dependencies listed in NVIDIA's kernel build
documentation. From `Linux_for_Tegra`:

```bash
sudo ./apply_binaries.sh
cd source
ln -sfn ../../../../../../nvethernetrm nvidia-oot/drivers/net/ethernet/nvidia/nvethernet/nvethernetrm
export ARCH=arm64
export CROSS_COMPILE=/absolute/path/to/aarch64-buildroot-linux-gnu-
./nvbuild.sh
./do_copy.sh
export INSTALL_MOD_PATH="$(realpath ../rootfs)"
sudo -E ./nvbuild.sh -i
cd ..
sudo ./tools/l4t_update_initrd.sh
```

The board name is `recomputer-orin-j40mini`. For NX 16GB its kernel and UEFI
DTB is `tegra234-j40mini-p3768-0000+p3767-0000-recomputer.dtb`, and the BPMP
DTB is `tegra234-bpmp-3767-0000-3768-super.dtb`.

## JetPack package policy

`extra_scripts/99-seeed-freeze-jp.pref` now selects the exact R39.2.1 BSP build
`39.2.1-20260806224157` and JetPack `7.2.1-b49`, including the dependency
versions resolved from NVIDIA's repository metadata. Install it into the
image's `/etc/apt/preferences.d` before installing JetPack. The local image
builder additionally holds the installed `nvidia-l4t-*` packages and checks
that installing JetPack does not replace boot-chain packages.

This branch does not add J40 Mini to NVIDIA's official OTA payloads. Updating
or reinstalling the stock bootloader/kernel packages remains a separate
custom-BSP integration task. Preserve the actual `recomputer-orin-j40mini`
board identity; official developer-kit capsules are not J40 Mini payloads.

## Validation and scope

The port compiles the kernel, NVIDIA OOT/GPU/display drivers and DTBs. All 1555
staged modules are AArch64 ELF with the matching `6.8.12-1021-tegra` vermagic.
The target DTB, Super board callback, five default DT overlays and all 23
initrd module-list entries pass offline checks.

Rootfs assembly and QSPI/NVMe package validation are recorded separately in
the image workspace. A generated `--no-flash` package is not proof of a
successful hardware boot. This port has not been flashed or tested on hardware.
No OTA or hardware acceptance is claimed.

For offline image generation use `tools/kernel_flash/l4t_initrd_flash.sh`
with **`--no-flash`**, a measured-capacity NVMe XML and the measured board/chip
environment. Do not use `--flash-only`, `--direct` or a guessed board identity
when preparing an offline package.
