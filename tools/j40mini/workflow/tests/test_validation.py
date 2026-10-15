"""Guard regressions without USB access, sudo or target operations."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import unittest

FLOW = Path(__file__).resolve().parents[1]
SCRIPTS = FLOW / 'scripts'


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


plan = load('validate_seeed_jetpack_plan')
recovery = load('process_seeed_recovery')


class PackagePlanTests(unittest.TestCase):
    def test_known_opencv_replacements(self):
        text = '\n'.join('Remv ' + name for name in sorted(plan.OPENCV_REPLACEMENTS))
        text += '\nInst libopencv (4.8 test [arm64])\nInst libopencv-dev (4.8 test [arm64])'
        self.assertEqual(len(plan.validate(text)), 10)

    def test_unexpected_removal_or_boot_chain_update(self):
        for text in ['Remv openssh-server', 'Inst nvidia-l4t-bootloader (39.2.1 test [arm64])',
                     'Inst nvidia-l4t-gstreamer (39.2.0 test [arm64])', 'Remv libopencv-core-dev']:
            with self.subTest(text=text), self.assertRaises(AssertionError):
                plan.validate(text)


class ImageTests(unittest.TestCase):
    def test_layout_keeps_final_newline_for_native_shell_filter(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / 'tools/kernel_flash'
            config.mkdir(parents=True)
            (config / 'flash_l4t_t234_nvme.xml').write_text(
                '<partition_layout><device type="external" sector_size="512" num_sectors="1">'
                '<partition name="APP" id="1"><allocation_attribute>0x808</allocation_attribute>'
                '</partition></device></partition_layout>')
            (root / 'measured.json').write_text(json.dumps({'nvme': {'sectors_512': 268435456}}))
            original = recovery.BSP
            try:
                recovery.BSP = root
                recovery.layout(root)
            finally:
                recovery.BSP = original
            data = (config / 'flash_l4t_t234_j40mini_measured_nvme.xml').read_bytes()
            self.assertTrue(data.endswith(b'</partition_layout>\n'))
            self.assertIn(b'num_sectors="268435456"', data)

    def test_normal_boot_payload_tampering_is_detected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            kernel, initrd, boot = [root / n for n in ('Image', 'initrd', 'boot.img')]
            kernel.write_bytes(b'compiled-kernel')
            initrd.write_bytes(b'matching-initrd')
            header = bytearray(2048)
            header[:8] = b'ANDROID!'
            struct.pack_into('<I', header, 8, kernel.stat().st_size)
            struct.pack_into('<I', header, 16, initrd.stat().st_size)
            struct.pack_into('<I', header, 36, 2048)
            boot.write_bytes(header + kernel.read_bytes().ljust(2048, b'\0') + initrd.read_bytes())
            self.assertTrue(recovery.verify_boot_image(boot, kernel, initrd)['kernel_matches_compiled'])
            data = bytearray(boot.read_bytes())
            data[2048] ^= 1
            boot.write_bytes(data)
            with self.assertRaises(AssertionError):
                recovery.verify_boot_image(boot, kernel, initrd)


class FlashGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        self.work = self.project / 'work'
        self.images = self.work / 'Linux_for_Tegra/tools/kernel_flash/images'
        self.images.mkdir(parents=True)
        (self.project / 'analysis').mkdir()
        data = b'fixture-payload'
        (self.images / 'payload.bin').write_bytes(data)
        self.manifest = {'board': 'recomputer-orin-j40mini', 'l4t': '39.2.1',
                         'module': {'boardid': '3767', 'sku': '0000',
                                    'nvme': {'bytes': 128 * 1024**3, 'sectors_512': 128 * 1024**3 // 512}},
                         'app_image_size_bytes': 64 * 1024**3,
                         'files': [{'path': 'payload.bin', 'bytes': len(data),
                                    'sha256': hashlib.sha256(data).hexdigest()}]}
        self.params = self.images.parent / 'initrdflashparam.txt'
        self.params.write_text('--no-flash recomputer-orin-j40mini external')
        self.blocks = [part.split('\nPY\n', 1)[0] for part in
                       (SCRIPTS / 'flash_seeed_j40mini.sh').read_text().split("<<'PY'\n")[1:]]

    def preflight(self):
        (self.project / 'analysis/seeed-j40mini-r39.2.1-flash-images.json').write_text(json.dumps(self.manifest))
        return subprocess.run([sys.executable, '-c', self.blocks[0], str(self.project), str(self.work),
                               '39.2.1', '-r39.2.1'], capture_output=True)

    def test_manifest_then_corrupt_payload(self):
        self.assertEqual(self.preflight().returncode, 0)
        (self.images / 'payload.bin').write_bytes(b'fixture-corrupt')
        self.assertNotEqual(self.preflight().returncode, 0)

    def test_reject_direct_flash_and_path_escape(self):
        self.params.write_text('--direct /dev/nvme0n1 recomputer-orin-j40mini external')
        self.assertNotEqual(self.preflight().returncode, 0)
        self.params.write_text('recomputer-orin-j40mini external')
        self.manifest['files'][0]['path'] = '../payload.bin'
        self.assertNotEqual(self.preflight().returncode, 0)

    def test_live_cid_must_match_and_measurement_cannot_omit_it(self):
        generation, flash = self.project / 'generation', self.project / 'flash'
        generation.mkdir()
        flash.mkdir()
        measurement = generation / 'measured.json'
        measurement.write_text(json.dumps({'ecid': '0x1234'}))
        probe = flash / 'bootrom-probe.log'
        def verify():
            return subprocess.run([sys.executable, '-c', self.blocks[1], str(self.project),
                                   str(generation), str(flash)], capture_output=True).returncode
        probe.write_text('BR_CID: 0x1234\n')
        self.assertEqual(verify(), 0)
        probe.write_text('BR_CID: 0x5678\n')
        self.assertNotEqual(verify(), 0)
        measurement.write_text('{}')
        self.assertNotEqual(verify(), 0)


class WorkspaceTests(unittest.TestCase):
    @unittest.skipIf(not hasattr(__import__('os'), 'geteuid') or __import__('os').geteuid() == 0,
                     'Preparation deliberately refuses root')
    def test_archive_preparation_and_existing_tree_guard(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / 'repository'
            script_dir = repo / 'tools/j40mini/workflow/scripts'
            script_dir.mkdir(parents=True)
            for name in ['prepare_workspace.sh', 'seeed_j40mini_env.sh']:
                shutil.copyfile(SCRIPTS / name, script_dir / name)
            (repo / 'recomputer-orin-j40mini.conf').write_text('# fixture\n')
            subprocess.run(['git', 'init', '-q', str(repo)], check=True)
            subprocess.run(['git', '-C', str(repo), 'add', 'recomputer-orin-j40mini.conf'], check=True)
            subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                            'commit', '-qm', 'fixture'], check=True)
            def archive(path, members):
                with tarfile.open(path, 'w:bz2') as out:
                    for name, data in members.items():
                        item = tarfile.TarInfo(name)
                        item.size = len(data)
                        out.addfile(item, io.BytesIO(data))
            bsp, sources, fs = [root / n for n in ['bsp.tbz2', 'sources.tbz2', 'rootfs.tbz2']]
            archive(bsp, {'Linux_for_Tegra/nv_tegra/nv_tegra_release': b'# R39 (release), REVISION: 2.1,\n'})
            public = {}
            names = ['kernel_src.tbz2', 'kernel_oot_modules_src.tbz2', 'kernel_nvgpu_src.tbz2',
                     'nvidia_kernel_display_driver_source.tbz2', 'nvidia_unified_gpu_display_driver_source.tbz2']
            for index, name in enumerate(names):
                inner = root / name
                location = ('nvidia-oot/drivers/net/ethernet/nvidia/nvethernet/fixture'
                            if index == 1 else 'fixture-' + str(index))
                archive(inner, {location: b'source'})
                data = inner.read_bytes()
                prefix = 'Linux_for_Tegra/source/' + name
                public[prefix] = data
                public[prefix + '.sha1sum'] = (hashlib.sha1(data).hexdigest() + '  ' + name).encode()
            archive(sources, public)
            archive(fs, {'etc/fixture': b'rootfs'})
            command = ['bash', str(script_dir / 'prepare_workspace.sh'), str(bsp), str(sources), str(fs)]
            first = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            prepared = script_dir.parent / 'work/seeed-j40mini-r39.2.1/Linux_for_Tegra'
            self.assertTrue((prepared / 'recomputer-orin-j40mini.conf').is_file())
            self.assertTrue((prepared / 'source/nvidia-oot/drivers/net/ethernet/nvidia/nvethernet/nvethernetrm').is_symlink())
            self.assertFalse((prepared / 'rootfs/etc/fixture').exists())
            self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)


if __name__ == '__main__':
    unittest.main()
