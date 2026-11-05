"""Idle-only code deployment and rollback. Database snapshots are evidence, never restored."""
import argparse
import ctypes
from ctypes import wintypes as w
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time
from bootstrap import ROOT
from profiles import load_profiles
from lifecycle import SingleInstance
from health_file import write_json

SCHEDULE_UNRESOLVED = "SELECT 1 FROM runs WHERE state IN ('queued','running','unknown') OR notification IN ('ready','sending','unknown') UNION SELECT 1 FROM attachments WHERE state IN ('ready','sending','unknown')"
CHAT_UNRESOLVED = """SELECT 1 FROM jobs WHERE state IN ('running','dispatching','compacting')
 OR (state='unknown' AND (thread=COALESCE((SELECT json_extract(value,'$') FROM kv WHERE key='thread'),thread)))
 UNION SELECT 1 FROM delivery WHERE state='pending'"""

def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None

def checked_files(source,names):
    rows=[]
    for name in names:
        p=Path(name)
        if p.name!=name or p.suffix not in {'.py','.ps1','.mjs','.vbs','.md','.json'}:
            raise ValueError('Only named top-level code files allowed')
        path=source/name
        if not path.is_file() or path.is_symlink():raise ValueError('Missing regular release file')
        if p.suffix=='.py':compile(path.read_text(encoding='utf-8-sig'),name,'exec')
        rows.append({'name':name,'after':digest(path)})
    if not rows:raise ValueError('Empty release')
    return rows

def terminate_gateway(pid):
    k=ctypes.WinDLL('kernel32',use_last_error=True)
    k.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];k.OpenProcess.restype=w.HANDLE
    k.QueryFullProcessImageNameW.argtypes=[w.HANDLE,w.DWORD,w.LPWSTR,ctypes.POINTER(w.DWORD)]
    k.TerminateProcess.argtypes=[w.HANDLE,w.UINT];k.WaitForSingleObject.argtypes=[w.HANDLE,w.DWORD];k.CloseHandle.argtypes=[w.HANDLE]
    handle=k.OpenProcess(0x1000|1|0x100000,False,pid)
    if not handle:raise RuntimeError('Gateway process unavailable')
    try:
        buf=ctypes.create_unicode_buffer(32768);size=w.DWORD(len(buf))
        if not k.QueryFullProcessImageNameW(handle,0,buf,ctypes.byref(size)):raise RuntimeError('Cannot identify Gateway')
        if Path(buf.value).resolve()!= (ROOT/'runtime/python/python.exe').resolve():raise RuntimeError('Wrong Gateway executable')
        if not k.TerminateProcess(handle,0) or k.WaitForSingleObject(handle,10000)!=0:raise RuntimeError('Gateway did not exit')
    finally:k.CloseHandle(handle)

def deploy(source,names):
    rows=checked_files(source,names);home=ROOT/'profile';target=ROOT/'bridge'
    evidence=ROOT/'evidence'/('release-'+str(time.time_ns()));evidence.mkdir(parents=True)
    leases=[];stopped=False;copied=False;hold=home/'maintenance.request';owns_hold=False
    try:
        leases.append(SingleInstance(home/'release.lock'))
        for p in load_profiles(home/'users.json'):
            for lock in ('execution.lock','schedules/worker.lock'):leases.append(SingleInstance(p['home']/lock))
            # Historical unknowns remain evidence; only the selected thread is resumed at startup.
            for relative,query in [('transport.sqlite',CHAT_UNRESOLVED),
                                   ('schedules/schedules.sqlite',SCHEDULE_UNRESOLVED)]:
                with sqlite3.connect((p['home']/relative).as_uri()+'?mode=ro',uri=True) as db:
                    if db.execute(query).fetchone():raise RuntimeError('Owner has unresolved work')
                    with sqlite3.connect(evidence/(str(p['owner_id'])+'-'+Path(relative).name)) as backup:db.backup(backup)
        leases.append(SingleInstance(home/'line-browser.lock'))
        health=json.loads((home/'health.json').read_text(encoding='utf-8'))
        if time.time()-health['time']>10 or any(u['busy'] or u['unknown'] or u['login_active'] for u in health['users']):
            raise RuntimeError('Gateway not idle or health stale')
        for row in rows:
            row['before']=digest(target/row['name'])
            if row['before'] is not None:
                (evidence/'before').mkdir(exist_ok=True);shutil.copy2(target/row['name'],evidence/'before'/row['name'])
        write_json(evidence/'manifest.json',rows);write_json(evidence/'health-before.json',health)
        with hold.open('x',encoding='utf-8') as f:f.write('code release')
        owns_hold=True
        terminate_gateway(health['pid']);stopped=True
        leases.append(SingleInstance(home/'gateway.lock'))
        # A running guard observes the hold and exits instead of restarting the child.
        for _ in range(100):
            try:guard=SingleInstance(home/'guard.lock');guard.close();break
            except RuntimeError:time.sleep(.1)
        else:raise RuntimeError('Guard did not quiesce')
        for row in rows:
            if digest(source/row['name'])!=row['after'] or digest(target/row['name'])!=row['before']:raise RuntimeError('Concurrent code change')
        for row in rows:shutil.copyfile(source/row['name'],target/row['name'])
        copied=True
        for row in rows:
            if digest(target/row['name'])!=row['after']:raise RuntimeError('Copy verification failed')
    except Exception:
        if stopped:
            for row in rows:
                old=evidence/'before'/row['name']
                if old.exists():shutil.copyfile(old,target/row['name'])
        raise
    finally:
        for lease in reversed(leases):lease.close()
        if owns_hold:hold.unlink(missing_ok=True)
        if stopped:
            subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-File',str(target/'Start.ps1')],
                check=True,creationflags=subprocess.CREATE_NO_WINDOW,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    if copied:
        for _ in range(50):
            try:
                health=json.loads((home/'health.json').read_text(encoding='utf-8'))
                if health.get('pid') and health['pid']!=json.loads((evidence/'health-before.json').read_text(encoding='utf-8'))['pid'] and health.get('telegram')=='ready' and health.get('codex')=='ready':
                    write_json(evidence/'health-after.json',health);print(evidence);return
            except (OSError,ValueError):pass
            time.sleep(1)
        raise RuntimeError('Release health unconfirmed; preserve evidence and inspect before rollback')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path);p.add_argument('--files',nargs='+');p.add_argument('--rollback',type=Path);a=p.parse_args()
    if a.rollback:
        rows=json.loads((a.rollback/'manifest.json').read_text(encoding='utf-8'))
        deploy(a.rollback/'before',[r['name'] for r in rows if r['before'] is not None])
    else:deploy(a.source,a.files)
