#!/usr/bin/env bash
# Assemble a local R39.2.1 BSP rootfs. Generate images in a separate step.
# SDK preinstallation is optional: J40MINI_PREINSTALL_JETPACK=1.
# This script has no recovery, --flash-only, --direct, or target SSH path.
set -euo pipefail
export SEEED_L4T_VERSION=39.2.1
export J40MINI_PREINSTALL_JETPACK="${J40MINI_PREINSTALL_JETPACK:-0}"
[[ ${J40MINI_PREINSTALL_JETPACK} == 0 || ${J40MINI_PREINSTALL_JETPACK} == 1 ]]
source "$(dirname -- "${BASH_SOURCE[0]}")/seeed_j40mini_env.sh"
BSP="${WORK}/Linux_for_Tegra"
ROOTFS="${BSP}/rootfs"
ROOTFS_TAR="${PROJECT_DIR}/downloads/jetpack-7.2.1/Tegra_Linux_Sample-Root-Filesystem_R39.2.1_aarch64.tbz2"
[[ $# == 0 ]] || { echo "Usage: sudo bash $0" >&2; exit 2; }
[[ ${EUID} == 0 ]] || { echo 'Local rootfs ownership, chroot and image loop mounts require sudo.' >&2; exit 1; }
if [[ ${J40MINI_BUILD_NAMESPACE:-0} != 1 ]]; then
    exec unshare --mount --propagation private -- env J40MINI_BUILD_NAMESPACE=1 bash "$0"
fi
[[ -f "${BSP}/recomputer-orin-j40mini.conf" && -f "${WORK}/.compiled" && -f "${WORK}/headless-profile.json" ]]
python3 "${PROJECT_DIR}/scripts/verify_seeed_j40mini.py"
[[ $(df -B1 --output=avail "${WORK}" | tail -1) -gt 150000000000 ]]
mkdir -p "${WORK}/logs" "${WORK}/operations"
exec > >(tee -a "${WORK}/logs/rootfs-images.log") 2>&1
TASK_UID="${SUDO_UID:-0}"
TASK_GID="${SUDO_GID:-0}"
SDK_MOUNTED=0
QEMU_PREPARED=0
cleanup_sdk() {
    if [[ ${SDK_MOUNTED} == 1 ]]; then
        for point in sys proc dev; do umount -R "${ROOTFS}/${point}" || true; done
        SDK_MOUNTED=0
    fi
    if [[ ${QEMU_PREPARED} == 1 ]]; then
        qemu_cleanup_rootfs "${ROOTFS}"
        QEMU_PREPARED=0
    fi
    if [[ -f "${WORK}/policy-rc.d.before-sdk" ]]; then
        cp -p "${WORK}/policy-rc.d.before-sdk" "${ROOTFS}/usr/sbin/policy-rc.d"
    elif [[ -f "${WORK}/.sdk-policy-created" ]]; then
        rm -f "${ROOTFS}/usr/sbin/policy-rc.d"
    fi
}
cleanup() {
    cleanup_sdk
    chown -R "${TASK_UID}:${TASK_GID}" "${WORK}/logs" "${WORK}/operations"
    chmod -R u+rwX,go+rX "${WORK}/logs"
}
trap cleanup EXIT
(cd "$(dirname -- "${ROOTFS_TAR}")" && sha256sum -c SHA256SUMS)
(cd "${WORK}/artifacts" && sha256sum -c SHA256SUMS)
if [[ ! -f "${BSP}/.seeed-rootfs-extracted" ]]; then
    [[ -z "$(find "${ROOTFS}" -mindepth 1 -maxdepth 1 ! -name README.txt -print -quit)" ]] || {
        echo 'Unmarked rootfs is not empty; refusing to overwrite.' >&2; exit 1;
    }
    tar --numeric-owner --xattrs -I lbzip2 -xpf "${ROOTFS_TAR}" -C "${ROOTFS}"
    touch "${BSP}/.seeed-rootfs-extracted"
fi
if [[ ! -f "${BSP}/.seeed-binaries-applied" ]]; then
    (cd "${BSP}" && bash ./apply_binaries.sh)
    touch "${BSP}/.seeed-binaries-applied"
fi
install -D -m 644 "${BSP}/extra_scripts/99-seeed-freeze-jp.pref" \
    "${ROOTFS}/etc/apt/preferences.d/00-j40mini-r39.2.1.pref"
cat > "${ROOTFS}/etc/apt/sources.list.d/nvidia-l4t-apt-source.list" <<'EOF'
deb https://repo.download.nvidia.com/jetson/common r39.2 main
deb https://repo.download.nvidia.com/jetson/som r39.2 main
deb https://repo.download.nvidia.com/jetson/ffmpeg r39.2 main
EOF
if [[ ${J40MINI_PREINSTALL_JETPACK} == 1 && ! -f "${BSP}/.seeed-jetpack-installed" ]]; then
    # NVIDIA's sourced helper reads this optional variable under our nounset mode.
    export HOST_QEMU_VERSION="${HOST_QEMU_VERSION:-}"
    source "${BSP}/nv_tegra/nv_qemu_setup_helper.sh"
    QEMU_PREPARED=1
    qemu_setup_rootfs "${ROOTFS}"
    if [[ -e "${ROOTFS}/usr/sbin/policy-rc.d" && ! -f "${WORK}/.sdk-policy-created" ]]; then
        cp -p "${ROOTFS}/usr/sbin/policy-rc.d" "${WORK}/policy-rc.d.before-sdk"
    fi
    printf '#!/bin/sh\nexit 101\n' > "${ROOTFS}/usr/sbin/policy-rc.d"
    chmod 755 "${ROOTFS}/usr/sbin/policy-rc.d"
    touch "${WORK}/.sdk-policy-created"
    # Prevent all NVIDIA firmware writers while configuring this offline rootfs.
    install -d "${ROOTFS}/opt/nvidia/l4t-packages"
    touch "${ROOTFS}/opt/nvidia/l4t-packages/.nv-l4t-disable-boot-fw-update-in-preinstall"
    [[ ! -L "${ROOTFS}/etc/resolv.conf" ]] || rm "${ROOTFS}/etc/resolv.conf"
    cp -L /etc/resolv.conf "${ROOTFS}/etc/resolv.conf"
    SDK_MOUNTED=1
    for point in dev proc sys; do mount --bind "/${point}" "${ROOTFS}/${point}"; done
    chroot "${ROOTFS}" env DEBIAN_FRONTEND=noninteractive apt-get -o APT::Update::Error-Mode=any update
    chroot "${ROOTFS}" apt-get -s --no-install-recommends install nvidia-jetpack=7.2.1-b49 \
        > "${WORK}/logs/jetpack-install-plan.txt"
    python3 "${PROJECT_DIR}/scripts/validate_seeed_jetpack_plan.py" "${WORK}/logs/jetpack-install-plan.txt"
    chroot "${ROOTFS}" env DEBIAN_FRONTEND=noninteractive apt-get install -y \
        --no-install-recommends nvidia-jetpack=7.2.1-b49
    chroot "${ROOTFS}" dpkg --audit
    chroot "${ROOTFS}" apt-get clean
    rm -f "${ROOTFS}/opt/nvidia/l4t-packages/.nv-l4t-disable-boot-fw-update-in-preinstall"
    cleanup_sdk
    touch "${BSP}/.seeed-jetpack-installed"
fi
if [[ ${J40MINI_PREINSTALL_JETPACK} == 0 ]]; then
    echo 'Skipping optional JetPack SDK preinstallation; assembling the R39.2.1 BSP image.'
    # A terminal interrupt can leave the previous SDK helper's temporary files.
    export HOST_QEMU_VERSION="${HOST_QEMU_VERSION:-}"
    source "${BSP}/nv_tegra/nv_qemu_setup_helper.sh"
    qemu_cleanup_rootfs "${ROOTFS}"
    cleanup_sdk
    rm -f "${ROOTFS}/opt/nvidia/l4t-packages/.nv-l4t-disable-boot-fw-update-in-preinstall"
fi
# Replace stock modules completely before installing the compiled Seeed modules.
RELEASE="$(cat "${WORK}/artifacts/kernel-release.txt")"
MOD_DIR="$(realpath -m "${ROOTFS}/lib/modules/${RELEASE}")"
[[ ${MOD_DIR} == "${ROOTFS}/"* && ${RELEASE} == 6.8.12-1021-tegra ]]
rm -rf -- "${MOD_DIR}"
tar --keep-directory-symlink --numeric-owner -xpf "${WORK}/artifacts/kernel-rootfs.tar.gz" -C "${ROOTFS}"
depmod -b "${ROOTFS}" "${RELEASE}"
(cd "${BSP}" && bash ./tools/l4t_update_initrd.sh)
python3 "${PROJECT_DIR}/scripts/provision_seeed_j40mini_r3921.py"
# Restore systemd-resolved's normal per-device DNS management.
rm -f "${ROOTFS}/etc/resolv.conf"
ln -s ../run/systemd/resolve/stub-resolv.conf "${ROOTFS}/etc/resolv.conf"
mapfile -t L4T_PACKAGES < <(dpkg-query --admindir="${ROOTFS}/var/lib/dpkg" \
    -W -f='${Package} ${db:Status-Status}\n' 'nvidia-l4t-*' | awk '$2 == "installed" {print $1}')
chroot "${ROOTFS}" /usr/bin/true 2>/dev/null || {
    # l4t_create_default_user removes its temporary QEMU binary.
    cp /usr/bin/qemu-aarch64-static "${ROOTFS}/usr/bin/"
}
chroot "${ROOTFS}" apt-mark hold "${L4T_PACKAGES[@]}"
chroot "${ROOTFS}" apt-get clean
chroot "${ROOTFS}" dpkg --audit > "${WORK}/logs/dpkg-audit.txt"
[[ ! -s "${WORK}/logs/dpkg-audit.txt" ]] || {
    cat "${WORK}/logs/dpkg-audit.txt" >&2
    exit 1
}
chroot "${ROOTFS}" apt-get check
if [[ ${J40MINI_PREINSTALL_JETPACK} == 1 ]]; then
    chroot "${ROOTFS}" apt-get -s --no-remove install nvidia-jetpack=7.2.1-b49 \
        > "${WORK}/logs/jetpack-installed-validation.txt"
fi
rm -f "${ROOTFS}/usr/bin/qemu-aarch64-static"
touch "${BSP}/.seeed-rootfs-assembled"
python3 "${PROJECT_DIR}/scripts/verify_seeed_j40mini.py"
date -Is > "${WORK}/.offline-rootfs-complete"
chown "${TASK_UID}:${TASK_GID}" "${WORK}/.offline-rootfs-complete"
echo 'R39.2.1 rootfs completed. Use prepare_seeed_j40mini_images.sh separately; no target operation was performed.'
