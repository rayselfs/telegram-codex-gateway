"""Host-confirmed first administrator pairing; no model or service calls."""
import argparse
import getpass
import hashlib
import json
from pathlib import Path
import secrets
import sys
import time
from gateway.core.members import Members, owner_id

class Bootstrap:
    def __init__(self, members):
        self.members = members
        members.db.execute('''CREATE TABLE IF NOT EXISTS bootstrap (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1), digest TEXT NOT NULL,
            expires REAL NOT NULL, claim_id TEXT, owner INTEGER,
            confirmed INTEGER NOT NULL DEFAULT 0, consumed INTEGER NOT NULL DEFAULT 0)''')

    def issue(self, *, now=None):
        token = secrets.token_urlsafe(32)
        with self.members.transaction():
            if self.members.db.execute('SELECT 1 FROM members').fetchone():
                raise PermissionError('Existing deployment cannot be paired again')
            self.members.db.execute('INSERT OR REPLACE INTO bootstrap VALUES(1,?,?,NULL,NULL,0,0)',
                (hashlib.sha256(token.encode()).hexdigest(), (time.time() if now is None else now)+900))
        return token

    def claim_bootstrap(self, token, telegram_user_id, *, private, now=None):
        owner_id(telegram_user_id)
        if not private or not isinstance(token,str) or len(token)>128:
            raise PermissionError('Private pairing required')
        with self.members.transaction():
            row = self.members.db.execute('SELECT * FROM bootstrap').fetchone()
            if not row or row['consumed'] or row['expires'] <= (time.time() if now is None else now) or not secrets.compare_digest(row['digest'],hashlib.sha256(token.encode()).hexdigest()):
                raise PermissionError('Pairing code is invalid or expired')
            if row['owner'] is not None:
                if row['owner'] != telegram_user_id: raise PermissionError('Pairing already claimed')
                return row['claim_id']
            claim = secrets.token_hex(16)
            self.members.db.execute('UPDATE bootstrap SET claim_id=?,owner=?', (claim,telegram_user_id))
            return claim

    def confirm_bootstrap(self, claim_id, expected_owner, *, now=None):
        owner_id(expected_owner)
        with self.members.transaction():
            row = self.members.db.execute('SELECT * FROM bootstrap WHERE claim_id=?', (claim_id,)).fetchone()
            if not row or row['consumed'] or row['owner'] != expected_owner or row['expires'] <= (time.time() if now is None else now):
                raise PermissionError('Host must verify this exact claim and owner')
            home, workspace = [str(self.members.root/'users'/str(expected_owner)/p) for p in ('profile','workspace')]
            self.members._paths([dict(owner_id=expected_owner,home=home,workspace=workspace)])
            if row['confirmed']:
                member=self.members.require_onboarding(expected_owner)
                if member['role'] != 'admin' or member['home'] != home or member['workspace'] != workspace:
                    raise PermissionError('Bootstrap binding changed')
            else:
                if self.members.db.execute('SELECT 1 FROM members').fetchone():
                    raise PermissionError('Administrator already exists')
                self.members.db.execute('INSERT INTO members VALUES(?,?,?,?,?,?,?,?,?)',
                    (expected_owner,'Administrator','admin','pending',home,workspace,1,0,1))
                self.members.db.execute('UPDATE bootstrap SET confirmed=1')
                self.members._audit(expected_owner,expected_owner,'host_confirm_bootstrap')
        # Only empty per-owner resources are created; no existing profile is inherited.
        for p in (Path(home)/'codex-home',Path(workspace)):
            p.mkdir(parents=True,exist_ok=True)
        config=Path(home)/'codex-home/config.toml'
        if not config.exists():
            with config.open('x',encoding='utf-8') as f:
                f.write('mcp_oauth_credentials_store = "file"\n[features]\napps = false\n')
        self.members.db.execute('UPDATE members SET provisioned=1 WHERE owner_id=?',(expected_owner,))

    def activate(self, claim_id, health_check):
        row = self.members.db.execute('SELECT * FROM bootstrap WHERE claim_id=?',(claim_id,)).fetchone()
        if not row or not row['confirmed'] or row['consumed']:
            raise PermissionError('Host-confirmed bootstrap required')
        candidate=self.members.require_onboarding(row['owner'])
        if not candidate['provisioned'] or not health_check(candidate):
            raise RuntimeError('Core login/account check is not ready')
        with self.members.transaction():
            latest=self.members.db.execute('SELECT * FROM bootstrap WHERE claim_id=?',(claim_id,)).fetchone()
            if not latest or latest['consumed'] or self.members._member(row['owner']) != candidate:
                raise PermissionError('Bootstrap state changed')
            self.members.db.execute("UPDATE members SET state='active',schedules_paused=0 WHERE owner_id=?",(row['owner'],))
            self.members.db.execute('UPDATE bootstrap SET consumed=1')
            self.members._changed();self.members._audit(row['owner'],row['owner'],'activate_bootstrap')

def main():
    from gateway.core.deployment import create
    from gateway.platforms import current
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path)
    parser.add_argument('--codex',required=True,type=Path)
    parser.add_argument('--timezone',default='UTC')
    args=parser.parse_args()
    root=args.root or Path(input('Local absolute data folder: ').strip())
    code=Path(__file__).resolve().parents[2]
    lock=json.loads((code/'versions.json').read_text(encoding='utf-8-sig'))
    if sys.version_info[:2] != (3,15): raise RuntimeError('This release requires Python 3.15')
    if sys.platform != 'win32':
        raise RuntimeError('Native platform runtime hash acceptance is pending; installation blocked')
    if hashlib.sha256(args.codex.read_bytes()).hexdigest() != lock['codex_sha256']:
        raise RuntimeError('Codex runtime does not match accepted artifact')
    backend=current()
    token=getpass.getpass('Telegram bot token (hidden): ').strip()
    if ':' not in token or not token.split(':')[0].isdigit(): raise ValueError('Invalid bot token')
    encrypted=backend.crypt(token.encode(),b'',True)
    cfg=create(root,code,Path(sys.executable),args.codex,args.timezone)
    home=Path(cfg['data_root'])/'profile';home.mkdir()
    (home/'telegram-token.dpapi').write_bytes(encrypted)
    members=Members(home/'members.sqlite',root)
    try: pairing=Bootstrap(members).issue()
    finally: members.close()
    print('Configuration: '+str(root/'deployment.json'))
    print('Send /start bootstrap_'+pairing+' in a private chat to your bot within 15 minutes.')
    print('Run registration polling, inspect the claimed numeric ID locally, then confirm it on this host.')

if __name__=='__main__': main()
