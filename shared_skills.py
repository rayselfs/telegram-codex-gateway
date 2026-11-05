"""Broker-only static publisher. This is NOT a network or CLI authorization boundary.

caller must come from the authenticated transport, never tool arguments. The store
and this code must be owned by the broker, with no worker write/delete access.
Archives arrive as bytes: the broker never opens an author-selected filesystem path.
"""
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import stat
import uuid
import zipfile

MAX_BYTES = 8 * 1024 * 1024
MAX_FILES = 512


def unpack(data):
    if not isinstance(data, bytes) or len(data) > MAX_BYTES:
        raise ValueError('Package exceeds 8 MiB')
    files, seen, spellings, total = {}, set(), {}, 0
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        if len(entries) > MAX_FILES:
            raise ValueError('Too many package entries')
        for entry in entries:
            if entry.orig_filename != entry.filename:
                raise ValueError('Archive path was normalized or truncated')
            name = entry.filename.rstrip('/') if entry.is_dir() else entry.filename
            parts = name.split('/')
            if (not name or len(name) > 200 or '\\' in name or ':' in name or
                    any(not p or p in ('.', '..') or p[-1] in '. ' or
                        re.search(r'[<>"|?*\x00-\x1f]', p) or
                        re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?', p, re.I) for p in parts)):
                raise ValueError('Unsafe package path')
            if name.casefold() in seen:
                raise ValueError('Duplicate Windows path')
            seen.add(name.casefold())
            for depth in range(1,len(parts)+1):
                prefix='/'.join(parts[:depth])
                old=spellings.setdefault(prefix.casefold(),prefix)
                if old!=prefix: raise ValueError('Windows parent directory alias')
            mode = (entry.external_attr >> 16) & 0o170000
            if mode not in (0, stat.S_IFREG, stat.S_IFDIR) or entry.flag_bits & 1:
                raise ValueError('Links, devices and encrypted entries are not supported')
            if entry.is_dir():
                continue
            total += entry.file_size
            if total > MAX_BYTES:
                raise ValueError('Expanded package exceeds 8 MiB')
            files[name] = archive.read(entry)
    if 'SKILL.md' not in files or not files['SKILL.md'].startswith(b'---'):
        raise ValueError('SKILL.md with frontmatter required')
    if 'manifest.json' in {p.casefold() for p in files}:
        raise ValueError('manifest.json is reserved')
    for name, content in files.items():
        for parent in PurePosixPath(name).parents:
            if str(parent).casefold() in {p.casefold() for p in files}:
                raise ValueError('File/directory collision')
        if name.endswith('.md'):
            text = content.decode('utf-8-sig')
            if re.search(r'^\s*\[[^\]]+\]:',text,re.M):
                raise ValueError('Reference-style links unsupported; use explicit inline resource links')
            # Static links only; code references are not executed or guessed.
            for link in re.findall(r'!?\[[^\]]*\]\(([^)]+)\)', text):
                link = link.split(' "', 1)[0].strip('<>')
                if link.startswith(('https://', 'http://', 'mailto:', '#')):
                    continue
                link = link.split('#', 1)[0]
                if not link or ':' in link or '\\' in link or link.startswith('/'):
                    raise ValueError('Invalid local resource reference')
                resolved = []
                for part in (PurePosixPath(name).parent / link).parts:
                    if part == '..':
                        if not resolved: raise ValueError('Resource escapes package')
                        resolved.pop()
                    elif part != '.': resolved.append(part)
                target = '/'.join(resolved)
                if target not in files and not any(p.startswith(target + '/') for p in files):
                    raise ValueError('Missing referenced resource')
    return files


class SkillStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root/'registry.sqlite')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS skills (
            id TEXT PRIMARY KEY, owner INTEGER NOT NULL, name TEXT NOT NULL,
            source TEXT, active TEXT);
          CREATE TABLE IF NOT EXISTS versions (
            skill TEXT, version TEXT, manifest TEXT NOT NULL,
            PRIMARY KEY(skill,version));
        ''')

    def close(self): self.db.close()

    def _owned(self, caller, skill_id):
        row = self.db.execute('SELECT owner FROM skills WHERE id=?', (skill_id,)).fetchone()
        if type(caller) is not int or caller <= 0 or not row or row[0] != caller:
            raise PermissionError('Only the verified original author may change this skill')

    def publish(self, caller, data, *, name, note, skill_id=None, source=None):
        if type(caller) is not int or caller <= 0:
            raise PermissionError('Verified Telegram identity required')
        if any(not isinstance(v, str) or not v.strip() or len(v)>limit or
               any(ord(c)<32 for c in v) for v,limit in ((name,80),(note,500))):
            raise ValueError('Short display name and change note required')
        # Authorize before parsing untrusted bytes. SQLite serializes revisions.
        self.db.execute('BEGIN IMMEDIATE')
        stage = None
        try:
            if skill_id is not None:
                self._owned(caller, skill_id)
                if source is not None: raise ValueError('Derivation cannot change on update')
            elif source is not None:
                if not isinstance(source, dict) or set(source) != {'skill_id','version'}:
                    raise ValueError('Derivation must identify a published version')
                if not self.db.execute('SELECT 1 FROM versions WHERE skill=? AND version=?',
                                       (source['skill_id'],source['version'])).fetchone():
                    raise ValueError('Unknown source version')
            files = unpack(data)
            hashes = {p:hashlib.sha256(b).hexdigest() for p,b in sorted(files.items())}
            version = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
            sid = skill_id or uuid.uuid4().hex
            if self.db.execute('SELECT 1 FROM versions WHERE skill=? AND version=?',(sid,version)).fetchone():
                raise ValueError('Version already exists; activate it explicitly')
            manifest = {'skill_id':sid,'owner_id':caller,'name':name,'version':version,
                        'files':hashes,'note':note,'source':source if skill_id is None else
                        json.loads(self.db.execute('SELECT source FROM skills WHERE id=?',(sid,)).fetchone()[0])}
            stage = self.root/('.stage-'+uuid.uuid4().hex)
            stage.mkdir()
            bundle = stage/'skills'/sid
            for relative, content in files.items():
                target = bundle/relative
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open('xb') as f:
                    f.write(content); f.flush(); os.fsync(f.fileno())
            (stage/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
            (stage/'.codex-plugin').mkdir()
            (stage/'.codex-plugin/plugin.json').write_text(json.dumps({
                'name':f'shared-{caller}-{sid}','version':'1.0.0',
                'description':name,'skills':'./skills'}),encoding='utf-8')
            target = self.root/'versions'/sid/version
            target.parent.mkdir(parents=True,exist_ok=True)
            # An interrupted previous publish is retained for maintenance; never overwrite it.
            if target.exists(): raise ValueError('Unregistered version exists; maintenance must reconcile')
            stage.rename(target)
            if skill_id is None:
                self.db.execute('INSERT INTO skills VALUES (?,?,?,?,?)',
                                (sid,caller,name,json.dumps(source),version))
            else:
                self.db.execute('UPDATE skills SET name=?, active=? WHERE id=?',(name,version,sid))
            self.db.execute('INSERT INTO versions VALUES (?,?,?)',(sid,version,json.dumps(manifest)))
            self.db.commit()
            return manifest
        except Exception:
            self.db.rollback()
            raise
        finally:
            if stage and stage.exists(): shutil.rmtree(stage)

    def activate(self, caller, skill_id, version):
        with self.db:
            self._owned(caller, skill_id)
            if not self.db.execute('SELECT 1 FROM versions WHERE skill=? AND version=?',(skill_id,version)).fetchone():
                raise ValueError('Unknown immutable version')
            self._verify(skill_id, version)
            self.db.execute('UPDATE skills SET active=? WHERE id=?',(version,skill_id))

    def withdraw(self, caller, skill_id):
        with self.db:
            self._owned(caller, skill_id)
            self.db.execute('UPDATE skills SET active=NULL WHERE id=?',(skill_id,))

    def listing(self):
        return [dict(skill_id=s, owner_id=o, name=n, source=json.loads(d), version=v)
                for s,o,n,d,v in self.db.execute('SELECT * FROM skills ORDER BY rowid')]

    def _verify(self, sid, version):
        row = self.db.execute('SELECT manifest FROM versions WHERE skill=? AND version=?',(sid,version)).fetchone()
        if not row: raise ValueError('Unregistered version')
        manifest = json.loads(row[0])
        version_root = self.root/'versions'/sid/version
        base = version_root/'skills'/sid
        for parent in [base,*base.parents]:
            if parent.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise ValueError('Published root redirected')
            if parent == self.root: break
        expected_plugin={'name':f'shared-{manifest["owner_id"]}-{sid}','version':'1.0.0',
                         'description':manifest['name'],'skills':'./skills'}
        if (json.loads((version_root/'.codex-plugin/plugin.json').read_text(encoding='utf-8'))!=expected_plugin or
                json.loads((version_root/'manifest.json').read_text(encoding='utf-8'))!=manifest):
            raise ValueError('Published metadata integrity failure')
        expected_files={f'skills/{sid}/{p}' for p in manifest['files']} | {'manifest.json','.codex-plugin/plugin.json'}
        actual_files=set()
        for path in version_root.rglob('*'):
            if path.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise ValueError('Published resource redirected')
            if path.is_file(): actual_files.add(path.relative_to(version_root).as_posix())
        if actual_files!=expected_files: raise ValueError('Published envelope changed')
        actual = {}
        for path in base.rglob('*'):
            if path.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise ValueError('Published resource redirected')
            if path.is_file():
                actual[path.relative_to(base).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != manifest['files']: raise ValueError('Published resource integrity failure')
        return base/'SKILL.md'

    def pin(self):
        # Pin the entire version directory; withdrawal never deletes old versions.
        return [{**row, 'path':str(self._verify(row['skill_id'],row['version'])),
                 'root':str(self.root/'versions'/row['skill_id']/row['version']/'skills')}
                for row in self.listing() if row['version']]
