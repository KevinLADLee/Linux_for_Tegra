#!/usr/bin/env bash
# Prepare an independent local BSP from matching archives and this Git revision.
# No sudo, device access, rootfs extraction or flashing occurs here.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/seeed_j40mini_env.sh"
REPOSITORY="$(cd -- "${SCRIPT_DIR}/../../../.." && pwd)"
if [[ $# != 3 ]]; then
    echo "Usage: bash $0 Jetson_Linux_R39.2.1_aarch64.tbz2 public_sources.tbz2 Tegra_Linux_Sample-Root-Filesystem_R39.2.1_aarch64.tbz2"
    [[ ${1:-} == --help ]] && exit 0
    exit 2
fi
[[ ${EUID} != 0 ]] || { echo 'Prepare the source workspace as a normal user, not root.' >&2; exit 1; }
BSP_ARCHIVE="$(realpath -e -- "$1")"
SOURCE_ARCHIVE="$(realpath -e -- "$2")"
ROOTFS_ARCHIVE="$(realpath -e -- "$3")"
[[ ! -e "${WORK}/Linux_for_Tegra" ]] || { echo 'Existing BSP will not be overwritten.' >&2; exit 1; }
mkdir -p "${WORK}" "${PROJECT_DIR}/analysis" "${PROJECT_DIR}/downloads/jetpack-7.2.1"
TEMP="$(mktemp -d "${WORK}/prepare.XXXXXXXX")"
trap 'rm -rf -- "${TEMP}"' EXIT
tar --no-same-owner -xf "${BSP_ARCHIVE}" -C "${TEMP}"
BSP="${TEMP}/Linux_for_Tegra"
grep -q '^# R39 (release), REVISION: 2.1,' "${BSP}/nv_tegra/nv_tegra_release"
mkdir -p "${TEMP}/public" "${BSP}/source"
tar --no-same-owner -xf "${SOURCE_ARCHIVE}" -C "${TEMP}/public"
ARCHIVE_DIR="${TEMP}/public/Linux_for_Tegra/source"
for archive in kernel_src.tbz2 kernel_oot_modules_src.tbz2 kernel_nvgpu_src.tbz2 \
    nvidia_kernel_display_driver_source.tbz2 nvidia_unified_gpu_display_driver_source.tbz2; do
    expected="$(awk 'NR == 1 {print $1}' "${ARCHIVE_DIR}/${archive}.sha1sum")"
    [[ ${expected} =~ ^[[:xdigit:]]{40}$ ]]
    actual="$(sha1sum "${ARCHIVE_DIR}/${archive}" | awk '{print $1}')"
    [[ ${actual} == "${expected}" ]] || { echo "Source SHA1 mismatch: ${archive}" >&2; exit 1; }
    tar --no-same-owner -xf "${ARCHIVE_DIR}/${archive}" -C "${BSP}/source"
done
# Archive tracked files only: private workflow state never becomes the overlay.
git -C "${REPOSITORY}" archive HEAD | tar --no-same-owner -xf - -C "${BSP}"
ln -sfn ../../../../../../nvethernetrm \
    "${BSP}/source/nvidia-oot/drivers/net/ethernet/nvidia/nvethernet/nvethernetrm"
mv "${BSP}" "${WORK}/Linux_for_Tegra"
ROOTFS_NAME=Tegra_Linux_Sample-Root-Filesystem_R39.2.1_aarch64.tbz2
ln -sfn "${ROOTFS_ARCHIVE}" "${PROJECT_DIR}/downloads/jetpack-7.2.1/${ROOTFS_NAME}"
rootfs_hash="$(sha256sum "${ROOTFS_ARCHIVE}" | awk '{print $1}')"
printf '%s  %s\n' "${rootfs_hash}" "${ROOTFS_NAME}" > "${PROJECT_DIR}/downloads/jetpack-7.2.1/SHA256SUMS"
git -C "${REPOSITORY}" rev-parse HEAD > "${WORK}/source-revision.txt"
mkdir -p "${WORK}/logs" "${WORK}/operations"
echo "Prepared independent R39.2.1 BSP: ${WORK}/Linux_for_Tegra"
echo 'Rootfs SHA256 is a local integrity record, not an independently authenticated NVIDIA checksum.'
