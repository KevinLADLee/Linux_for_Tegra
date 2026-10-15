#!/usr/bin/env bash
# Flash the existing, verified J40 Mini package to its measured Orin NX module.
# Explicit destructive entry point; no image regeneration.
set -euo pipefail
export SEEED_L4T_VERSION="${SEEED_L4T_VERSION:-39.2.1}"
source "$(dirname -- "${BASH_SOURCE[0]}")/seeed_j40mini_env.sh"
REPORT_SUFFIX="-r39.2.1"
BSP="${WORK}/Linux_for_Tegra"
[[ $# == 0 ]] || { echo "Usage: sudo bash $0" >&2; exit 2; }
[[ ${EUID} == 0 ]] || { echo 'Run with sudo; physical flashing requires root.' >&2; exit 1; }
[[ -f "${WORK}/.images-generated-no-flash" ]] || { echo 'No verified image package.' >&2; exit 1; }
if [[ ${SEEED_L4T_VERSION} == 39.2.1 ]]; then
    [[ -f "${WORK}/.offline-build-complete" ]] || { echo 'Offline build is incomplete.' >&2; exit 1; }
fi
RUN="$(realpath "$(cat "${WORK}/.images-generated-no-flash")")"
[[ $(dirname "${RUN}") == "${WORK}/operations" && -f "${RUN}/IMAGES_GENERATED_NO_FLASH" ]] || {
    echo 'Invalid image-generation record.' >&2; exit 1;
}
mapfile -t devices < <(lsusb -d 0955:7323)
[[ ${#devices[@]} == 1 ]] || {
    echo 'Put the J40 Mini / NX 16GB in Force Recovery: hold RECOVERY while applying power, then release.' >&2
    echo 'Expected exactly one APX device 0955:7323.' >&2; exit 1;
}
umask 077
mkdir -p "${WORK}/operations"
FLASH_RUN="$(mktemp -d "${WORK}/operations/flash-XXXXXXXX")"
TASK_UID="${SUDO_UID:-0}"
TASK_GID="${SUDO_GID:-0}"
touch "${FLASH_RUN}/flash.log"
chown "${TASK_UID}:${TASK_GID}" "${FLASH_RUN}" "${FLASH_RUN}/flash.log"
trap 'chown -R "${TASK_UID}:${TASK_GID}" "${FLASH_RUN}"' EXIT
exec > >(tee "${FLASH_RUN}/flash.log") 2>&1
python3 - "${PROJECT_DIR}" "${WORK}" "${SEEED_L4T_VERSION}" "${REPORT_SUFFIX}" <<'PY'
import hashlib,json,shlex,sys
from pathlib import Path
p,w=map(Path,sys.argv[1:3]);version,suffix=sys.argv[3:5];b=w/'Linux_for_Tegra'
r=json.loads((p/f'analysis/seeed-j40mini{suffix}-flash-images.json').read_text())
assert r['board']=='recomputer-orin-j40mini' and r['l4t']==version
assert r['module']['boardid']=='3767' and r['module']['sku']=='0000'
assert r['module']['nvme']['bytes']==r['module']['nvme']['sectors_512']*512
assert r['module']['nvme']['bytes']>=r['app_image_size_bytes']+5*1024**3
for entry in r['files']:
 path=b/'tools/kernel_flash/images'/entry['path']
 assert path.resolve().is_relative_to((b/'tools/kernel_flash/images').resolve()),entry['path']
 assert path.stat().st_size==entry['bytes'],entry['path']
 with path.open('rb') as stream:
  assert hashlib.file_digest(stream,'sha256').hexdigest()==entry['sha256'],entry['path']
params=shlex.split((b/'tools/kernel_flash/initrdflashparam.txt').read_text())
assert params[-2:]==['recomputer-orin-j40mini','external'],params
assert not set(params)&{'--initrd','--boot-rootfs','--direct','--external-only','--erase-all'},params
print('Verified package hashes, target board, measured SSD geometry and stored flash parameters.')
PY
cd "${BSP}/bootloader"
timeout 25s ./tegrarcm_v2 --new_session --chip 0x23 0 --uid | tee "${FLASH_RUN}/bootrom-probe.log"
python3 - "${PROJECT_DIR}" "${RUN}" "${FLASH_RUN}" <<'PY'
import json,re,sys
from pathlib import Path
p,r,f=map(Path,sys.argv[1:]);measurement=json.loads((r/'measured.json').read_text())
assert 'ecid' in measurement, 'Original private Recovery measurement is required; no CID is guessed.'
expected=measurement['ecid'].lower()
ids={value.lower() for value in re.findall(r'BR_CID:\s*(0x[0-9a-fA-F]+)',(f/'bootrom-probe.log').read_text())}
assert ids=={expected},'Connected device does not match the measured module; refusing to flash.'
print('Live Recovery ECID matches the module used to generate this package.')
PY
unset BOARDID BOARDSKU FAB BOARDREV CHIP_SKU RAMCODE_ID FUSELEVEL
unset ROOTFS_AB ROOTFS_ENC ERASE_QSPI UNIFIED_FLASH FLASHING_KERNEL FLASHING_UEFI
cd "${BSP}"
echo 'Flashing the verified QSPI and NVMe package now. Keep power and USB connected.'
bash tools/kernel_flash/l4t_initrd_flash.sh --flash-only --network usb0 --showlogs \
    recomputer-orin-j40mini external
python3 - "${PROJECT_DIR}" "${FLASH_RUN}" "${SEEED_L4T_VERSION}" "${REPORT_SUFFIX}" <<'PY'
import importlib.util,json,sys
from pathlib import Path
from datetime import datetime,timezone
p,f=map(Path,sys.argv[1:3]);version,suffix=sys.argv[3:5]
spec=importlib.util.spec_from_file_location('record',p/'scripts/process_seeed_recovery.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
report={'completed_at':datetime.now(timezone.utc).isoformat(),'board':'recomputer-orin-j40mini',
        'l4t':version,'physically_flashed':True,'vendor_flash_exit_code':0,
        'installed_system_verified':False,'log':str(f/'flash.log')}
m.write_json(p/f'analysis/seeed-j40mini{suffix}-flash-result.json',report)
m.write_json(f/'flash-result.json',report)
package_path=p/f'analysis/seeed-j40mini{suffix}-flash-images.json'
package=json.loads(package_path.read_text());package['physically_flashed']=True
package['flash_completed_at']=report['completed_at'];m.write_json(package_path,package)
PY
touch "${FLASH_RUN}/FLASH_COMPLETED"
echo "Flashing completed. Log: ${FLASH_RUN}/flash.log"
if [[ ${SEEED_L4T_VERSION} == 39.2.1 ]]; then
    echo 'Headless unitree account is preconfigured. Wait for first boot, then verify the installed system.'
else
    echo 'Complete Ubuntu OEM setup on the attached display, then verify the installed system.'
fi
