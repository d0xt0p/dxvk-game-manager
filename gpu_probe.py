"""Read-only Vulkan discovery in a disposable subprocess (no SDK dependency)."""
import ctypes as C
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

AUTO = 'Автовыбор игрой (без фильтра)'
MANUAL = 'Ввести имя вручную…'
TYPES = {0: 'другая', 1: 'встроенная', 2: 'дискретная', 3: 'виртуальная', 4: 'программная'}


class ApplicationInfo(C.Structure):
    _fields_ = [('sType', C.c_uint32), ('pNext', C.c_void_p),
                ('pApplicationName', C.c_char_p), ('applicationVersion', C.c_uint32),
                ('pEngineName', C.c_char_p), ('engineVersion', C.c_uint32), ('apiVersion', C.c_uint32)]


class InstanceInfo(C.Structure):
    _fields_ = [('sType', C.c_uint32), ('pNext', C.c_void_p), ('flags', C.c_uint32),
                ('pApplicationInfo', C.POINTER(ApplicationInfo)), ('enabledLayerCount', C.c_uint32),
                ('ppEnabledLayerNames', C.c_void_p), ('enabledExtensionCount', C.c_uint32),
                ('ppEnabledExtensionNames', C.c_void_p)]


class PropertiesPrefix(C.Structure):
    _fields_ = [('apiVersion', C.c_uint32), ('driverVersion', C.c_uint32),
                ('vendorID', C.c_uint32), ('deviceID', C.c_uint32),
                ('deviceType', C.c_uint32), ('deviceName', C.c_char * 256)]


class PropertiesStorage(C.Union):
    # VkPhysicalDeviceProperties has a fixed Vulkan 1.0 ABI. Overallocate its
    # trailing limits/sparseProperties while keeping native 8-byte alignment;
    # only the documented prefix above is interpreted here.
    _fields_ = [('prefix', PropertiesPrefix), ('storage', C.c_uint64 * 512)]


def version(value):
    return f'{(value >> 22) & 127}.{(value >> 12) & 1023}.{value & 4095}'


def enumerate_vulkan(loader=None):
    if loader is None:
        if os.name != 'nt':
            raise RuntimeError('Автоопределение GPU доступно в Windows.')
        # Do not load an unrelated DLL from the game or current directory.
        loader = C.WinDLL('vulkan-1.dll', winmode=0x800)
    create = loader.vkCreateInstance
    create.argtypes = [C.POINTER(InstanceInfo), C.c_void_p, C.POINTER(C.c_void_p)]
    create.restype = C.c_int32
    destroy = loader.vkDestroyInstance
    destroy.argtypes = [C.c_void_p, C.c_void_p]
    destroy.restype = None
    enum = loader.vkEnumeratePhysicalDevices
    enum.argtypes = [C.c_void_p, C.POINTER(C.c_uint32), C.POINTER(C.c_void_p)]
    enum.restype = C.c_int32
    props = loader.vkGetPhysicalDeviceProperties
    props.argtypes = [C.c_void_p, C.POINTER(PropertiesStorage)]
    props.restype = None
    app = ApplicationInfo(sType=0, pApplicationName=b'DXVK Game Manager', apiVersion=1 << 22)
    info = InstanceInfo(sType=1, pApplicationInfo=C.pointer(app))
    instance = C.c_void_p()
    result = create(C.byref(info), None, C.byref(instance))
    if result != 0:
        raise RuntimeError(f'Не удалось создать Vulkan instance (код {result}). Проверьте драйвер.')
    try:
        for _ in range(4):
            count = C.c_uint32()
            result = enum(instance, C.byref(count), None)
            if result not in (0, 5) or count.value > 128:
                raise RuntimeError(f'Ошибка перечисления Vulkan GPU (код {result}).')
            if not count.value:
                return []
            devices = (C.c_void_p * count.value)()
            result = enum(instance, C.byref(count), devices)
            if result == 5:  # VK_INCOMPLETE: device list changed; query again.
                continue
            if result != 0 or count.value > len(devices):
                raise RuntimeError(f'Ошибка чтения списка Vulkan GPU (код {result}).')
            found = []
            for handle in devices[:count.value]:
                storage = PropertiesStorage()
                props(handle, C.byref(storage))
                p = storage.prefix
                name = bytes(p.deviceName).decode('utf-8', errors='strict').strip()
                if not name or any(ord(c) < 32 for c in name):
                    raise RuntimeError('Драйвер вернул некорректное имя GPU.')
                found.append({'name': name, 'type': int(p.deviceType), 'api': version(p.apiVersion),
                              'vendor_id': int(p.vendorID), 'device_id': int(p.deviceID)})
            return found
        raise RuntimeError('Список Vulkan GPU меняется; повторите поиск.')
    finally:
        destroy(instance, None)


def scan():
    """A faulty/hanging third-party driver must not terminate the UI process."""
    command = ([sys.executable, '--probe-gpus'] if getattr(sys, 'frozen', False)
               else [sys.executable, str(Path(__file__).absolute()), '--probe-gpus'])
    try:
        # Windowed PyInstaller builds have sys.stdout=None even with a pipe.
        # A private result file works in both the source and frozen processes.
        with tempfile.TemporaryDirectory(prefix='dxvkgm-probe-') as folder:
            output = Path(folder) / 'result.json'
            result = subprocess.run(command + [str(output)], capture_output=True,
                                    timeout=15, creationflags=0x08000000 if os.name == 'nt' else 0)
            if result.returncode:
                raise RuntimeError('Процесс проверки Vulkan завершился с ошибкой. Проверьте драйвер или используйте ручной ввод.')
            if not output.is_file() or output.stat().st_size > 1024 * 1024:
                raise RuntimeError('Проверка Vulkan не вернула список устройств.')
            data = json.loads(output.read_text(encoding='utf-8'))
        if data.get('error'):
            raise RuntimeError(data['error'])
        if not isinstance(data.get('devices'), list):
            raise ValueError('devices')
        return data['devices']
    except subprocess.TimeoutExpired as e:
        raise RuntimeError('Драйвер не ответил за 15 секунд. Можно повторить поиск или ввести имя вручную.') from e
    except (json.JSONDecodeError, ValueError) as e:
        raise RuntimeError('Не удалось прочитать ответ проверки Vulkan.') from e


def choices(devices):
    """Labels contain metadata; ONLY the unmodified Vulkan name is a filter."""
    return [AUTO] + [f'{i + 1}. {d["name"]} · {TYPES.get(d["type"], "другая")} · Vulkan {d["api"]}'
                     for i, d in enumerate(devices)] + [MANUAL]


def selected_filter(label, devices, manual=''):
    labels = choices(devices)
    if label == AUTO:
        return ''
    if label == MANUAL:
        if not manual.strip():
            raise ValueError('Введите Vulkan-имя GPU или выберите автовыбор.')
        return manual.strip()
    if label not in labels:
        raise ValueError('Выберите видеокарту из списка.')
    name = devices[labels.index(label) - 1]['name']
    if sum(name in d['name'] for d in devices) != 1:
        raise ValueError('Этот фильтр совпадает с несколькими GPU. По имени различить их нельзя; используйте автовыбор. '
                         'Выбор по UUID в этой версии не реализован.')
    return name


def main(output=None):
    try:
        data = {'devices': enumerate_vulkan(), 'error': None}
    except Exception as e:
        data = {'devices': [], 'error': f'Vulkan: {e}'}
    payload = json.dumps(data, ensure_ascii=False)
    if output is not None:
        Path(output).write_text(payload, encoding='utf-8')
    else:
        sys.stdout.buffer.write(payload.encode('utf-8'))


if __name__ == '__main__':
    main(sys.argv[2] if len(sys.argv) == 3 and sys.argv[1] == '--probe-gpus' else None)
