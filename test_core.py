import io
import json
from pathlib import Path
import struct
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import core


def pe(arch='x64', dll=False, imports=(), delay=()):
    data = bytearray(2048)
    data[:2] = b'MZ'
    struct.pack_into('<I', data, 0x3c, 0x80)
    data[0x80:0x84] = b'PE\0\0'
    opt_size = 240 if arch == 'x64' else 224
    struct.pack_into('<HH', data, 0x84, 0x8664 if arch == 'x64' else 0x14c, 1)
    struct.pack_into('<HH', data, 0x94, opt_size, 0x2002 if dll else 2)
    opt = 0x98
    struct.pack_into('<H', data, opt, 0x20b if arch == 'x64' else 0x10b)
    struct.pack_into('<I', data, opt + 60, 512)
    dd = opt + (112 if arch == 'x64' else 96)
    struct.pack_into('<I', data, dd - 4, 16)
    sec = opt + opt_size
    data[sec:sec + 5] = b'.text'
    struct.pack_into('<IIII', data, sec + 8, 1536, 0x1000, 1536, 512)
    namepos = 1500
    for idx, names, pos, width, field in ((1, imports, 512, 20, 12), (13, delay, 900, 32, 4)):
        if not names:
            continue
        struct.pack_into('<II', data, dd + idx * 8, pos - 512 + 0x1000, (len(names) + 1) * width)
        for i, name in enumerate(names):
            if idx == 13:
                struct.pack_into('<I', data, pos + i * width, 1)
            struct.pack_into('<I', data, pos + i * width + field, namepos - 512 + 0x1000)
            raw = name.encode() + b'\0'
            data[namepos:namepos + len(raw)] = raw
            namepos += len(raw)
    return bytes(data)


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='test-run-', dir=Path(__file__).parent)
        self.root = Path(self.tmp.name)
        self.game = self.root / 'Игра с пробелами'
        self.game.mkdir()
        self.exe = self.game / 'Watch_Dogs.exe'
        self.exe.write_bytes(pe(imports=('d3d11.dll', 'dxgi.dll')))
        self.pkg = self.root / 'dxvk-test'
        for arch in ('x32', 'x64'):
            (self.pkg / arch).mkdir(parents=True)
            for name in core.NAMES - {'dxvk.conf'}:
                (self.pkg / arch / name).write_bytes(pe(arch, True) + name.encode())

    def tearDown(self):
        self.assertTrue(self.root.resolve().is_relative_to(Path(__file__).parent.resolve()))
        self.tmp.cleanup()

    def plan(self, api='D3D11'):
        return core.plan(self.exe, self.pkg, api, 'NVIDIA RTX A2000')

    def test_arch_and_imports_and_delay_imports(self):
        for arch in ('x32', 'x64'):
            p = core.PE(pe(arch, imports=('dxgi.dll',), delay=('d3d11.dll',)))
            self.assertEqual(p.arch, arch)
            self.assertEqual(p.imports(), ['d3d11.dll', 'dxgi.dll'])
        self.assertEqual(core.inspect_exe(self.exe)['hints'], ['D3D11'])

    def test_invalid_arch_and_truncated_pe(self):
        for raw in (b'hello', pe()[:100]):
            with self.assertRaises(core.ManagerError):
                core.PE(raw)
        raw = bytearray(pe())
        struct.pack_into('<H', raw, 0x84, 0xaa64)
        with self.assertRaises(core.ManagerError):
            core.PE(raw)

    def test_every_api_installs_exact_dependencies_and_restores(self):
        for api, names in core.DLLS.items():
            with self.subTest(api=api):
                core.install(self.plan(api))
                self.assertEqual({p.name for p in self.game.glob('*.dll')}, set(names))
                self.assertIn('NVIDIA RTX A2000', (self.game / 'dxvk.conf').read_text('utf-8'))
                core.restore(self.exe)
                self.assertFalse(list(self.game.glob('*.dll')))
                self.assertFalse((self.game / 'dxvk.conf').exists())

    def test_originals_restored_byte_for_byte(self):
        originals = {'d3d11.dll': b'original mod', 'dxvk.conf': b'\xff\xfeconfig\0'}
        for name, data in originals.items():
            (self.game / name).write_bytes(data)
        m = core.install(self.plan())
        core.restore(self.exe)
        for name, data in originals.items():
            self.assertEqual((self.game / name).read_bytes(), data)
            self.assertEqual((self.game / core.STATE / m['id'] / name).read_bytes(), data)
        self.assertFalse((self.game / 'dxgi.dll').exists())

    def test_reinstall_blocked(self):
        core.install(self.plan())
        with self.assertRaises(core.ManagerError):
            core.install(self.plan())

    def test_external_change_blocks_all_restore_writes(self):
        (self.game / 'd3d11.dll').write_bytes(b'original')
        core.install(self.plan())
        installed = (self.game / 'd3d11.dll').read_bytes()
        (self.game / 'dxvk.conf').write_bytes(b'user edit')
        with self.assertRaises(core.ManagerError):
            core.restore(self.exe)
        self.assertEqual((self.game / 'd3d11.dll').read_bytes(), installed)
        self.assertEqual((self.game / 'dxvk.conf').read_bytes(), b'user edit')

    def test_changed_plan_does_not_overwrite(self):
        p = self.plan()
        (self.game / 'dxgi.dll').write_bytes(b'new mod')
        with self.assertRaises(core.ManagerError):
            core.install(p)
        self.assertEqual((self.game / 'dxgi.dll').read_bytes(), b'new mod')

    def test_corrupt_backup_blocks_all_restore_writes(self):
        (self.game / 'dxvk.conf').write_bytes(b'old')
        m = core.install(self.plan())
        (self.game / core.STATE / m['id'] / 'dxvk.conf').write_bytes(b'corrupt')
        with self.assertRaises(core.ManagerError):
            core.restore(self.exe)
        self.assertTrue((self.game / 'd3d11.dll').exists())

    def test_interrupted_install_can_be_recovered(self):
        (self.game / 'd3d11.dll').write_bytes(b'old dll')
        original_write = core.atomic_write
        def fail(path, data):
            if Path(path) == self.game / 'dxgi.dll':
                raise PermissionError('file locked')
            return original_write(path, data)
        with patch.object(core, 'atomic_write', side_effect=fail):
            with self.assertRaises(core.ManagerError):
                core.install(self.plan())
        self.assertEqual(core.manifest(self.game)['status'], 'installing')
        core.restore(self.exe)
        self.assertEqual((self.game / 'd3d11.dll').read_bytes(), b'old dll')
        self.assertFalse((self.game / 'dxgi.dll').exists())

    def test_interrupted_restore_can_be_repeated(self):
        (self.game / 'd3d11.dll').write_bytes(b'old dll')
        (self.game / 'dxgi.dll').write_bytes(b'old dxgi')
        core.install(self.plan())
        original_write = core.atomic_write
        def fail(path, data):
            if Path(path) == self.game / 'dxgi.dll':
                raise PermissionError('locked')
            return original_write(path, data)
        with patch.object(core, 'atomic_write', side_effect=fail):
            with self.assertRaises(PermissionError):
                core.restore(self.exe)
        core.restore(self.exe)
        self.assertEqual((self.game / 'dxgi.dll').read_bytes(), b'old dxgi')

    def test_tar_and_zip_and_arch_validation(self):
        for kind in ('tar.gz', 'zip'):
            path = self.root / ('dxvk.' + kind)
            if kind == 'zip':
                with zipfile.ZipFile(path, 'w') as z:
                    for name in core.DLLS['D3D11']:
                        z.writestr('dxvk/x64/' + name, pe(dll=True))
            else:
                with tarfile.open(path, 'w:gz') as t:
                    for name in core.DLLS['D3D11']:
                        data = pe(dll=True)
                        entry = tarfile.TarInfo('dxvk/x64/' + name)
                        entry.size = len(data)
                        t.addfile(entry, io.BytesIO(data))
            self.assertEqual(set(core.package_files(path, 'x64', 'D3D11')), set(core.DLLS['D3D11']))
        (self.pkg / 'x64' / 'dxgi.dll').write_bytes(pe('x32', True))
        with self.assertRaises(core.ManagerError):
            self.plan()

    def test_malicious_archive_and_manifest_paths_rejected(self):
        path = self.root / 'bad.tar.gz'
        with tarfile.open(path, 'w:gz') as t:
            data = pe(dll=True)
            entry = tarfile.TarInfo('../x64/d3d11.dll')
            entry.size = len(data)
            t.addfile(entry, io.BytesIO(data))
        with self.assertRaises(core.ManagerError):
            core.package_files(path, 'x64', 'D3D11')
        m = core.install(self.plan())
        m['files']['../../other'] = next(iter(m['files'].values()))
        core.save_manifest(self.game, m)
        with self.assertRaises(core.ManagerError):
            core.restore(self.exe)

    def test_lock_excludes_second_writer(self):
        with core.locked(self.game):
            with self.assertRaises(core.ManagerError):
                with core.locked(self.game):
                    self.fail('second writer admitted')

    def test_config_and_dx12_rejected(self):
        for gpu in ('NVIDIA\nother = 1', 'GPU"', 'GPU\\'):
            with self.assertRaises(core.ManagerError):
                core.config(gpu)
        with self.assertRaises(core.ManagerError):
            self.plan('D3D12')

    def test_unknown_install_not_removed(self):
        (self.game / 'd3d11.dll').write_bytes(b'manual')
        with self.assertRaises(core.ManagerError):
            core.restore(self.exe)
        self.assertEqual((self.game / 'd3d11.dll').read_bytes(), b'manual')

    def test_logs_do_not_claim_gpu_selected(self):
        p = self.game / 'Watch_Dogs_d3d11.log'
        p.write_text('info: DXVK: v3.1.1\ninfo: NVIDIA RTX A2000\nerr: failed Vulkan\n', encoding='utf-8')
        report = core.logs(self.exe)
        self.assertIn('failed Vulkan', report)
        self.assertIn('не доказывает выбор', report)
        core.install(self.plan())
        import os
        os.utime(p, (1, 1))
        self.assertIn('СТАРЫЙ', core.logs(self.exe))


if __name__ == '__main__':
    unittest.main(verbosity=2)
