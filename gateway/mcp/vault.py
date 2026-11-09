"""Local DPAPI credential storage. No model-facing plaintext retrieval tool."""
import base64
import json
import os
from pathlib import Path
import re
import stat
import time
import uuid
from gateway.platforms.lifecycle import SingleInstance

from gateway.core.bootstrap import ROOT as DATA_ROOT
ROOT=DATA_ROOT/'secrets'
OWNERS={'system':'system'}
# Numeric owners are supplied only by verified Gateway profile routing.

from gateway.platforms import current
crypt = current().crypt

class Vault:
    def __init__(self,owner,root=ROOT):
        self.owner=str(owner)
        if self.owner!='system' and (not self.owner.isascii() or not self.owner.isdigit() or int(self.owner)<=0):raise ValueError('Unknown credential owner')
        self.root=Path(root).absolute()
        self.folder=self.root/('system' if self.owner=='system' else 'users/'+self.owner)

    def path(self,name):
        if not isinstance(name,str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,62}',name):raise ValueError('Use a lowercase credential name with letters, digits and hyphens')
        path=self.folder/(name+'.json')
        for parent in (self.root,self.root/'users',self.folder,path):
            if parent.is_symlink() or parent.is_junction():raise ValueError('Credential paths must not be redirected')
        return path

    def put(self,name,value,purpose='',replace=False):
        target=self.path(name)
        if not isinstance(value,str) or not value or len(value.encode())>65536:raise ValueError('Credential must contain 1-65536 bytes')
        if not isinstance(purpose,str) or len(purpose)>160 or any(ord(c)<32 for c in purpose):raise ValueError('Use a short single-line purpose, never the credential itself')
        self.folder.mkdir(parents=True,exist_ok=True)
        lock=SingleInstance(self.folder/'.write.lock')
        temporary=self.folder/(uuid.uuid4().hex+'.tmp')
        try:
            if target.exists() and not replace:raise ValueError('Credential already exists; use explicit replace')
            record={'version':1,'owner':self.owner,'name':name,'purpose':purpose,'updated_at':int(time.time()),
                    'ciphertext':base64.b64encode(crypt(value.encode(),self.entropy(name),True)).decode()}
            with temporary.open('x',encoding='utf-8') as f:
                json.dump(record,f,ensure_ascii=False);f.flush();os.fsync(f.fileno())
            os.replace(temporary,target)
        finally:
            temporary.unlink(missing_ok=True);lock.close()

    def entropy(self,name):return ('Codex GatewayVault:v1:'+self.owner+':'+name).encode()

    def record(self,name):
        record=json.loads(self.path(name).read_text(encoding='utf-8'))
        if record.get('version')!=1 or record.get('owner')!=self.owner or record.get('name')!=name:raise ValueError('Credential owner/name mismatch')
        return record

    def read(self,name):
        # Trusted gateway adapters only: never expose this method as an LLM tool or generic command runner.
        record=self.record(name)
        return crypt(base64.b64decode(record['ciphertext'],validate=True),self.entropy(name),False).decode()

    def listing(self):
        self.path('path-check')
        result=[]
        for path in sorted(self.folder.glob('*.json')):
            record=self.record(path.stem)
            result.append({k:record[k] for k in ('name','purpose','updated_at')})
        return result

    def delete(self,name):
        target=self.path(name)
        if not target.exists():raise ValueError('Credential does not exist')
        lock=SingleInstance(self.folder/'.write.lock')
        try:target.unlink()
        finally:lock.close()

def main():
    import argparse,getpass
    p=argparse.ArgumentParser();p.add_argument('action',choices=['set','list','remove'])
    p.add_argument('--user',required=True,choices=list(OWNERS));p.add_argument('--name');p.add_argument('--purpose',default='');p.add_argument('--replace',action='store_true')
    a=p.parse_args();vault=Vault(OWNERS[a.user])
    if a.action=='list':print(json.dumps(vault.listing(),ensure_ascii=False));return
    vault.path(a.name)
    if a.action=='set':
        import sys
        if not sys.stdin.isatty():raise ValueError('Use an interactive local terminal; no chat or redirected input')
        value=getpass.getpass('Secret (hidden): ')
        vault.put(a.name,value,a.purpose,a.replace);value=None
        print('Saved encrypted. Existing service logins were not changed.')
    else:
        if input('Type the credential name to delete: ')!=a.name:raise ValueError('Cancelled')
        vault.delete(a.name);print('Removed. Revoke the credential at its provider if needed.')

if __name__=='__main__':
    try:main()
    except Exception as error:
        print(type(error).__name__+': Credential operation failed; verify owner/name and local access. No secret was printed.')
        raise SystemExit(1)
