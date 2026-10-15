#!/usr/bin/env bash
# Install the compiled kernel/modules into an unprivileged artifact directory.
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/seeed_j40mini_env.sh"
OUT="${SOURCE}/kernel_out"
KERNEL="${OUT}/kernel/kernel-noble"
export KERNEL_HEADERS="${KERNEL}"
export INSTALL_MOD_PATH="${WORK}/artifacts/rootfs"
export INSTALL_MOD_STRIP=1
DTBS="${OUT}/build/nvidia-public/devicetree/generic-dtbs"
TARGET_DTB="tegra234-j40mini-p3768-0000+p3767-0000-recomputer.dtb"
[[ -f "${WORK}/.compiled" && -s "${KERNEL}/arch/arm64/boot/Image" && -s "${DTBS}/${TARGET_DTB}" ]] || {
    echo 'Complete the kernel, modules and DTB build first.' >&2; exit 1;
}
mkdir -p "${INSTALL_MOD_PATH}/boot/dtb" "${WORK}/logs"
exec > >(tee "${WORK}/logs/stage.log") 2>&1
make -C "${OUT}/kernel" install
make -C "${OUT}" kernel_name=noble system_type=l4t INSTALL_MOD_DIR=updates modules_install
cp "${DTBS}"/*.dtb "${INSTALL_MOD_PATH}/boot/dtb/"
cp "${DTBS}"/*.dtbo "${INSTALL_MOD_PATH}/boot/"
RELEASE="$(cat "${KERNEL}/include/config/kernel.release")"
depmod -b "${INSTALL_MOD_PATH}" "${RELEASE}"
printf '%s\n' "${RELEASE}" > "${WORK}/artifacts/kernel-release.txt"
# modules_install makes build links into the host build tree; omit those from delivery.
rm -f "${INSTALL_MOD_PATH}/lib/modules/${RELEASE}/build" "${INSTALL_MOD_PATH}/lib/modules/${RELEASE}/source"
tar --owner=0 --group=0 --numeric-owner -czf "${WORK}/artifacts/kernel-rootfs.tar.gz" -C "${INSTALL_MOD_PATH}" boot lib
(cd "${WORK}/artifacts" && sha256sum kernel-rootfs.tar.gz > SHA256SUMS)
(cd "${SOURCE}" && bash -e ./do_copy.sh)
echo "Artifacts staged for ${RELEASE}. The directory contains kernel files, not a complete root filesystem."
