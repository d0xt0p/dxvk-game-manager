"""Read a real game installation; exercise deployment ONLY in a new local fixture."""
from pathlib import Path
import json
import sys
import uuid
import core


def check(game_exe):
    source_exe = core.plain(game_exe)
    source = source_exe.parent
    fixture = Path(__file__).parent / ('integration-run-' + uuid.uuid4().hex)
    fixture.mkdir()
    target = fixture / 'game'
    package = fixture / 'package' / 'x64'
    target.mkdir()
    package.mkdir(parents=True)
    # No execution, no writes in the source game folder.
    original_hashes = {name: core.file_hash(source / name) for name in
                       (source_exe.name, 'd3d11.dll', 'dxgi.dll', 'dxvk.conf')}
    exe = target / source_exe.name
    exe.write_bytes(core.read_file(source_exe))
    for name in core.DLLS['D3D11']:
        (package / name).write_bytes(core.read_file(source / name))
    initial = {'d3d11.dll': b'original test modification', 'dxvk.conf': b'original test settings'}
    for name, data in initial.items():
        (target / name).write_bytes(data)
    p = core.plan(exe, package.parent, 'D3D11', 'NVIDIA RTX A2000')
    core.install(p)
    for name in core.DLLS['D3D11']:
        assert core.file_hash(target / name) == original_hashes[name]
    core.restore(exe)
    for name, data in initial.items():
        assert (target / name).read_bytes() == data
    assert not (target / 'dxgi.dll').exists()
    assert original_hashes == {name: core.file_hash(source / name) for name in original_hashes}
    report = {
        'result': 'PASS', 'fixture': str(fixture),
        'real_exe': core.inspect_exe(source_exe),
        'checks': ['Real x64 DXVK DLL deployment into isolated fixture',
                   'Byte-for-byte restoration of pre-existing fixture files',
                   'Removal of newly added fixture DLL',
                   'Original game EXE, DLLs and configuration unchanged (SHA-256)'],
        'source_sha256': original_hashes,
        'game_executed': False,
    }
    (fixture / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('Usage: python integration_check.py <game.exe with existing x64 D3D11 DXVK>')
    check(sys.argv[1])
