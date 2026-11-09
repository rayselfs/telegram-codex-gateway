"""Maintenance-only pinned third-party package; no installer hooks are executed."""
import hashlib
import json
from pathlib import Path
import shutil
import stat

from gateway.core.bootstrap import ROOT
MANAGED = ROOT/'managed/superpowers/7.0.1'
LEGACY_ROOTS = []


def hashes(root):
    result = {}
    if getattr(root.lstat(),'st_file_attributes',0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise ValueError('Managed package cannot be redirected')
    for path in sorted(root.rglob('*')):
        if getattr(path.lstat(),'st_file_attributes',0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError('Managed resources cannot be redirected')
        if path.is_file():
            result[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def snapshot(source, destination):
    source,destination = Path(source),Path(destination)
    meta=json.loads((source/'.codex-plugin/plugin.json').read_text(encoding='utf-8'))
    if (meta['name'],meta['version']) != ('superpowers','7.0.1') or not (source/'LICENSE').is_file():
        raise ValueError('Expected licensed Superpowers 7.0.1')
    original=hashes(source)
    destination.mkdir(parents=True,exist_ok=False)
    shutil.copytree(source,destination/'package')
    if hashes(destination/'package') != original or hashes(source) != original:
        raise ValueError('Package changed during snapshot; retain for inspection')
    (destination/'manifest.json').write_text(json.dumps({
        'name':'superpowers','version':'7.0.1','source':str(source),
        'upstream':'https://github.com/obra/superpowers','license':'MIT','files':original},indent=2),encoding='utf-8')


def verify(destination=MANAGED):
    destination=Path(destination)
    manifest=json.loads((destination/'manifest.json').read_text(encoding='utf-8'))
    if (manifest['name'],manifest['version']) != ('superpowers','7.0.1') or hashes(destination/'package')!=manifest['files']:
        raise ValueError('Managed package integrity mismatch')
    return destination/'package/skills'


def extra_roots():
    return [*LEGACY_ROOTS,*([str(verify())] if (MANAGED/'manifest.json').exists() else [])]
