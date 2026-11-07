from bootstrap import CODE_ROOT
"""Idle-only code deployment and rollback. Database snapshots are evidence, never restored."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time
from bootstrap import ROOT, PYTHON, child_environment
from profiles import load_profiles
from lifecycle import SingleInstance
from health_file import write_json

SCHEDULE_UNRESOLVED = "SELECT 1 FROM runs WHERE state IN ('queued','running','unknown') OR notification IN ('ready','sending','unknown') UNION SELECT 1 FROM attachments WHERE state IN ('ready','sending','unknown')"
CHAT_UNRESOLVED = """SELECT 1 FROM jobs WHERE state IN ('running','dispatching','compacting')
 OR (state='unknown' AND (thread=COALESCE((SELECT json_extract(value,'$') FROM kv WHERE key='thread'),thread)))
 UNION SELECT 1 FROM delivery WHERE state='pending'"""

from gateway.core.releases import digest, checked_files, apply_package, rollback_package

def terminate_gateway(pid):
    # Request shutdown only through this deployment's authenticated local path.
    (ROOT/'profile/stop.request').write_text('idle code release',encoding='utf-8')
    for _ in range(200):
        try:
            lock=SingleInstance(ROOT/'profile/gateway.lock');lock.close();return
        except RuntimeError:time.sleep(.1)
    raise RuntimeError('Gateway has not stopped; preserve evidence and inspect')

def start_gateway():
    from gateway.platforms import current
    home=ROOT/'profile'
    (home/'stop.request').unlink(missing_ok=True)
    import os
    if os.name=='nt':
        from bootstrap import CONFIG
        from gateway.core.background import start
        if CONFIG is None:raise RuntimeError('Persisted deployment required')
        return start(CONFIG)
    with (home/'stdout.log').open('ab') as out,(home/'stderr.log').open('ab') as err:
        subprocess.Popen([str(PYTHON),'-B',str(CODE_ROOT/'gateway_guard.py')],cwd=CODE_ROOT,
            env=child_environment(),stdout=out,stderr=err,**current().process_options())

def deploy(source,names,*,rollback=None):
    rows=checked_files(source,names) if rollback is None else []
    home=ROOT/'profile';target=CODE_ROOT
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
        health=json.loads((home/'health.json').read_text(encoding='utf-8'))
        if time.time()-health['time']>10 or any(u['busy'] or u['unknown'] or u['login_active'] for u in health['users']):
            raise RuntimeError('Gateway not idle or health stale')
        write_json(evidence/'health-before.json',health)
        with hold.open('x',encoding='utf-8') as f:f.write('code release')
        owns_hold=True
        terminate_gateway(health['pid']);stopped=True
        leases.append(SingleInstance(home/'gateway.lock'))
        # A running guard observes the hold and exits instead of restarting the child.
        for _ in range(100):
            try:guard=SingleInstance(home/'guard.lock');guard.close();break
            except RuntimeError:time.sleep(.1)
        else:raise RuntimeError('Guard did not quiesce')
        if rollback is None:
            rows=apply_package(source,target,names,evidence/'package')
        else:
            rows=rollback_package(target,rollback/'package')
        write_json(evidence/'manifest.json',rows)
        copied=True
    finally:
        for lease in reversed(leases):lease.close()
        if owns_hold:hold.unlink(missing_ok=True)
        if stopped:
            start_gateway()
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
        deploy(None,None,rollback=a.rollback)
    else:deploy(a.source,a.files)
