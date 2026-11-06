"""Persist installation paths; missing configuration never selects another root."""
import ctypes
import json
import os
from pathlib import Path
import uuid

def validate_root(root, code):
    root, code = Path(root), Path(code).absolute()
    if not root.is_absolute() or root == Path(root.anchor) or str(root).startswith(('\\\\','//')):
        raise ValueError('Choose an absolute local data directory')
    for p in (root, code):
        for ancestor in (p, *p.parents):
            if ancestor.is_symlink() or ancestor.is_junction():
                raise ValueError('Deployment directories must not be redirected')
    root, code = root.resolve(), code.resolve()
    if root.is_relative_to(code) or code.is_relative_to(root):
        raise ValueError('Data and source directories must not overlap')
    if os.name == 'nt':
        drive_type = ctypes.windll.kernel32.GetDriveTypeW(str(root.anchor))
        if drive_type not in (2, 3, 6):
            raise ValueError('Data root must be on an available local drive')
    return root

def create(root, code, python, codex, timezone):
    from zoneinfo import ZoneInfo
    ZoneInfo(timezone)
    root = validate_root(root, code)
    if root.exists() and any(root.iterdir()):
        raise ValueError('Nonempty data directory requires explicit resume or migration')
    executables = [Path(p).absolute() for p in (python, codex)]
    if not all(p.is_file() for p in executables):
        raise ValueError('Configured runtimes must exist')
    root.mkdir(parents=True, exist_ok=True)
    value = dict(version=1, deployment_id=uuid.uuid4().hex, data_root=str(root),
                 code_root=str(Path(code).resolve()), python=str(executables[0]),
                 codex=str(executables[1]), timezone=timezone)
    with (root/'deployment.json').open('x', encoding='utf-8') as f:
        json.dump(value,f,indent=2); f.flush(); os.fsync(f.fileno())
    return value

def load(path):
    path = Path(path)
    if not path.is_absolute(): raise ValueError('Absolute deployment configuration path required')
    value = json.loads(path.read_text(encoding='utf-8'))
    if value.get('version') != 1 or len(value.get('deployment_id','')) != 32:
        raise ValueError('Invalid deployment configuration')
    int(value['deployment_id'],16)
    root = validate_root(value['data_root'], value['code_root'])
    if path != root/'deployment.json' or not root.is_dir():
        raise ValueError('Configuration does not match its persisted root')
    for key in ('python','codex'):
        if not Path(value[key]).is_absolute() or not Path(value[key]).is_file():
            raise ValueError('Configured runtime is unavailable')
    return value

def deployment_name(config, owner=None):
    if owner is not None and (type(owner) is not int or owner <= 0):
        raise ValueError('Numeric owner required')
    return 'telegram-codex-'+config['deployment_id']+('' if owner is None else '-'+str(owner))
