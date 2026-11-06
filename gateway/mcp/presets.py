"""Optional service policy; existing owner settings are admitted by exact hash."""
import hashlib
import json
import tomllib
import os
import secrets
from pathlib import Path

CATALOG=Path(__file__).resolve().parents[2]/'presets/mcp/catalog.json'

def fingerprint(config):
    return hashlib.sha256(json.dumps(config,sort_keys=True,separators=(',',':')).encode()).hexdigest()

class Presets:
    def __init__(self,members):
        self.members=members;self.db=members.db
        self.catalog=json.loads(CATALOG.read_text(encoding='utf-8'))
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS mcp_allowed(provider TEXT PRIMARY KEY,enabled INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS mcp_existing(owner INTEGER NOT NULL,provider TEXT NOT NULL,fingerprint TEXT NOT NULL,PRIMARY KEY(owner,provider));
          CREATE TABLE IF NOT EXISTS mcp_imported(owner INTEGER PRIMARY KEY);
        ''')

    def import_existing(self,owner,servers):
        self.members.require_active(owner)
        with self.members.transaction():
            if self.db.execute('SELECT 1 FROM mcp_imported WHERE owner=?',(owner,)).fetchone(): return
            for provider,config in servers.items():
                if config.get('enabled',True):
                    self.db.execute('INSERT INTO mcp_existing VALUES(?,?,?)',(owner,provider,fingerprint(config)))
            self.db.execute('INSERT INTO mcp_imported VALUES(?)',(owner,))

    def set_allowed(self,actor,provider,enabled):
        if type(enabled) is not bool or provider not in self.catalog:raise ValueError('Installed preset required')
        with self.members.transaction():
            self.members.require_admin(actor)
            self.db.execute('INSERT OR REPLACE INTO mcp_allowed VALUES(?,?)',(provider,int(enabled)))
            self.members._audit(actor,actor,'mcp_policy',provider+':'+str(enabled))

    def allowed(self,owner,provider,config):
        self.members.require_active(owner)
        setting=self.db.execute('SELECT enabled FROM mcp_allowed WHERE provider=?',(provider,)).fetchone()
        if setting and not setting[0]:return False
        legacy=self.db.execute('SELECT fingerprint FROM mcp_existing WHERE owner=? AND provider=?',(owner,provider)).fetchone()
        if legacy and legacy[0]==fingerprint(config):return True
        preset=self.catalog.get(provider)
        if not setting or not setting[0] or not preset or preset['status']!='configurable':return False
        expected=preset['server']
        return {k:v for k,v in config.items() if k!='enabled'}==expected

    def connection(self,owner,provider):
        member=self.members.require_active(owner)
        preset=self.catalog.get(provider)
        if not preset or preset['status']!='configurable' or not self.allowed(owner,provider,preset['server']):
            raise PermissionError('Preset is disabled or needs host setup')
        return {'owner_id':owner,'home':member['home'],'provider_id':provider,'server':preset['server'].copy()}

    def authorize_runtime(self,owner,*,scheduled=False):
        member=(self.members.require_schedule if scheduled else self.members.require_active)(owner)
        path=Path(member['home'])/'codex-home/config.toml'
        servers=tomllib.loads(path.read_text(encoding='utf-8-sig')).get('mcp_servers',{})
        for provider,config in servers.items():
            if config.get('enabled',True) and not self.allowed(owner,provider,config):
                raise PermissionError('Configured MCP is not approved for this owner; runtime remains blocked')
        return member

    def connect(self,owner,provider):
        connection=self.connection(owner,provider)
        from lifecycle import SingleInstance
        path=Path(connection['home'])/'codex-home/config.toml'
        for part in (path,*path.parents):
            if part.is_symlink() or part.is_junction():raise ValueError('Redirected owner configuration')
        lease=SingleInstance(Path(connection['home'])/'execution.lock')
        temporary=path.with_name('.mcp-'+secrets.token_hex(8)+'.tmp')
        try:
            self.members.require_active(owner)
            text=path.read_text(encoding='utf-8-sig')
            servers=tomllib.loads(text).get('mcp_servers',{})
            if provider in servers:
                if not self.allowed(owner,provider,servers[provider]):raise PermissionError('Existing service differs; inspect without overwriting')
                return {'status':'existing_configuration_retained','provider':provider}
            values=connection['server']
            if set(values)!={'url'}:raise RuntimeError('Preset requires verified host setup')
            new=text+'\n[mcp_servers.'+provider+']\nurl = '+json.dumps(values['url'])+'\n'
            tomllib.loads(new)
            with temporary.open('x',encoding='utf-8') as out:out.write(new);out.flush();os.fsync(out.fileno())
            self.members.require_active(owner)
            if path.read_text(encoding='utf-8-sig')!=text:raise RuntimeError('Configuration changed during update')
            if not self.allowed(owner,provider,values):raise PermissionError('Preset permission changed')
            os.replace(temporary,path)
            return {'status':'configured','provider':provider,'login':'/login '+provider,'trust':'New tools retain their normal user approval requirements'}
        finally:temporary.unlink(missing_ok=True);lease.close()

    async def command(self,owner,text,request):
        self.members.require_active(owner)
        parts=text.split()
        if not parts or parts==['status']:
            return {'presets':[{'provider':name,'status':v['status'],'enabled':bool((self.db.execute('SELECT enabled FROM mcp_allowed WHERE provider=?',(name,)).fetchone() or [0])[0])} for name,v in self.catalog.items()]}
        if len(parts)==3 and parts[0]=='allow' and parts[2] in ('on','off'):
            self.set_allowed(owner,parts[1],parts[2]=='on')
            return {'provider':parts[1],'allowed':parts[2]=='on','runtime':'Authorization is rechecked before new work; existing account credentials are retained'}
        if len(parts)==2 and parts[0]=='connect':return self.connect(owner,parts[1])
        raise ValueError('Use /mcp status, /mcp allow <provider> on|off, or /mcp connect <provider>')
