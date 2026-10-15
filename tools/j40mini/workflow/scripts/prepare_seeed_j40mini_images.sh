#!/usr/bin/env bash
# Read this module, inspect NVMe from a RAM-only initrd, generate NO-FLASH images.
# Never invokes a target storage writer. Run once with sudo in a local terminal.
set -euo pipefail
export SEEED_L4T_VERSION=39.2.1
source "$(dirname -- "${BASH_SOURCE[0]}")/seeed_j40mini_env.sh"
BSP="${WORK}/Linux_for_Tegra"
BOARD=recomputer-orin-j40mini
MODE=all
if [[ $# == 1 && ( $1 == --resume || $1 == --verify-only ) ]]; then
    MODE="${1#--}"
elif [[ $# != 0 ]]; then
    echo "Usage: sudo bash $0 [--resume|--verify-only]" >&2; exit 2
fi
[[ ${EUID} == 0 ]] || { echo 'Run with sudo: USB access and local image generation require root.' >&2; exit 1; }
umask 077
mkdir -p "${WORK}/operations"
if [[ ${MODE} != all ]]; then
    RUN="$(realpath "$(cat "${WORK}/operations/latest-path.txt")")"
    [[ $(dirname "${RUN}") == "${WORK}/operations" && -f "${RUN}/measured.json" ]] || {
        echo 'No complete previous identity/capacity record to resume.' >&2; exit 1;
    }
else
    RUN="$(mktemp -d "${WORK}/operations/prepare-XXXXXXXX")"
fi
TASK_UID="${SUDO_UID:-0}"
TASK_GID="${SUDO_GID:-0}"
cleanup() {
    chown -R "${TASK_UID}:${TASK_GID}" "${RUN}"
}
trap cleanup EXIT
printf '%s\n' "${RUN}" > "${WORK}/operations/latest-path.txt"
chown "${TASK_UID}:${TASK_GID}" "${WORK}/operations" "${WORK}/operations/latest-path.txt" "${RUN}"
exec > >(tee -a "${RUN}/operation.log") 2>&1
if [[ ${MODE} == verify-only ]]; then
    python3 "${PROJECT_DIR}/scripts/process_seeed_recovery.py" images "${RUN}"
    python3 "${PROJECT_DIR}/scripts/verify_seeed_j40mini.py"
    echo 'Existing images verified. No image regeneration or target operation was performed.'
    exit 0
fi
unset BOARDID BOARDSKU FAB BOARDREV CHIP_SKU RAMCODE_ID FUSELEVEL
unset SKIP_EEPROM_CHECK ERASE_QSPI ROOTFS_AB ROOTFS_ENC EXT_NUM_SECTORS
unset UNIFIED_FLASH FLASHING_KERNEL FLASHING_UEFI
[[ -f "${BSP}/.seeed-rootfs-assembled" ]] || { echo 'Assemble rootfs first.' >&2; exit 1; }
python3 "${PROJECT_DIR}/scripts/verify_seeed_j40mini.py"
[[ $(df -B1 --output=avail "${WORK}" | tail -1) -gt 150000000000 ]] || {
    echo 'At least 150 GB free required for image generation.' >&2; exit 1;
}
cd "${BSP}"
if [[ ${MODE} == all ]]; then
mapfile -t devices < <(lsusb -d 0955:7323)
[[ ${#devices[@]} == 1 ]] || { echo 'Expected exactly one NX 16GB APX device (0955:7323).' >&2; exit 1; }
echo 'Reading EEPROM and fuse information only.'
timeout 180s bash flash.sh --read-info "${BOARD}" internal | tee "${RUN}/recovery-info.log"
cp bootloader/cvm.bin bootloader/chip_info.bin_bak "${RUN}/"
python3 "${PROJECT_DIR}/scripts/process_seeed_recovery.py" identity "${RUN}"
echo 'Booting temporary initrd in RAM; --initrd exits before any storage flashing.'
# Both host flash_through_ssh() and the initrd init script explicitly branch on
# initrd_only; this mode neither starts the storage writer nor mounts the SSD.
timeout 240s bash tools/kernel_flash/l4t_initrd_flash.sh \
    --initrd --network usb0 --showlogs "${BOARD}" internal | tee "${RUN}/initrd-boot.log"
SSH=(sshpass -p root ssh -q -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
    -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3)
timeout 60s "${SSH[@]}" root@fc00:1:1::2 'bash -se' > "${RUN}/nvme-info.txt" <<'TARGET'
set -euo pipefail
disks=(/sys/block/nvme*n*)
[[ ${#disks[@]} == 1 && ${disks[0]} == /sys/block/nvme0n1 ]] || {
    echo 'Expected exactly one NVMe namespace nvme0n1.' >&2; exit 1;
}
if grep -qE '^/dev/nvme' /proc/mounts; then
    echo 'NVMe is mounted; refusing this probe.' >&2; exit 1
fi
blockdev --setro /dev/nvme0n1
printf 'bytes=%s\n' "$(blockdev --getsize64 /dev/nvme0n1)"
printf 'logical_sector_size=%s\n' "$(blockdev --getss /dev/nvme0n1)"
printf 'sectors_512=%s\n' "$(cat /sys/class/block/nvme0n1/size)"
printf 'read_only=%s\n' "$(blockdev --getro /dev/nvme0n1)"
printf 'model=%s\n' "$(cat /sys/class/block/nvme0n1/device/model)"
printf 'compatible=%s\n' "$(tr '\0' ' ' < /proc/device-tree/compatible)"
printf 'kernel=%s\n' "$(uname -r)"
TARGET
else
    echo 'Resuming offline generation from the previously measured module and NVMe; not booting a target.'
    python3 "${PROJECT_DIR}/scripts/process_seeed_recovery.py" identity "${RUN}"
fi
python3 "${PROJECT_DIR}/scripts/process_seeed_recovery.py" capacity "${RUN}"
# Only values validated from this run are sourced, never old AS2 parameters.
source "${RUN}/measured.env"
XML="tools/kernel_flash/flash_l4t_t234_j40mini_measured_nvme.xml"
python3 "${PROJECT_DIR}/scripts/process_seeed_recovery.py" layout "${RUN}"
echo 'Generating local QSPI + NVMe images with --no-flash.'
# Do not reuse temporary probe files or any prior image output.
rm -f "${WORK}/.images-generated-no-flash"
if [[ -d tools/kernel_flash/images ]]; then
    mv tools/kernel_flash/images "${RUN}/images-before-$(date +%Y%m%d-%H%M%S)"
fi
if [[ -f "${RUN}/image-generation.log" ]]; then
    cp -p "${RUN}/image-generation.log" "${RUN}/image-generation-before-$(date +%Y%m%d-%H%M%S).log"
fi
bash tools/kernel_flash/l4t_initrd_flash.sh \
    --no-flash --showlogs --network usb0 \
    --external-device nvme0n1p1 -c "${XML}" -S 64GiB \
    -p '-c bootloader/generic/cfg/flash_t234_qspi.xml --no-systemimg' \
    "${BOARD}" external | tee "${RUN}/image-generation.log"
python3 "${PROJECT_DIR}/scripts/process_seeed_recovery.py" images "${RUN}"
touch "${RUN}/IMAGES_GENERATED_NO_FLASH"
printf '%s\n' "${RUN}" > "${WORK}/.images-generated-no-flash"
chown "${TASK_UID}:${TASK_GID}" "${WORK}/.images-generated-no-flash"
python3 "${PROJECT_DIR}/scripts/verify_seeed_j40mini.py"
date -Is > "${WORK}/.offline-build-complete"
chown "${TASK_UID}:${TASK_GID}" "${WORK}/.offline-build-complete"
echo "Finished generating images. Device storage was not flashed. Record: ${RUN}"
