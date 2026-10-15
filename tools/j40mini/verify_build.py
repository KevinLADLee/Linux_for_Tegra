#!/usr/bin/env python3
"""Read-only offline checks for the R39.2.1 J40 Mini / NX 16GB build."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import tempfile


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bsp', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--staged-rootfs', type=Path, help='Also compare installed kernel, DTB and modules')
    args = parser.parse_args()
    bsp = args.bsp.resolve()
    out = bsp / 'source/kernel_out'
    kernel = out / 'kernel/kernel-noble'
    release = (kernel / 'include/config/kernel.release').read_text().strip()
    assert release == '6.8.12-1021-tegra', release
    assert 'REVISION: 2.1,' in (bsp / 'nv_tegra/nv_tegra_release').read_text()
    assert not list((bsp / 'nv_tegra/l4t_deb_packages').glob('*39.2.0*.deb'))
    dtb_name = 'tegra234-j40mini-p3768-0000+p3767-0000-recomputer.dtb'
    dtb = out / 'build/nvidia-public/devicetree/generic-dtbs' / dtb_name
    compatible = run('fdtget', '-t', 's', str(dtb), '/', 'compatible')
    assert 'nvidia,p3767-0000' in compatible and '-super' in compatible
    callback = '''
LDK_DIR="$1"
source "$LDK_DIR/recomputer-orin-j40mini.conf"
board_sku=0000
board_FAB=300
update_flash_args
printf '%s\\n' "$DTB_FILE" "$TBCDTB_FILE" "$BPFDTB_FILE" "$PINMUX_CONFIG" "$PMC_CONFIG"
'''
    selected = run('bash', '-c', callback, 'verify-j40mini', str(bsp)).splitlines()
    assert selected == [dtb_name, dtb_name, 'tegra234-bpmp-3767-0000-3768-super.dtb',
                        'recomputer-mini-orin-j40-pinmux-p3767-0000.dtsi',
                        'recomputer-mini-orin-j40-padvoltage-p3767-0000.dtsi'], selected
    assert (bsp / 'bootloader/generic' / selected[2]).is_file()
    assert all((bsp / 'bootloader/generic/BCT' / name).is_file() for name in selected[3:])
    overlays = ['L4TConfiguration.dtbo', 'tegra234-carveouts.dtbo', 'tegra-optee.dtbo',
                'T234SetFmpImageTypeGuid.dtbo', 'tegra234-p3768-0000+p3767-0000-dynamic.dtbo']
    with tempfile.TemporaryDirectory(prefix='j40mini-verify-') as temp:
        merged = Path(temp) / 'merged.dtb'
        run('fdtoverlay', '-i', str(dtb), '-o', str(merged),
            *(str(bsp / 'kernel/dtb' / name) for name in overlays))
        assert run('fdtget', '-t', 's', str(merged), '/', 'compatible') == compatible
    modules = sorted(out.rglob('*.ko'))
    assert modules, 'No compiled modules'
    for module in modules:
        data = module.read_bytes()
        assert data[:4] == b'\x7fELF' and data[5] == 1, str(module)
        assert struct.unpack_from('<H', data, 18)[0] == 183, str(module)
        vermagic = re.search(rb'vermagic=([^\0]+)', data)
        assert vermagic and vermagic[1].split()[0].decode() == release, str(module)
    for name in ('nvgpu.ko', 'mttcan.ko', 'nvidia.ko', 'nvidia-modeset.ko', 'nvidia-drm.ko'):
        assert any(module.name == name for module in modules), name
    staged_count = None
    if args.staged_rootfs:
        root = args.staged_rootfs.resolve()
        assert sha256(root / 'boot/Image') == sha256(kernel / 'arch/arm64/boot/Image')
        assert sha256(root / 'boot/dtb' / dtb_name) == sha256(dtb)
        staged = sorted((root / 'lib/modules' / release).rglob('*.ko'))
        assert len(staged) == len(modules), (len(staged), len(modules))
        assert (root / 'lib/modules' / release / 'modules.dep').stat().st_size > 0
        # modules_install may strip and sign the installed copies: verify their ABI.
        for module in staged:
            data = module.read_bytes()
            assert data[:4] == b'\x7fELF' and struct.unpack_from('<H', data, 18)[0] == 183
            assert re.search(rb'vermagic=([^\0]+)', data)[1].split()[0].decode() == release
        staged_count = len(staged)
    print(json.dumps({'l4t': '39.2.1', 'board': 'recomputer-orin-j40mini',
                      'module_sku': '0000', 'kernel_release': release,
                      'compiled_modules': len(modules), 'staged_modules': staged_count,
                      'dtb_compatible': compatible, 'super_callback': 'passed',
                      'default_overlay_merge': 'passed',
                      'kernel_image_sha256': sha256(kernel / 'arch/arm64/boot/Image'),
                      'target_dtb_sha256': sha256(dtb),
                      'target_accessed': False, 'hardware_validated': False}, indent=2))


if __name__ == '__main__':
    main()
