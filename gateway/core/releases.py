"""Explicit code-only package transactions; runtime databases are never restored."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil

EXTENSIONS = {'.py', '.ps1', '.vbs', '.mjs', '.cjs', '.md', '.json', '.toml', '.txt', '.sh', '.plist'}
RESERVED = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(10)), *(f'LPT{i}' for i in range(10))}

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None

def checked_path(root, name):
    if not isinstance(name, str) or not name or '\\' in name or ':' in name:
        raise ValueError('Invalid relative code path')
    p = PurePosixPath(name)
    if p.is_absolute() or p.as_posix() != name or p.suffix not in EXTENSIONS:
        raise ValueError('Invalid relative code path')
    if any(part in ('.', '..') or part.rstrip(' .') != part or
           part.split('.')[0].upper() in RESERVED for part in p.parts):
        raise ValueError('Ambiguous code path')
    root = Path(root).absolute()
    path = root.joinpath(*p.parts)
    for ancestor in (path, *path.parents):
        if ancestor.is_symlink() or ancestor.is_junction():
            raise ValueError('Reparse paths are forbidden')
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Code path escapes root')
    return path

def checked_files(source, names):
    rows, seen = [], set()
    for name in names:
        path = checked_path(source, name)
        key = name.casefold()
        if key in seen or not path.is_file() or path.stat().st_nlink != 1:
            raise ValueError('Missing, duplicate, or aliased code file')
        seen.add(key)
        if path.suffix == '.py':
            compile(path.read_text(encoding='utf-8-sig'), name, 'exec')
        rows.append({'name': name, 'after': digest(path)})
    if not rows:
        raise ValueError('Empty release')
    return rows

def _manifest(evidence, rows):
    path = evidence / 'manifest.json'
    tmp = evidence / 'manifest.tmp'
    tmp.write_text(json.dumps(rows, indent=2), encoding='utf-8')
    os.replace(tmp, path)

def apply_package(source, target, names, evidence):
    source, target, evidence = [Path(p).absolute() for p in (source, target, evidence)]
    for a, b in ((source, target), (source, evidence), (target, evidence)):
        if a.is_relative_to(b) or b.is_relative_to(a):
            raise ValueError('Release roots must not overlap')
    rows = checked_files(source, names)
    for row in rows:
        dst = checked_path(target, row['name'])
        if dst.exists() and (not dst.is_file() or dst.stat().st_nlink != 1):
            raise ValueError('Invalid destination file')
        row['before'] = digest(dst)
        checked_path(evidence, row['name'])
    evidence.mkdir(parents=True, exist_ok=False)
    # Finish and verify every backup and staged file before replacing any code.
    for row in rows:
        name = row['name']
        stage = evidence / 'staged' / name
        stage.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(checked_path(source, name), stage)
        if digest(stage) != row['after']:
            raise RuntimeError('Source changed while staging')
        if row['before'] is not None:
            backup = evidence / 'before' / name
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(checked_path(target, name), backup)
            if digest(backup) != row['before']:
                raise RuntimeError('Target changed while backing up')
    _manifest(evidence, rows)
    try:
        for row in rows:
            dst = checked_path(target, row['name'])
            if digest(dst) != row['before']:
                raise RuntimeError('Concurrent target change')
            dst.parent.mkdir(parents=True, exist_ok=True)
            os.replace(evidence / 'staged' / row['name'], dst)
            if digest(dst) != row['after']:
                raise RuntimeError('Installed code hash mismatch')
    except BaseException:
        rollback_package(target, evidence)
        raise
    return rows

def rollback_package(target, evidence):
    target, evidence = Path(target), Path(evidence)
    manifest = checked_path(evidence, 'manifest.json')
    rows = json.loads(manifest.read_text(encoding='utf-8'))
    if not isinstance(rows, list) or not rows:
        raise ValueError('Invalid release manifest')
    seen = set()
    # Validate the entire rollback before touching a file, including new files.
    for row in rows:
        name = row['name']
        dst = checked_path(target, name)
        if name.casefold() in seen:
            raise ValueError('Duplicate manifest path')
        seen.add(name.casefold())
        current = digest(dst)
        if current not in (row['before'], row['after']):
            raise RuntimeError('Code changed after release; rollback blocked')
        if row['before'] is not None:
            backup = checked_path(evidence / 'before', name)
            if digest(backup) != row['before']:
                raise RuntimeError('Rollback backup hash mismatch')
    for row in reversed(rows):
        dst = checked_path(target, row['name'])
        if digest(dst) == row['before']:
            continue
        if row['before'] is None:
            dst.unlink()
        else:
            # copy2 preserves executable modes on platforms that use them.
            shutil.copy2(checked_path(evidence / 'before', row['name']), dst)
    return rows
