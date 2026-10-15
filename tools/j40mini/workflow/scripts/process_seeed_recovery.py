#!/usr/bin/env python3
"""Validate measured J40 Mini identity/storage and generated local images."""
import csv
import hashlib
import json
import os
import re
import shlex
import subprocess
import struct
import sys
import tempfile
import tarfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
VERSION = '39.2.1'
SUFFIX = '-r39.2.1'
WORK = PROJECT / ('work/seeed-j40mini-r' + VERSION)
BSP = WORK / 'Linux_for_Tegra'
APP_SIZE = 64 * 1024**3


def write_json(path, value):
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix='.' + path.name,
                                     delete=False) as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
        temporary = Path(stream.name)
    temporary.replace(path)
    if path.parent == PROJECT / 'analysis':
        path.chmod(0o644)
        if os.geteuid() == 0 and 'SUDO_UID' in os.environ:
            os.chown(path, int(os.environ['SUDO_UID']), int(os.environ['SUDO_GID']))


def unique(pattern, text):
    matches = set(re.findall(pattern, text))
    assert len(matches) == 1, f'Expected one distinct match for {pattern}: {matches}'
    return matches.pop()


def identity(run):
    def field(flag, filename):
        return subprocess.check_output([str(BSP / 'bootloader/chkbdinfo'), flag,
                                        str(run / filename)], text=True).strip()
    info = {key: field(flag, 'cvm.bin') for key, flag in
            [('boardid', '-i'), ('fab', '-f'), ('sku', '-k'), ('revision', '-r')]}
    assert (info['boardid'], info['sku']) == ('3767', '0000'), info
    assert re.fullmatch(r'[A-Za-z0-9]+', info['fab']), info
    assert re.fullmatch(r'[A-Za-z0-9.]+', info['revision']), info
    info['chip_sku'] = field('-C', 'chip_info.bin_bak')
    assert info['chip_sku'] == '00:00:00:D3', info
    info['ramcode'] = int(field('-R', 'chip_info.bin_bak').split(':')[-1], 16) % 16
    text = (run / 'recovery-info.log').read_text(errors='replace')
    info['ecid'] = unique(r'ECID is (0x[0-9a-fA-F]+)', text)
    assert int(info['ecid'], 16) > 0
    info['fuselevel'] = unique(r'Chip SKU\([^\n]+?fuselevel\((fuselevel_[a-z]+)\)', text)
    assert info['fuselevel'] in ('fuselevel_nofuse', 'fuselevel_production')
    info['boot_authentication'] = unique(r'Boot Authentication:\s*([A-Za-z0-9]+)', text)
    assert info['boot_authentication'] in ('NS', 'None'), 'Signing keys required; do not continue unsigned'
    ramcodes = {int(value) for value in re.findall(r'RAMCODE is (\d+)', text)}
    if ramcodes:
        assert ramcodes == {info['ramcode']}, ramcodes
    info['measured_at'] = datetime.fromtimestamp(
        (run / 'recovery-info.log').stat().st_mtime, timezone.utc).isoformat()
    write_json(run / 'recovery-identity.json', info)
    print('Measured module:', {k: v for k, v in info.items() if k != 'ecid'})


def capacity(run):
    info = json.loads((run / 'recovery-identity.json').read_text())
    storage = dict(line.split('=', 1) for line in (run / 'nvme-info.txt').read_text().splitlines())
    for key in ('bytes', 'logical_sector_size', 'sectors_512', 'read_only'):
        storage[key] = int(storage[key])
    assert storage['read_only'] == 1
    # This Orin workflow currently supports the factory 512-byte NVMe format.
    assert storage['logical_sector_size'] == 512, 'Review layout support before using a non-512-byte namespace'
    assert storage['bytes'] == storage['sectors_512'] * 512
    assert storage['bytes'] >= APP_SIZE + 5 * 1024**3, 'NVMe too small for this 64 GiB APP layout'
    assert 'nvidia,p3767-0000' in storage['compatible'] and 'nvidia,tegra234' in storage['compatible']
    assert storage['kernel'] == '6.8.12-1021-tegra'
    info['nvme'] = storage
    write_json(run / 'measured.json', info)
    safe = {k: v for k, v in info.items() if k != 'ecid'}
    write_json(PROJECT / 'analysis/seeed-j40mini-r39.2.1-recovery-parameters.json', safe)
    env = {'BOARDID': info['boardid'], 'BOARDSKU': info['sku'], 'FAB': info['fab'],
           'BOARDREV': info['revision'], 'CHIP_SKU': info['chip_sku'],
           'RAMCODE_ID': str(info['ramcode']), 'FUSELEVEL': info['fuselevel'],
           'EXT_NUM_SECTORS': str(storage['sectors_512'])}
    (run / 'measured.env').write_text(''.join(f'export {key}={shlex.quote(value)}\n' for key, value in env.items()))
    print('Measured NVMe:', storage)


def layout(run):
    measured = json.loads((run / 'measured.json').read_text())
    tree = ET.parse(BSP / 'tools/kernel_flash/flash_l4t_t234_nvme.xml')
    devices = tree.findall("device[@type='external']")
    assert len(devices) == 1
    device = devices[0]
    assert device.get('sector_size') == '512'
    device.set('num_sectors', str(measured['nvme']['sectors_512']))
    app = device.find("partition[@name='APP']")
    assert app is not None and app.get('id') == '1'
    assert app.findtext('allocation_attribute').strip() == '0x808'
    output = BSP / 'tools/kernel_flash/flash_l4t_t234_j40mini_measured_nvme.xml'
    # NVIDIA's limit_boot_chains/limit_qspi_device use `while read` without
    # handling an unterminated last line. Preserve the final closing tag.
    output.write_text(ET.tostring(tree.getroot(), encoding='unicode') + '\n')
    output.chmod(0o644)
    print('Layout uses measured NVMe sectors; APP image size is 64 GiB.')


def digest(path, algorithm='sha256'):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, algorithm).hexdigest()


def read_flash_config(path):
    values = {}
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        key, value = line.split('=', 1)
        assert re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key), line
        words = shlex.split(value)
        assert len(words) <= 1, line
        value = words[0] if words else ''
        assert key not in values or values[key] == value, key
        values[key] = value
    return values


def verify_rootfs_archive(path):
    """Verify the delivered tar against the compiled files, without extracting."""
    root = WORK / 'artifacts/rootfs'
    wanted = {
        'boot/Image': digest(root / 'boot/Image'),
        'boot/dtb/tegra234-j40mini-p3768-0000+p3767-0000-recomputer.dtb':
            digest(root / 'boot/dtb/tegra234-j40mini-p3768-0000+p3767-0000-recomputer.dtb'),
    }
    for module in (root / 'lib/modules/6.8.12-1021-tegra').rglob('*.ko'):
        # The Ubuntu rootfs has /lib -> usr/lib.
        wanted['usr/' + str(module.relative_to(root))] = digest(module)
    module_count = len(wanted) - 2
    shadow_checked = VERSION == '39.2.0'
    if VERSION == '39.2.1':
        for relative in ('var/lib/dpkg/status', 'etc/passwd', 'boot/initrd'):
            wanted[relative] = digest(BSP / 'rootfs' / relative)
        for relative in ('etc/NetworkManager/system-connections/unitree-robot.nmconnection',
                         'etc/systemd/timesyncd.conf.d/90-unitree-ntp.conf',
                         'etc/ssh/sshd_config.d/20-unitree-headless.conf'):
            wanted[relative] = digest(PROJECT / 'configs/seeed-j40mini' / relative)
        wanted['etc/apt/preferences.d/00-j40mini-r39.2.1.pref'] = digest(
            BSP / 'extra_scripts/99-seeed-freeze-jp.pref')
    seen = set()
    process = subprocess.Popen(['zstd', '-dc', str(path)], stdout=subprocess.PIPE)
    try:
        with tarfile.open(fileobj=process.stdout, mode='r|') as archive:
            for member in archive:
                name = member.name.removeprefix('./')
                if VERSION == '39.2.1':
                    assert not name.startswith('etc/ssh/ssh_host_'), 'Shared SSH host key in image'
                if name in wanted:
                    assert member.isfile(), name
                    if VERSION == '39.2.1':
                        assert member.uid == 0, f'System file must belong to root: {name}'
                    stream = archive.extractfile(member)
                    assert hashlib.file_digest(stream, 'sha256').hexdigest() == wanted[name], name
                    seen.add(name)
                elif VERSION == '39.2.1' and name == 'etc/shadow':
                    lines = archive.extractfile(member).read().decode().splitlines()
                    account, = [line.split(':') for line in lines if line.startswith('unitree:')]
                    assert account[1] == json.loads((WORK / 'headless-profile.json').read_text())['password_hash']
                    shadow_checked = True
        # Drain tar padding so zstd can finish validating its frame/checksum.
        while process.stdout.read(1024 * 1024):
            pass
        process.stdout.close()
        assert process.wait() == 0, 'Rootfs archive zstd integrity failed'
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait()
    assert seen == wanted.keys(), f'Missing compiled files in rootfs archive: {wanted.keys() - seen}'
    assert shadow_checked, 'Headless account missing from delivered rootfs'
    return {'format': 'zstd compressed tar', 'kernel_and_dtb_match': True,
            'compiled_modules_match': module_count, 'compression_integrity': 'passed',
            **({'package_metadata_and_headless_profile_match': True} if VERSION == '39.2.1' else {})}


def verify_boot_image(path, kernel, initrd):
    """Check the actual normal-boot payload, not just the filesystem's copies."""
    with path.open('rb') as stream:
        header = stream.read(2048)
        assert header[:8] == b'ANDROID!', f'Review boot image format: {path}'
        kernel_size = struct.unpack_from('<I', header, 8)[0]
        ramdisk_size = struct.unpack_from('<I', header, 16)[0]
        page_size = struct.unpack_from('<I', header, 36)[0]
        assert page_size in (2048, 4096, 8192), page_size
        assert kernel_size == kernel.stat().st_size
        assert ramdisk_size == initrd.stat().st_size
        stream.seek(page_size)
        assert hashlib.sha256(stream.read(kernel_size)).hexdigest() == digest(kernel)
        stream.seek(page_size + ((kernel_size + page_size - 1) // page_size) * page_size)
        assert hashlib.sha256(stream.read(ramdisk_size)).hexdigest() == digest(initrd)
    return {'format': 'Android boot image', 'kernel_matches_compiled': True,
            'ramdisk_matches_rootfs_initrd': True}


def images(run):
    measured = json.loads((run / 'measured.json').read_text())
    image_dir = BSP / 'tools/kernel_flash/images'
    summary = {}
    for kind in ('internal', 'external'):
        folder = image_dir / kind
        config = read_flash_config(folder / 'flash.cfg')
        rows = list(csv.reader((folder / 'flash.idx').read_text().splitlines(), skipinitialspace=True))
        assert rows, kind
        partitions = []
        for row in rows:
            assert len(row) >= 8, row
            name = row[1].strip().split(':')[-1]
            offset, size = int(row[2].strip()), int(row[3].strip())
            filename = row[4].strip()
            file_source = 'flash.idx'
            if not filename:
                # NVIDIA packages filesystem images through flash.cfg rather
                # than the filename column of flash.idx (e.g. APP_ext).
                key = name + ('_ext' if kind == 'external' else '')
                filename = config.get(key, '')
                if filename:
                    file_source = 'flash.cfg:' + key
            if filename:
                assert Path(filename).name == filename, filename
                path = folder / filename
                assert path.is_file() and path.stat().st_size > 0, str(path)
                assert path.stat().st_size <= size, f'Payload exceeds partition: {name}'
                expected = row[7].strip()
                # APP may be repackaged as a compressed tar by NVIDIA's tool;
                # in that case its authoritative hash is the sidecar.
                sidecar = Path(str(path) + '.sha1sum')
                if name == 'APP' and sidecar.exists():
                    expected = sidecar.read_text().split()[0]
                if name == 'APP':
                    assert sidecar.is_file() and re.fullmatch(r'[0-9a-fA-F]{40}', expected), str(path)
                if re.fullmatch(r'[0-9a-fA-F]{40}', expected):
                    assert digest(path, 'sha1') == expected.lower(), str(path)
            if kind == 'external':
                assert offset >= 0 and size >= 0 and offset + size <= measured['nvme']['bytes'], row
            elif VERSION == '39.2.1':
                assert offset >= 0 and size >= 0 and offset + size <= 64 * 1024**2, row
            partitions.append({'name': name, 'offset': offset, 'size': size,
                               'file': filename, 'file_source': file_source})
        if kind == 'external':
            app, = [p for p in partitions if p['name'] == 'APP']
            assert app['size'] >= APP_SIZE and app['file'], app
            assert any(p['name'] == 'secondary_gpt' for p in partitions)
            if VERSION == '39.2.1':
                assert app['size'] == APP_SIZE
                gpt, = [p for p in partitions if p['name'] == 'secondary_gpt']
                assert gpt['offset'] + gpt['size'] == measured['nvme']['bytes']
        if VERSION == '39.2.1':
            ordered = sorted(partitions, key=lambda p: p['offset'])
            for left, right in zip(ordered, ordered[1:]):
                assert left['offset'] + left['size'] <= right['offset'], (left, right)
        summary[kind] = partitions
    app, = [p for p in summary['external'] if p['name'] == 'APP']
    rootfs_archive = verify_rootfs_archive(image_dir / 'external' / app['file'])
    boot_validation = {}
    if VERSION == '39.2.1':
        boot_partition, = [p for p in summary['external'] if p['name'] == 'A_kernel']
        boot_validation = verify_boot_image(image_dir / 'external' / boot_partition['file'],
                                            WORK / 'artifacts/rootfs/boot/Image', BSP / 'rootfs/boot/initrd')
    inventory = [{'path': str(p.relative_to(image_dir)), 'bytes': p.stat().st_size,
                  'sha256': digest(p)} for p in sorted(image_dir.rglob('*')) if p.is_file()]
    report_path = PROJECT / f'analysis/seeed-j40mini{SUFFIX}-flash-images.json'
    previous = json.loads(report_path.read_text()) if report_path.exists() else {}
    same_package = previous.get('files') == inventory
    report = {'generated_at': datetime.now(timezone.utc).isoformat(),
              'board': 'recomputer-orin-j40mini', 'l4t': VERSION,
              'module': {k: v for k, v in measured.items() if k != 'ecid'},
              'app_image_size_bytes': APP_SIZE, 'partitions': summary,
              'rootfs_archive_validation': rootfs_archive,
              **({'normal_boot_payload_validation': boot_validation} if VERSION == '39.2.1' else {}),
              'files': inventory, 'physically_flashed': bool(same_package and previous.get('physically_flashed')),
              'generation_log': str(run / 'image-generation.log')}
    if same_package and previous.get('flash_completed_at'):
        report['flash_completed_at'] = previous['flash_completed_at']
    write_json(report_path, report)
    write_json(run / 'images-validation.json', report)
    (run / 'IMAGES_GENERATED_NO_FLASH').touch()
    marker = WORK / '.images-generated-no-flash'
    marker.write_text(str(run) + '\n')
    if os.geteuid() == 0 and 'SUDO_UID' in os.environ:
        uid, gid = int(os.environ['SUDO_UID']), int(os.environ['SUDO_GID'])
        for item in [image_dir, *image_dir.rglob('*'), marker]:
            os.chown(item, uid, gid)
    print(f'QSPI/NVMe package generated and indexed: {image_dir}; {len(inventory)} files. No flash performed.')


if __name__ == '__main__':
    assert len(sys.argv) == 3 and sys.argv[1] in ('identity', 'capacity', 'layout', 'images')
    globals()[sys.argv[1]](Path(sys.argv[2]).resolve())
