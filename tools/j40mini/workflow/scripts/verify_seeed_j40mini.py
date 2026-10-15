#!/usr/bin/env python3
"""Validate the local J40 Mini build artifacts, without accessing a Jetson."""
import hashlib
import io
import json
import os
import re
import struct
import subprocess
import tarfile
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
VERSION = '39.2.1'
SUFFIX = '-r39.2.1'
WORK = PROJECT / ('work/seeed-j40mini-r' + VERSION)
BSP = WORK / 'Linux_for_Tegra'
ARTIFACTS = WORK / 'artifacts'
ROOT = ARTIFACTS / 'rootfs'
DTB = 'tegra234-j40mini-p3768-0000+p3767-0000-recomputer.dtb'
COMMIT = 'df17ed28201645fcaeb6c8e6fb82a0b4592faefb'


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    release = (ARTIFACTS / 'kernel-release.txt').read_text().strip()
    assert release == '6.8.12-1021-tegra', release
    assert f'# R39 (release), REVISION: {VERSION[3:]},' in (BSP / 'nv_tegra/nv_tegra_release').read_text()
    assert digest(BSP / 'kernel/Image') == digest(ROOT / 'boot/Image')
    assert digest(BSP / 'kernel/dtb' / DTB) == digest(ROOT / 'boot/dtb' / DTB)
    compatible = run('fdtget', '-t', 's', str(ROOT / 'boot/dtb' / DTB), '/', 'compatible')
    assert 'nvidia,p3767-0000' in compatible and 'nvidia,tegra234' in compatible
    module_dir = ROOT / 'lib/modules' / release
    modules = sorted(module_dir.rglob('*.ko'))
    assert modules, 'No staged modules'
    for module in modules:
        data = module.read_bytes()
        assert data[:4] == b'\x7fELF' and data[5] == 1, str(module)
        assert struct.unpack_from('<H', data, 18)[0] == 183, str(module)
        vermagic = re.search(rb'vermagic=([^\0]+)', data)
        assert vermagic and vermagic[1].startswith((release + ' ').encode()), str(module)
    for name in ('nvgpu.ko', 'mttcan.ko', 'nvidia.ko', 'nvidia-modeset.ko', 'nvidia-drm.ko'):
        assert any(module.name == name for module in modules), name
    assert (module_dir / 'modules.dep').stat().st_size > 0
    initrd_deb, = (BSP / 'nv_tegra/l4t_deb_packages').glob('nvidia-l4t-initrd_*.deb')
    initrd_data = subprocess.check_output(['dpkg-deb', '--fsys-tarfile', str(initrd_deb)])
    with tarfile.open(fileobj=io.BytesIO(initrd_data)) as archive:
        listing = archive.extractfile('./etc/nv-update-initrd/list.d/modules_k6.8').read().decode()
    initrd_module_entries = 0
    for line in listing.splitlines():
        if not line or line.startswith('#'):
            continue
        source = line.split(':')[0].replace('<KERNEL_VERSION>', release).lstrip('/')
        assert list(ROOT.glob(source)), source
        initrd_module_entries += 1
    # Evaluate the board's SKU callback only; no flash command is invoked.
    callback = '''
LDK_DIR="$1"
source "$LDK_DIR/recomputer-orin-j40mini.conf"
board_sku=0000
board_FAB=300
update_flash_args
printf '%s\\n' "$DTB_FILE" "$TBCDTB_FILE" "$BPFDTB_FILE" "$PINMUX_CONFIG" "$PMC_CONFIG"
'''
    selected = run('bash', '-c', callback, 'verify-board', str(BSP)).splitlines()
    assert selected[:2] == [DTB, DTB], selected
    assert (BSP / 'bootloader/generic' / selected[2]).is_file()
    for name in selected[3:]:
        assert (BSP / 'bootloader/generic/BCT' / name).is_file()
    overlays = ['L4TConfiguration.dtbo', 'tegra234-carveouts.dtbo', 'tegra-optee.dtbo',
                'T234SetFmpImageTypeGuid.dtbo',
                'tegra234-p3768-0000+p3767-0000-dynamic.dtbo']
    validation = WORK / 'validation'
    validation.mkdir(exist_ok=True)
    merged = validation / 'j40mini-default-overlays.dtb'
    run('fdtoverlay', '-i', str(BSP / 'kernel/dtb' / DTB), '-o', str(merged),
        *(str(BSP / 'kernel/dtb' / name) for name in overlays))
    assert run('fdtget', '-t', 's', str(merged), '/', 'compatible') == compatible
    rootfs_assembled = (BSP / '.seeed-rootfs-assembled').exists()
    measured_path = PROJECT / 'analysis/seeed-j40mini-r39.2.1-recovery-parameters.json'
    measured = json.loads(measured_path.read_text()) if measured_path.exists() else None
    flash_path = PROJECT / f'analysis/seeed-j40mini{SUFFIX}-flash-result.json'
    flash_result = json.loads(flash_path.read_text()) if flash_path.exists() else {}
    rootfs_checks = {}
    if rootfs_assembled:
        installed = BSP / 'rootfs'
        assert digest(installed / 'boot/Image') == digest(ROOT / 'boot/Image')
        assert digest(installed / 'boot/dtb' / DTB) == digest(ROOT / 'boot/dtb' / DTB)
        for module in modules:
            assert digest(installed / module.relative_to(ROOT)) == digest(module), str(module)
        initrd_hash = digest(installed / 'boot/initrd')
        assert initrd_hash == digest(BSP / 'bootloader/l4t_initrd.img')
        initrd_files = run('lsinitramfs', str(installed / 'boot/initrd'))
        initrd_releases = set(re.findall(r'(?:^|/)lib/modules/([^/\n]+)/', initrd_files, re.M))
        assert initrd_releases == {release}, initrd_releases
        rootfs_checks = {'kernel_dtb_and_all_modules_match': True,
                         'initrd_sha256': initrd_hash, 'initrd_kernel_release': release,
                         'bootloader_initrd_matches': True}
        if VERSION == '39.2.1':
            status = run('dpkg-query', '--admindir=' + str(installed / 'var/lib/dpkg'),
                         '-W', '-f=${Package} ${Version} ${db:Status-Status}\n', 'nvidia-l4t-*')
            versions = {}
            for line in status.splitlines():
                fields = line.split()
                if len(fields) != 3 or fields[2] in ('not-installed', 'config-files'):
                    continue
                assert fields[2] == 'installed', line
                assert '39.2.0-' not in fields[1], line
                versions[fields[0]] = fields[1]
            assert versions['nvidia-l4t-core'] == '39.2.1-20260806224157'
            assert versions['nvidia-l4t-bootloader'] == '39.2.1-20260806224157'
            rootfs_checks['all_bsp_packages_match_r39_2_1'] = True
            sdk_query = subprocess.run(
                ['dpkg-query', '--admindir=' + str(installed / 'var/lib/dpkg'),
                 '-W', '-f=${Version} ${db:Status-Status}', 'nvidia-jetpack'],
                text=True, capture_output=True)
            jetpack = sdk_query.stdout.strip()
            sdk_installed = sdk_query.returncode == 0 and jetpack.endswith(' installed')
            if sdk_query.returncode == 0 and jetpack and not sdk_installed:
                assert jetpack.split()[-1] in ('not-installed', 'config-files'), jetpack
            if sdk_installed or os.environ.get('J40MINI_PREINSTALL_JETPACK', '0') == '1':
                assert jetpack == '7.2.1-b49 installed', jetpack
            rootfs_checks['jetpack_sdk_preinstalled'] = sdk_installed
            rootfs_checks['jetpack'] = '7.2.1-b49' if sdk_installed else 'not preinstalled (optional)'
            for temporary in ('usr/bin/qemu-aarch64-static',
                              'var/lib/dpkg/triggers/ldconfig.backup',
                              'opt/nvidia/l4t-packages/.nv-l4t-disable-boot-fw-update-in-preinstall'):
                assert not (installed / temporary).exists(), temporary
            assert (installed / 'var/lib/dpkg/triggers/ldconfig').is_file()
            assert not list((installed / 'var/cache/apt/archives').rglob('*.deb'))
            accounts = [s.split(':') for s in (installed / 'etc/passwd').read_text().splitlines()]
            unitree, = [a for a in accounts if a[0] == 'unitree']
            assert unitree[2] == '1000' and unitree[5:] == ['/home/unitree', '/bin/bash']
            network = installed / 'etc/NetworkManager/system-connections/unitree-robot.nmconnection'
            assert network.stat().st_mode & 0o777 == 0o600
            for setting in ('interface-name=enP8p1s0', 'address1=192.168.123.18/24', 'never-default=true'):
                assert setting in network.read_text(), setting
            assert 'gateway=' not in network.read_text() and 'mac-address=' not in network.read_text()
            assert (installed / 'etc/localtime').readlink() == Path('/usr/share/zoneinfo/Asia/Shanghai')
            assert 'ntp.aliyun.com' in (installed / 'etc/systemd/timesyncd.conf.d/90-unitree-ntp.conf').read_text()
            assert not list((installed / 'etc/ssh').glob('ssh_host_*'))
            assert (installed / 'etc/systemd/system/multi-user.target.wants/ssh.service').is_symlink()
            assert not (installed / 'etc/systemd/system/default.target').is_symlink()
            rootfs_checks['headless_profile'] = 'unitree; SSH; unique host keys on first boot; Asia/Shanghai; NTP; 192.168.123.18/24'
            if os.geteuid() == 0:
                shadow = next(s.split(':') for s in (installed / 'etc/shadow').read_text().splitlines()
                              if s.startswith('unitree:'))
                assert shadow[1] == json.loads((WORK / 'headless-profile.json').read_text())['password_hash']
                rootfs_checks['requested_password_hash_matches'] = True
    report = {
        'verified_at': datetime.now(timezone.utc).isoformat(),
        'hardware': 'Orin NX 16GB + Seeed Studio J40 Mini',
        'l4t': VERSION, 'seeed_commit': COMMIT,
        **({'fork_commit': (WORK / 'source-revision.txt').read_text().strip()} if VERSION == '39.2.1' else {}),
        'board': 'recomputer-orin-j40mini', 'module_sku': '0000',
        'kernel_release': release, 'module_count': len(modules),
        'all_modules': 'ELF AArch64; vermagic matches kernel release',
        'initrd_module_entries': initrd_module_entries, 'initrd_module_entries_present': True,
        'dtb_compatible': compatible, 'board_callback_files': selected,
        'default_overlay_merge': 'passed', 'default_overlays': overlays,
        'kernel_image_sha256': digest(BSP / 'kernel/Image'),
        'target_dtb_sha256': digest(BSP / 'kernel/dtb' / DTB),
        'kernel_rootfs_tar_sha256': digest(ARTIFACTS / 'kernel-rootfs.tar.gz'),
        'rootfs_assembled': rootfs_assembled, 'rootfs_checks': rootfs_checks,
        'flash_images_generated': (WORK / '.images-generated-no-flash').exists(),
        'physically_flashed': bool(flash_result.get('physically_flashed') and
                                  flash_result.get('vendor_flash_exit_code') == 0),
        'installed_system_verified': bool(flash_result.get('installed_system_verified')),
        'hardware_validated': False,
        'initrd_boot_validated': bool(flash_result.get('physically_flashed') or
                                      (measured and measured.get('nvme', {}).get('kernel') == release)),
        'note': ('Board callback tested with FAB=300; independent Recovery measurement confirms '
                 f"FAB={measured['fab']}, BOARDREV={measured['revision']}. "
                 + ('Installed-system SSH, kernel and basic network configuration are verified; '
                    'full peripheral acceptance has not been performed.'
                    if flash_result.get('installed_system_verified') else
                    'hardware_validated=false means installed-system boot and peripheral acceptance '
                    'have not been performed.') if measured else
                 'Board FAB=300 is a callback test input, not a reading from the module.'),
    }
    (PROJECT / f'analysis/seeed-j40mini{SUFFIX}-build.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
