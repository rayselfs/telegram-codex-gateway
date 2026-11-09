"""Transactional membership. Caller identity must come from verified routing."""
from contextlib import contextmanager
import hashlib
from pathlib import Path
import secrets
import sqlite3
import time

def owner_id(value):
    if type(value) is not int or value <= 0:
        raise ValueError('Verified numeric owner required')
    return value

class Members:
    def __init__(self, path, root):
        self.root = Path(root).absolute()
        self.db = sqlite3.connect(path, isolation_level=None, timeout=15)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS members (
            owner_id INTEGER PRIMARY KEY, display_name TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('admin','staff')),
            state TEXT NOT NULL CHECK(state IN ('pending','active','suspended')),
            home TEXT NOT NULL UNIQUE, workspace TEXT NOT NULL UNIQUE,
            approved INTEGER NOT NULL DEFAULT 0, provisioned INTEGER NOT NULL DEFAULT 0,
            schedules_paused INTEGER NOT NULL DEFAULT 1);
          CREATE UNIQUE INDEX IF NOT EXISTS unique_admin ON members(role) WHERE role='admin';
          CREATE TABLE IF NOT EXISTS invites (
            digest TEXT PRIMARY KEY, expires REAL NOT NULL, claimed_by INTEGER REFERENCES members(owner_id));
          CREATE TABLE IF NOT EXISTS member_audit (
            id INTEGER PRIMARY KEY, at REAL NOT NULL, actor INTEGER NOT NULL,
            subject INTEGER NOT NULL, action TEXT NOT NULL, reason TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS member_epoch (singleton INTEGER PRIMARY KEY CHECK(singleton=1), value INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS suspension_origin(owner INTEGER PRIMARY KEY, state TEXT NOT NULL);
          INSERT OR IGNORE INTO member_epoch VALUES(1,0);
        ''')

    def close(self): self.db.close()

    @contextmanager
    def transaction(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

    def _audit(self, actor, subject, action, reason=''):
        self.db.execute('INSERT INTO member_audit(at,actor,subject,action,reason) VALUES(?,?,?,?,?)',
                        (time.time(), actor, subject, action, reason))

    def _changed(self): self.db.execute('UPDATE member_epoch SET value=value+1')

    @property
    def epoch(self): return self.db.execute('SELECT value FROM member_epoch').fetchone()[0]

    def _member(self, owner):
        row = self.db.execute('SELECT * FROM members WHERE owner_id=?', (owner_id(owner),)).fetchone()
        if row is None:
            raise PermissionError('Member is not approved')
        return dict(row)

    def require_active(self, owner):
        row = self._member(owner)
        if row['state'] != 'active':
            raise PermissionError('Member is not active')
        return row

    def require_admin(self, owner):
        row = self.require_active(owner)
        if row['role'] != 'admin':
            raise PermissionError('Administrator required')
        return row

    def require_onboarding(self, owner):
        row = self._member(owner)
        if row['state'] != 'pending' or not row['approved']:
            raise PermissionError('Registration approval required')
        return row

    def _paths(self, rows):
        seen, paths = set(), []
        for row in rows:
            owner = owner_id(row['owner_id'])
            if owner in seen: raise ValueError('Duplicate owner')
            seen.add(owner)
            for key in ('home', 'workspace'):
                p = Path(row[key])
                if not p.is_absolute() or '..' in p.parts or p == self.root or not p.is_relative_to(self.root):
                    raise ValueError('Member path must remain inside deployment')
                for part in (p, *p.parents):
                    if part.is_symlink() or part.is_junction():
                        raise ValueError('Member paths must not be redirected')
                p = p.resolve()
                if p == self.root.resolve() or not p.is_relative_to(self.root.resolve()):
                    raise ValueError('Member path escapes deployment')
                if any(p.is_relative_to(other) or other.is_relative_to(p) for other in paths):
                    raise ValueError('Member directories overlap')
                paths.append(p)

    def import_existing(self, rows, admin_owner):
        owner_id(admin_owner)
        if not rows or admin_owner not in [r['owner_id'] for r in rows]:
            raise ValueError('Existing administrator must be explicitly identified')
        self._paths(rows)
        with self.transaction():
            admin = self.db.execute("SELECT owner_id FROM members WHERE role='admin'").fetchone()
            if admin and admin[0] != admin_owner:
                raise ValueError('Existing administrator cannot be replaced')
            present = {r['owner_id']: dict(r) for r in self.db.execute('SELECT * FROM members')}
            combined = list(present.values()) + [r for r in rows if r['owner_id'] not in present]
            self._paths(combined)
            for row in rows:
                owner = row['owner_id']
                name = row['name']
                if not isinstance(name, str) or not name.strip(): raise ValueError('Display name required')
                home, workspace = str(row['home']), str(row['workspace'])
                role = 'admin' if owner == admin_owner else 'staff'
                if owner in present:
                    old = present[owner]
                    if old['home'] != home or old['workspace'] != workspace or old['role'] != role:
                        raise ValueError('Migration would alter existing member binding')
                    continue
                self.db.execute('INSERT INTO members VALUES(?,?,?,?,?,?,?,?,?)',
                    (owner, name, role, 'active', home, workspace, 1, 1, 0))
                self._audit(admin_owner, owner, 'import_existing')
                self._changed()

    def active_profiles(self):
        return [dict(owner_id=r['owner_id'], name=r['display_name'], home=Path(r['home']), workspace=Path(r['workspace']))
                for r in self.db.execute("SELECT * FROM members WHERE state='active' ORDER BY owner_id")]

    def list_members(self, actor):
        self.require_admin(actor)
        return [dict(r) for r in self.db.execute('SELECT owner_id,display_name,role,state,approved,provisioned FROM members ORDER BY owner_id')]

    def invite(self, actor, *, now=None):
        token = secrets.token_urlsafe(32)
        with self.transaction():
            self.require_admin(actor)
            self.db.execute('INSERT INTO invites VALUES(?,?,NULL)',
                (hashlib.sha256(token.encode()).hexdigest(), (time.time() if now is None else now) + 86400))
            self._audit(actor, actor, 'invite')
        return token

    def claim(self, token, owner, name, *, now=None):
        owner_id(owner)
        if not isinstance(token, str) or len(token) > 128 or not isinstance(name, str) or not name.strip() or len(name) > 128:
            raise ValueError('Invalid invitation claim')
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.transaction():
            invite = self.db.execute('SELECT * FROM invites WHERE digest=?', (digest,)).fetchone()
            if not invite or invite['expires'] <= (time.time() if now is None else now):
                raise PermissionError('Invitation is invalid or expired')
            if invite['claimed_by'] is not None:
                if invite['claimed_by'] != owner: raise PermissionError('Invitation already claimed')
                return self._member(owner)
            if self.db.execute('SELECT 1 FROM members WHERE owner_id=?', (owner,)).fetchone():
                raise PermissionError('Member already registered')
            home, workspace = [str(self.root/'users'/str(owner)/p) for p in ('profile', 'workspace')]
            rows = [dict(r) for r in self.db.execute('SELECT * FROM members')]
            self._paths(rows + [dict(owner_id=owner, home=home, workspace=workspace)])
            self.db.execute('INSERT INTO members VALUES(?,?,?,?,?,?,?,?,?)',
                (owner, name, 'staff', 'pending', home, workspace, 0, 0, 1))
            self.db.execute('UPDATE invites SET claimed_by=? WHERE digest=?', (owner, digest))
            self._audit(owner, owner, 'claim')
            return self._member(owner)

    def approve(self, actor, owner):
        with self.transaction():
            self.require_admin(actor)
            row = self._member(owner)
            if row['state'] != 'pending': raise PermissionError('Pending member required')
            self.db.execute('UPDATE members SET approved=1 WHERE owner_id=?', (owner,))
            self._audit(actor, owner, 'approve')

    def provision(self, actor, owner):
        with self.transaction():
            self.require_admin(actor)
            row = self.require_onboarding(owner)
            self._paths([dict(r) for r in self.db.execute('SELECT * FROM members')])
            home = Path(row['home'])
            for path in (home/'codex-home', home/'temp', home/'inbox', home/'outbox', Path(row['workspace'])):
                path.mkdir(parents=True, exist_ok=True)
            config = home/'codex-home/config.toml'
            if not config.exists():
                with config.open('x', encoding='utf-8') as f:
                    f.write('mcp_oauth_credentials_store = "file"\n[features]\napps = false\n')
            self.db.execute('UPDATE members SET provisioned=1 WHERE owner_id=?', (owner,))
            self._audit(actor, owner, 'provision')
            return self._member(owner)

    def activate(self, actor, owner, health_check):
        self.require_admin(actor)
        candidate = self.require_onboarding(owner)
        if not candidate['provisioned']: raise PermissionError('Provisioning required')
        if not health_check(candidate): raise RuntimeError('Non-inference account check is not ready')
        with self.transaction():
            self.require_admin(actor)
            current = self.require_onboarding(owner)
            if current != candidate: raise RuntimeError('Member changed during account check')
            self.db.execute("UPDATE members SET state='active',schedules_paused=0 WHERE owner_id=?", (owner,))
            self._changed(); self._audit(actor, owner, 'activate')

    def suspend(self, actor, owner, reason):
        if not isinstance(reason, str) or not reason.strip(): raise ValueError('Reason required')
        with self.transaction():
            self.require_admin(actor)
            row = self._member(owner)
            if row['role'] == 'admin': raise PermissionError('Cannot suspend the administrator')
            if row['state'] != 'suspended':
                self.db.execute('INSERT OR REPLACE INTO suspension_origin VALUES(?,?)',(owner,row['state']))
                self.db.execute("UPDATE members SET state='suspended',schedules_paused=1 WHERE owner_id=?", (owner,))
                self._changed(); self._audit(actor, owner, 'suspend', reason)

    def restore(self, actor, owner, reason):
        if not isinstance(reason, str) or not reason.strip(): raise ValueError('Reason required')
        with self.transaction():
            self.require_admin(actor)
            row = self._member(owner)
            if row['state'] != 'suspended' or not row['provisioned']:
                raise PermissionError('Previously provisioned suspended member required')
            previous=self.db.execute('SELECT state FROM suspension_origin WHERE owner=?',(owner,)).fetchone()
            state='active' if previous and previous['state']=='active' else 'pending'
            self.db.execute("UPDATE members SET state=?,schedules_paused=1 WHERE owner_id=?", (state,owner))
            self._changed(); self._audit(actor, owner, 'restore', reason)

    def resume_own_schedules(self, owner):
        with self.transaction():
            self.require_active(owner)
            self.db.execute('UPDATE members SET schedules_paused=0 WHERE owner_id=?', (owner,))
            self._audit(owner, owner, 'resume_own_schedules')

    def require_schedule(self, owner):
        row = self.require_active(owner)
        if row['schedules_paused']: raise PermissionError('Member must confirm schedule restoration')
        return row
