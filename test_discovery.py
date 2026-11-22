import ctypes as C
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

import core
import gpu_probe as gpu
import test_core as fixtures
pe = fixtures.pe


class ApiAnalysisTests(unittest.TestCase):
    setUp = fixtures.DeploymentTests.setUp
    tearDown = fixtures.DeploymentTests.tearDown
    def test_engine_imports_can_propose_api(self):
        self.exe.write_bytes(pe(imports=('engine.dll',)))
        (self.game / 'engine.dll').write_bytes(pe(dll=True, delay=('d3d11.dll',)))
        info = core.api_analysis(self.exe)
        self.assertEqual(info['suggested'], 'D3D11')
        self.assertEqual(info['evidence'][0]['file'], 'engine.dll')

    def test_multiple_renderers_require_manual_choice(self):
        self.exe.write_bytes(pe(imports=('engine.dll',)))
        (self.game / 'engine.dll').write_bytes(pe(dll=True, imports=('d3d11.dll', 'd3d12.dll')))
        info = core.api_analysis(self.exe)
        self.assertEqual(info['hints'], ['D3D11', 'D3D12'])
        self.assertIsNone(info['suggested'])

    def test_engine_import_precedes_auxiliary_library(self):
        self.exe.write_bytes(pe(imports=('engine.dll',)))
        (self.game / 'engine.dll').write_bytes(pe(dll=True, imports=('d3d11.dll', 'helper.dll')))
        (self.game / 'helper.dll').write_bytes(pe(dll=True, imports=('opengl32.dll',)))
        info = core.api_analysis(self.exe)
        self.assertEqual(info['suggested'], 'D3D11')
        self.assertEqual(info['hints'], ['D3D11', 'OpenGL'])

    def test_wrappers_not_used_as_api_evidence(self):
        self.exe.write_bytes(pe(imports=('dxgi.dll',)))
        (self.game / 'dxgi.dll').write_bytes(pe(dll=True, imports=('d3d11.dll',)))
        self.assertIsNone(core.api_analysis(self.exe)['suggested'])

    def test_import_cycle_and_unknown_api_stop(self):
        self.exe.write_bytes(pe(imports=('engine.dll',)))
        (self.game / 'engine.dll').write_bytes(pe(dll=True, imports=('other.dll',)))
        (self.game / 'other.dll').write_bytes(pe(dll=True, imports=('engine.dll',)))
        info = core.api_analysis(self.exe)
        self.assertEqual(info['scanned'], 3)
        self.assertIsNone(info['suggested'])


class FakeFn:
    def __init__(self, fn):
        self.fn = fn

    def __call__(self, *args):
        return self.fn(*args)


class FakeLoader:
    def __init__(self, enum_error=False):
        self.destroyed = False
        self.vkCreateInstance = FakeFn(self.create)
        self.vkDestroyInstance = FakeFn(self.destroy)
        self.vkEnumeratePhysicalDevices = FakeFn(self.enum)
        self.vkGetPhysicalDeviceProperties = FakeFn(self.props)
        self.enum_error = enum_error

    def create(self, info, alloc, instance):
        C.cast(instance, C.POINTER(C.c_void_p))[0] = 123
        return 0

    def destroy(self, *args):
        self.destroyed = True

    def enum(self, instance, count, devices):
        if self.enum_error:
            return -3
        C.cast(count, C.POINTER(C.c_uint32))[0] = 1
        if devices is not None:
            devices[0] = 456
        return 0

    def props(self, handle, storage):
        p = C.cast(storage, C.POINTER(gpu.PropertiesStorage)).contents.prefix
        p.apiVersion = (1 << 22) | (4 << 12) | 123
        p.vendorID = 0x10de
        p.deviceType = 2
        p.deviceName = b'NVIDIA Test GPU'


class GpuTests(unittest.TestCase):
    def test_properties_decode_and_instance_cleanup(self):
        loader = FakeLoader()
        devices = gpu.enumerate_vulkan(loader)
        self.assertEqual(devices[0]['name'], 'NVIDIA Test GPU')
        self.assertEqual(devices[0]['api'], '1.4.123')
        self.assertTrue(loader.destroyed)

    def test_instance_destroyed_on_enumeration_failure(self):
        loader = FakeLoader(enum_error=True)
        with self.assertRaises(RuntimeError):
            gpu.enumerate_vulkan(loader)
        self.assertTrue(loader.destroyed)

    def test_filter_contains_no_ui_metadata(self):
        devices = [{'name': 'NVIDIA RTX A2000 Laptop GPU', 'type': 2, 'api': '1.4.0'}]
        labels = gpu.choices(devices)
        self.assertEqual(gpu.selected_filter(labels[1], devices), devices[0]['name'])
        self.assertEqual(gpu.selected_filter(gpu.AUTO, devices), '')
        self.assertEqual(gpu.selected_filter(gpu.MANUAL, devices, ' AMD Test '), 'AMD Test')

    def test_ambiguous_and_stale_gpu_choice_rejected(self):
        for names in (['GPU', 'GPU'], ['GPU', 'GPU Plus']):
            devices = [{'name': n, 'type': 2, 'api': '1.4.0'} for n in names]
            with self.assertRaises(ValueError):
                gpu.selected_filter(gpu.choices(devices)[1], devices)
        with self.assertRaises(ValueError):
            gpu.selected_filter('old device', [])

    def test_probe_timeout_does_not_hang_ui(self):
        with patch.object(gpu.subprocess, 'run', side_effect=subprocess.TimeoutExpired('probe', 15)):
            with self.assertRaisesRegex(RuntimeError, '15'):
                gpu.scan()

    def test_probe_failure_is_readable(self):
        def fail(command, **kwargs):
            Path(command[-1]).write_text('{"error":"no Vulkan"}', encoding='utf-8')
            return subprocess.CompletedProcess(command, 0)
        with patch.object(gpu.subprocess, 'run', side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, 'no Vulkan'):
                gpu.scan()


if __name__ == '__main__':
    unittest.main(verbosity=2)
