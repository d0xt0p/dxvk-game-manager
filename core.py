"""Local DXVK deployment. No game execution, downloads, registry or driver changes."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import struct
import tarfile
import tempfile
import uuid
import zipfile

DLLS = {
    'D3D8': ('d3d8.dll', 'd3d9.dll'),
    'D3D9': ('d3d9.dll',),
    'D3D10': ('d3d10core.dll', 'd3d11.dll', 'dxgi.dll'),
    'D3D11': ('d3d11.dll', 'dxgi.dll'),
}
NAMES = {n for group in DLLS.values() for n in group} | {'dxvk.conf'}
STATE = '.dxvk-game-manager'
LIMIT = 128 * 1024 * 1024


class ManagerError(Exception):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


def plain(path):
    """Reject redirects rather than following them during deployment/recovery."""
    path = Path(path).absolute()
    for part in (path, *path.parents):
        try:
            s = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(s.st_mode) or getattr(s, 'st_file_attributes', 0) & 0x400:
            raise ManagerError(f'Ссылки и junction не поддерживаются: {part}')
    return path


def read_file(path, limit=LIMIT):
    path = plain(path)
    if not path.is_file() or path.stat().st_size > limit:
        raise ManagerError(f'Ожидался обычный файл размером до {limit // 1024 // 1024} МБ: {path}')
    with path.open('rb') as f:
        data = f.read(limit + 1)
    if len(data) > limit:
        raise ManagerError('Файл превышает допустимый размер.')
    return data


def file_hash(path):
    return digest(read_file(path)) if plain(path).exists() else None


def atomic_write(path, data):
    path = plain(path)
    fd, tmp = tempfile.mkstemp(prefix='.dxvkgm-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


class PE:
    """Bounds-checked PE reader: architecture, ordinary and delay-load imports."""
    def __init__(self, data):
        self.data = data
        try:
            if data[:2] != b'MZ':
                raise ValueError('MZ')
            off = self.u32(0x3c)
            if data[off:off + 4] != b'PE\0\0':
                raise ValueError('PE')
            machine, count = struct.unpack_from('<HH', data, off + 4)
            self.arch = {0x14c: 'x32', 0x8664: 'x64'}.get(machine)
            if not self.arch:
                raise ManagerError(f'Не поддерживается PE Machine 0x{machine:04x}; нужны x86 или x64.')
            opt_size, self.flags = struct.unpack_from('<HH', data, off + 20)
            opt = off + 24
            magic = self.u16(opt)
            if magic != (0x10b if self.arch == 'x32' else 0x20b):
                raise ValueError('PE optional header')
            dd = 96 if self.arch == 'x32' else 112
            if opt_size < dd or opt + opt_size > len(data) or not 0 < count <= 96:
                raise ValueError('PE header size')
            self.base = (self.u32(opt + 28) if self.arch == 'x32'
                         else struct.unpack_from('<Q', data, opt + 24)[0])
            self.headers = self.u32(opt + 60)
            directories = min(self.u32(opt + dd - 4), (opt_size - dd) // 8)
            self.dirs = [struct.unpack_from('<II', data, opt + dd + i * 8)
                         for i in range(directories)]
            self.sections = []
            for i in range(count):
                p = opt + opt_size + i * 40
                if p + 40 > len(data):
                    raise ValueError('section table')
                vs, va, size, raw = struct.unpack_from('<IIII', data, p + 8)
                self.sections.append((va, size, raw))
        except (struct.error, ValueError, IndexError) as e:
            raise ManagerError('Повреждённый или неполный PE-файл.') from e

    def u16(self, p):
        return struct.unpack_from('<H', self.data, p)[0]

    def u32(self, p):
        return struct.unpack_from('<I', self.data, p)[0]

    def offset(self, rva, size=1):
        if 0 <= rva < self.headers and rva + size <= min(self.headers, len(self.data)):
            return rva
        for va, raw_size, raw in self.sections:
            if va <= rva and rva + size <= va + raw_size:
                p = raw + rva - va
                if p + size <= len(self.data):
                    return p
        raise ManagerError('Некорректный RVA в таблице PE.')

    def imports(self):
        found = set()
        try:
            for idx, width, field in ((1, 20, 12), (13, 32, 4)):
                if idx >= len(self.dirs):
                    continue
                rva, size = self.dirs[idx]
                if not rva:
                    continue
                if size < width:
                    raise ManagerError('Неполная таблица импортов.')
                terminated = False
                for i in range(min(size // width, 4096)):
                    p = self.offset(rva + i * width, width)
                    if not any(self.data[p:p + width]):
                        terminated = True
                        break
                    name = self.u32(p + field)
                    if idx == 13 and not self.u32(p) & 1:
                        name -= self.base
                    n = self.offset(name)
                    end = self.data.find(b'\0', n, n + 260)
                    if end < 0:
                        raise ManagerError('Некорректное имя импортируемой DLL.')
                    self.offset(name, end - n + 1)
                    found.add(self.data[n:end].decode('ascii').lower())
                if not terminated:
                    raise ManagerError('Таблица импортов не завершена.')
        except (struct.error, UnicodeError) as e:
            raise ManagerError('Не удалось прочитать импорты PE.') from e
        return sorted(found)


def inspect_exe(path):
    path = plain(path)
    pe = PE(read_file(path, 1024 * 1024 * 1024))
    if pe.flags & 0x2000 or path.suffix.lower() != '.exe':
        raise ManagerError('Выберите EXE игры, а не DLL.')
    warning = ''
    try:
        imports = pe.imports()
    except ManagerError as e:
        imports, warning = [], str(e)
    hints = [api for api, names in {
        'D3D8': ['d3d8.dll'], 'D3D9': ['d3d9.dll'],
        'D3D10': ['d3d10.dll', 'd3d10_1.dll', 'd3d10core.dll'],
        'D3D11': ['d3d11.dll'], 'D3D12': ['d3d12.dll'],
        'Vulkan': ['vulkan-1.dll'], 'OpenGL': ['opengl32.dll']
    }.items() if set(names) & set(imports)]
    return {'arch': pe.arch, 'hints': hints, 'imports': imports, 'warning': warning}


def api_analysis(exe):
    """Bounded static hints from the EXE and imported local engine DLLs.

    Never load/execute a module. Never infer an active runtime renderer.
    Skip replaceable graphics wrappers so an existing DXVK/mod installation
    does not become circular evidence for which API the game uses.
    """
    exe = plain(exe)
    result = inspect_exe(exe)
    mapping = {'d3d8.dll': 'D3D8', 'd3d9.dll': 'D3D9', 'd3d10.dll': 'D3D10',
               'd3d10_1.dll': 'D3D10', 'd3d10core.dll': 'D3D10', 'd3d11.dll': 'D3D11',
               'd3d12.dll': 'D3D12', 'vulkan-1.dll': 'Vulkan', 'opengl32.dll': 'OpenGL'}
    pending = [(exe, 0)]
    visited, evidence, notes = set(), [], []
    budget = 256 * 1024 * 1024
    while pending and len(visited) < 24:
        path, depth = pending.pop(0)
        if str(path).lower() in visited:
            continue
        visited.add(str(path).lower())
        try:
            size = plain(path).stat().st_size
            if size > min(LIMIT, budget):
                notes.append(f'{path.name}: пропущен по ограничению размера.')
                continue
            budget -= size
            image = PE(read_file(path))
            if image.arch != result['arch']:
                continue
            imports = image.imports()
        except (OSError, ManagerError) as e:
            notes.append(f'{path.name}: {e}')
            continue
        for name in imports:
            if name in mapping:
                evidence.append({'api': mapping[name], 'file': path.name, 'import': name, 'depth': depth})
            if depth >= 2 or name in NAMES or name in mapping or '/' in name or '\\' in name or ':' in name:
                continue
            local = exe.parent / name
            if local.suffix.lower() == '.dll' and local.is_file():
                pending.append((local, depth + 1))
    if pending:
        notes.append('Достигнут предел анализа: 24 файла или глубина 2.')
    hints = sorted({e['api'] for e in evidence})
    nearest = min((e['depth'] for e in evidence), default=None)
    closest = {e['api'] for e in evidence if e['depth'] == nearest}
    supported = [h for h in closest if h in DLLS]
    result.update(hints=hints, evidence=evidence, notes=notes,
                  suggested=supported[0] if len(closest) == 1 and len(supported) == 1 else None,
                  scanned=len(visited))
    return result


def package_files(source, arch, api):
    """Read selected members only; never extract an untrusted archive to disk."""
    source = plain(source)
    wanted = DLLS.get(api)
    if not wanted:
        raise ManagerError('Выберите D3D8, D3D9, D3D10 или D3D11. DX12 не поддерживается.')
    result = {}
    if source.is_dir():
        folder = source if source.name.lower() == arch else source / arch
        result = {name: read_file(folder / name) for name in wanted}
    else:
        if source.stat().st_size > 512 * 1024 * 1024:
            raise ManagerError('Архив слишком большой (максимум 512 МБ).')

        def accept(name):
            parts = PurePosixPath(name.replace('\\', '/')).parts
            if '..' in parts or PurePosixPath(name).is_absolute() or any(':' in p for p in parts):
                raise ManagerError('Архив содержит небезопасный путь.')
            return parts[-1].lower() if len(parts) >= 2 and parts[-2].lower() == arch else None

        if zipfile.is_zipfile(source):
            with zipfile.ZipFile(source) as z:
                for member in z.infolist():
                    name = accept(member.filename)
                    if name not in wanted:
                        continue
                    if name in result or member.file_size > LIMIT or stat.S_ISLNK(member.external_attr >> 16):
                        raise ManagerError('Дубликат, ссылка или слишком большая DLL в архиве.')
                    result[name] = z.read(member)
        else:
            with tarfile.open(source, 'r:*') as t:
                for i, member in enumerate(t):
                    if i > 10000:
                        raise ManagerError('Слишком много записей в архиве.')
                    name = accept(member.name)
                    if name not in wanted:
                        continue
                    if name in result or not member.isfile() or member.size > LIMIT:
                        raise ManagerError('Дубликат, ссылка или слишком большая DLL в архиве.')
                    with t.extractfile(member) as f:
                        result[name] = f.read(LIMIT + 1)
    if set(result) != set(wanted):
        raise ManagerError(f'В пакете нет всех DLL для {arch}/{api}: {", ".join(wanted)}')
    for name, data in result.items():
        pe = PE(data)
        if pe.arch != arch or not pe.flags & 0x2000:
            raise ManagerError(f'{name}: неверная архитектура или файл не является DLL.')
    return result


def config(gpu, hud=True):
    gpu = gpu.strip()
    if any(c in gpu for c in '\r\n"\\\0') or any(ord(c) < 32 for c in gpu):
        raise ManagerError('Имя GPU содержит недопустимые символы.')
    return ('# DXVK Game Manager — local game configuration\n'
            f'dxvk.deviceFilter = "{gpu}"\n'
            f'dxvk.hud = {"devinfo,fps" if hud else "0"}\n').encode('utf-8')


def game_folder(exe):
    exe = plain(exe)
    folder = exe.parent
    windows = Path(os.environ.get('WINDIR', 'C:/Windows')).absolute()
    if folder == windows or windows in folder.parents:
        raise ManagerError('Установка в каталог Windows запрещена.')
    return folder


@contextmanager
def locked(folder):
    state = plain(folder / STATE)
    state.mkdir(exist_ok=True)
    lock = plain(state / 'operation.lock')
    with lock.open('a+b') as f:
        f.seek(0, 2)
        if not f.tell():
            f.write(b'0')
            f.flush()
        f.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            raise ManagerError('Эта папка уже изменяется другим экземпляром приложения.') from e
        try:
            yield state
        finally:
            f.seek(0)
            if os.name == 'nt':
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def save_manifest(folder, m):
    atomic_write(folder / STATE / 'active.json', json.dumps(m, ensure_ascii=False, indent=2).encode('utf-8'))


def manifest(folder):
    path = plain(folder / STATE / 'active.json')
    if not path.exists():
        return None
    try:
        m = json.loads(read_file(path, 1024 * 1024))
        if (m['schema'] != 1 or m['status'] not in ('installing', 'installed', 'restoring', 'restored')
                or not isinstance(m['id'], str) or len(m['id']) != 32
                or any(c not in '0123456789abcdef' for c in m['id'])
                or not m['files'] or not set(m['files']) <= NAMES):
            raise ValueError('schema')
        for info in m['files'].values():
            for key in ('original', 'installed'):
                h = info[key]
                if h is None and key == 'original':
                    continue
                if not isinstance(h, str) or len(h) != 64 or any(c not in '0123456789abcdef' for c in h):
                    raise ValueError('hash')
        return m
    except (ValueError, KeyError, TypeError) as e:
        raise ManagerError('Журнал установки повреждён. Сохраните резервные копии; автоматические изменения остановлены.') from e


def plan(exe, source, api, gpu, hud=True):
    folder = game_folder(exe)
    info = inspect_exe(exe)
    payload = package_files(source, info['arch'], api)
    payload['dxvk.conf'] = config(gpu, hud)
    existing = {name: file_hash(folder / name) for name in payload}
    return {'folder': str(folder), 'exe': Path(exe).name, 'arch': info['arch'],
            'api': api, 'source': str(Path(source).absolute()), 'gpu': gpu.strip(),
            'payload': payload, 'existing': existing,
            'exe_hash': file_hash(exe)}


def install(p):
    folder = plain(p['folder'])
    with locked(folder) as state:
        previous = manifest(folder)
        if previous and previous['status'] != 'restored':
            raise ManagerError('В папке уже есть управляемая установка. Сначала выполните откат.')
        if file_hash(folder / p['exe']) != p['exe_hash']:
            raise ManagerError('EXE изменился после проверки. Повторите подготовку.')
        for name, old in p['existing'].items():
            if file_hash(folder / name) != old:
                raise ManagerError(f'{name} изменился после проверки. Повторите подготовку.')
        ident = uuid.uuid4().hex
        backup = plain(state / ident)
        backup.mkdir()
        m = {k: p[k] for k in ('exe', 'arch', 'api', 'source', 'gpu')}
        m.update(schema=1, id=ident, status='installing',
                 created=datetime.now(timezone.utc).isoformat(), files={})
        for name, data in p['payload'].items():
            old = p['existing'][name]
            if old is not None:
                original = read_file(folder / name)
                if digest(original) != old:
                    raise ManagerError('Исходный файл изменился при резервном копировании.')
                atomic_write(backup / name, original)
            m['files'][name] = {'original': old, 'installed': digest(data)}
        # Persist the complete recovery plan BEFORE touching game files.
        atomic_write(backup / 'manifest.json', json.dumps(m, ensure_ascii=False, indent=2).encode('utf-8'))
        save_manifest(folder, m)
        try:
            for name, data in p['payload'].items():
                if file_hash(folder / name) != p['existing'][name]:
                    raise ManagerError(f'{name} изменился во время установки.')
                atomic_write(folder / name, data)
            m['status'] = 'installed'
            save_manifest(folder, m)
        except Exception as e:
            raise ManagerError(f'Установка прервана: {e}\nЗакройте игру и выполните «Удалить DXVK / откат». Журнал и копии сохранены.') from e
        return m


def restore(exe):
    folder = game_folder(exe)
    with locked(folder):
        m = manifest(folder)
        if not m or m['status'] == 'restored':
            raise ManagerError('Нет активной установки, созданной этим приложением.')
        backup = plain(folder / STATE / m['id'])
        originals = {}
        # Check ALL conflicts and backup hashes before restoring ANY file.
        for name, info in m['files'].items():
            current = file_hash(folder / name)
            if current not in (info['installed'], info['original'], None):
                raise ManagerError(f'{name} изменён после установки. Откат остановлен без перезаписи файлов. '
                                   f'Сохраните изменённый файл отдельно и уберите его из папки игры, затем повторите откат. Копии: {backup}')
            if info['original'] is not None:
                data = read_file(backup / name)
                if digest(data) != info['original']:
                    raise ManagerError(f'Повреждена резервная копия {name}. Откат остановлен.')
                originals[name] = data
        m['status'] = 'restoring'
        save_manifest(folder, m)
        for name, info in m['files'].items():
            target = plain(folder / name)
            if file_hash(target) not in (info['installed'], info['original'], None):
                raise ManagerError(f'{name} изменился во время отката. Операция остановлена.')
            if name in originals:
                atomic_write(target, originals[name])
            elif target.exists():
                target.unlink()
        m['status'] = 'restored'
        save_manifest(folder, m)
        return m


def status(exe):
    folder = game_folder(exe)
    m = manifest(folder)
    if not m:
        present = [n for n in sorted(NAMES) if plain(folder / n).exists()]
        return 'Нет управляемой установки.' + (f' Уже присутствуют: {", ".join(present)}. Происхождение неизвестно.' if present else '')
    label = {'installed': 'DXVK установлен', 'installing': 'Установка прервана — нужен откат',
             'restoring': 'Откат прерван — повторите откат', 'restored': 'Исходные файлы восстановлены'}[m['status']]
    changed = [n for n, i in m['files'].items() if file_hash(folder / n) != i['installed']]
    return f'{label}. {m["api"]}, {m["arch"]}, EXE: {m["exe"]}.\nРезервные копии: {folder / STATE / m["id"]}' + (
        f'\nОтличаются от установленного набора: {", ".join(changed)}' if changed and m['status'] != 'restored' else '')


def logs(exe, directory=None):
    folder = plain(directory) if directory else game_folder(exe)
    stem = Path(exe).stem
    m = manifest(game_folder(exe))
    installed = datetime.fromisoformat(m['created']).timestamp() if m and m['status'] != 'restored' else None
    reports = []
    for api in ('d3d8', 'd3d9', 'd3d10', 'd3d11', 'dxgi'):
        path = plain(folder / f'{stem}_{api}.log')
        if not path.exists():
            continue
        modified = path.stat().st_mtime
        with path.open('rb') as f:
            f.seek(max(0, path.stat().st_size - 128 * 1024))
            raw = f.read(128 * 1024).decode('utf-8', errors='replace')
        lines = raw.splitlines()
        evidence = [line for line in lines if any(x in line.lower() for x in
                    ('dxvk:', 'device name:', 'device:', 'adapter', 'found config', 'effective configuration',
                     'devicefilter', 'nvidia', 'intel', 'err:', 'error', 'failed', 'vulkan:'))]
        age = 'СТАРЫЙ: до установки' if installed and modified < installed else 'Сверьте время с последним запуском'
        reports.append(f'=== {path.name} | {datetime.fromtimestamp(modified).isoformat(sep=" ", timespec="seconds")} | {age} ===\n'
                       + '\n'.join(evidence[-80:]) + '\n\nПоследние строки:\n' + '\n'.join(lines[-35:]))
    return ('\n\n'.join(reports) if reports else 'Логи для выбранного EXE не найдены. Проверьте рабочую папку игры и DXVK_LOG_PATH.') + (
        '\n\nИмя GPU в перечислении адаптеров не доказывает выбор устройства. Сверьте выбранный Device и HUD в самой игре. '
        'Наличие DLL не доказывает, что игра загрузила DXVK.')
