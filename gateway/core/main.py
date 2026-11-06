"""Entry point. Only this process receives the independent home settings."""
import argparse
import asyncio
import hashlib
import json
import logging
from pathlib import Path
import subprocess
import sys
import time
import os
from health_file import write_json
from bootstrap import isolate, ROOT, CODEX


def verify_install():
    lock = json.loads((ROOT / 'bridge/versions.json').read_text(encoding='utf-8-sig'))
    if hashlib.sha256(CODEX.read_bytes()).hexdigest() != lock['codex_sha256']:
        raise RuntimeError('Codex binary changed; rerun acceptance before starting')
    from importlib.metadata import version
    for package, expected in lock['python_packages'].items():
        if version(package) != expected:
            raise RuntimeError('Gateway dependency changed; rerun acceptance')


async def serve(home):
    from rpc import Client
    from transport import Transport
    from controller import Controller
    from state import Journal
    from profiles import load_profiles
    from lifecycle import SingleInstance, ChildJob, decrypt_token
    from telegram.error import BadRequest
    from oauth_login import ServiceLogin
    from schedules import Schedules, ScheduleUI
    from schedule_delivery import deliver as deliver_schedules
    from availability import Availability
    from worker_supervisor import WorkerSupervisor
    from worker_recovery import OfflineClient, quiesce, restore
    verify_install()
    profiles = load_profiles(home / 'users.json')
    token = decrypt_token(home / 'telegram-token.dpapi')
    instance = SingleInstance(home / 'gateway.lock')
    stop = home / 'stop.request'
    stop.unlink(missing_ok=True)
    resources, tasks = [], []
    connection = None
    started_at = time.time()
    try:
        for profile in profiles:
            item = {'profile': profile}
            resources.append(item)
            rpc = item['rpc'] = OfflineClient()
            item['job'] = None
            tg = item['tg'] = Transport(token, profile['owner_id'], profile['home'] / 'inbox')
            tg.display_name = profile['name']
            journal = item['journal'] = Journal(profile['home'] / 'transport.sqlite')
            controller = item['controller'] = Controller(rpc, tg, journal, profile['workspace'],
                personal_instructions=profile['home']/'codex-home/AGENTS.md')
            from autonomy_policy import configure
            item['skills_store']=configure(controller,profile['owner_id'])
            controller.execution_lock_path = profile['home'] / 'execution.lock'
            item['availability'] = Availability(tg, journal)
            schedules = item['schedules'] = Schedules(profile['home'] / 'schedules/schedules.sqlite')
            with schedules.db:
                schedules.db.execute("UPDATE runs SET notification='unknown' WHERE notification='sending'")
                schedules.db.execute("UPDATE attachments SET state='unknown' WHERE state='sending'")
            controller.schedule_ui = ScheduleUI(schedules, controller)
            tg.login = ServiceLogin(tg, profile['home'])
            # Cold startup honors persisted failure gates without spending budget on success.
        connection = resources[0]['tg']
        connection.peers = {r['profile']['owner_id']: r['tg'] for r in resources}
        # shortcut: private reconnect flag preserves pending updates; revalidate on Hermes upgrades.
        if not await connection.connect(is_reconnect=True):
            raise RuntimeError('Telegram connection failed')
        for item in resources:
            tg, controller = item['tg'], item['controller']
            tg._bot = connection._bot
            tg.connection = connection
            try:
                await tg.install_menu()
            except BadRequest as error:
                if 'chat not found' not in str(error).lower():
                    raise
                # A new owner may not have pressed Start on this Bot yet.
            item['event_task'] = None
            item['supervisor'] = WorkerSupervisor(item['journal'],
                lambda item=item: quiesce(item), lambda item=item: restore(item))
            from self_help import SelfHelp
            controller.self_help=SelfHelp(controller,item['supervisor'])
            item['recovery_task']=asyncio.create_task(item['supervisor'].start())
            tasks.extend([asyncio.create_task(controller.sender()),
                          asyncio.create_task(deliver_schedules(item['schedules'], tg)),
                          asyncio.create_task(tg.typing_loop())])
        while not stop.exists():
            users = [{'name': r['profile']['name'], 'codex': 'offline' if r['rpc'].failure else 'ready',
                      'busy': r['controller'].busy, 'unknown': r['controller'].unknown,
                      'seen': r['tg'].seen_update, 'login_active': r['tg'].login.active} for r in resources]
            status = {'time': time.time(), 'pid': os.getpid(), 'started_at': started_at,
                      'poll_failures': connection.poll_failures, 'last_poll_error': connection.last_poll_error,
                      'poll_age_seconds': (round(time.monotonic()-connection._polling_last_progress_monotonic,1)
                          if connection._polling_last_progress_monotonic else None), 'telegram': connection.health(),
                      'codex': 'ready' if all(u['codex']=='ready' for u in users) else 'offline',
                      'busy': any(u['busy'] for u in users),
                      'login_active': any(u['login_active'] for u in users),
                      'unknown': any(u['unknown'] for u in users), 'users': users}
            write_json(home/'health.json',status)
            await asyncio.gather(*(r['availability'].update(
                not r['rpc'].failure, 'Codex worker 連線異常。') for r in resources))
            for item in resources:
                recovery_task=item.get('recovery_task')
                if recovery_task is None or recovery_task.done():
                    if recovery_task:
                        await recovery_task
                    worker_failed=bool(item['rpc'].failure) or bool(item['event_task'] and item['event_task'].done())
                    if (item['controller'].busy or item['controller'].pending) and not worker_failed:
                        continue
                    item['recovery_task']=asyncio.create_task(item['supervisor'].tick(
                        failed=worker_failed))
            if any(t.done() for t in tasks):
                raise RuntimeError('Gateway worker stopped; no fallback started')
            await asyncio.sleep(1)
    finally:
        await asyncio.gather(*(r['availability'].update(False, '服務正在停止。', force=True)
                              for r in resources if r.get('availability') and hasattr(r.get('tg'), '_bot')),
                             return_exceptions=True)
        for item in resources:
            for name in ('recovery_task','event_task'):
                task=item.get(name)
                if task:
                    task.cancel()
                    await asyncio.gather(task,return_exceptions=True)
            if item.get('controller'):
                item['controller'].closed = True
            if item.get('tg') and item['tg'].voice_task:
                item['tg'].voice_task.cancel()
                await asyncio.gather(item['tg'].voice_task, return_exceptions=True)
            if item.get('tg') and item['tg'].login:
                await item['tg'].login.close()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if connection:
            try:
                await asyncio.wait_for(connection.disconnect(), timeout=20)
            except Exception:
                pass
        for item in reversed(resources):
            if item.get('rpc'):
                await asyncio.to_thread(item['rpc'].close)
            if item.get('job'):
                item['job'].close()
            if item.get('controller'):
                item['controller'].release_execution()
            if item.get('schedules'):
                item['schedules'].close()
            if item.get('journal'):
                item['journal'].close()
            if item.get('skills_store'):
                item['skills_store'].close()
        instance.close()
        write_json(home/'health.json',{'time':time.time(),'telegram':'offline','codex':'offline'})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['serve', 'login', 'check'])
    parser.add_argument('--owner', type=int)
    args = parser.parse_args()
    home = isolate()
    # No raw Hermes/httpx exception logs: Telegram URLs embed credentials.
    logging.disable(logging.CRITICAL)
    verify_install()
    if args.action != 'serve' and (home / 'users.json').exists():
        from profiles import load_profiles
        profiles = load_profiles(home / 'users.json')
        if args.owner is None:raise ValueError('--owner Telegram numeric ID is required for login/check')
        home = next(p['home'] for p in profiles if p['owner_id'] == args.owner)
    if args.action == 'login':
        import os
        env = dict(os.environ, CODEX_HOME=str(home / 'codex-home'),
                   CODEX_SQLITE_HOME=str(home / 'codex-home'))
        result = subprocess.run([str(CODEX), 'login', '--device-auth'], env=env)
        return result.returncode
    if args.action == 'check':
        from rpc import Client
        with Client(home) as rpc:
            rpc.initialize(client_name='codex_telegram_gateway', capabilities={'experimentalApi': True})
            account = rpc.request('account/read', {'refreshToken': False})
            print(json.dumps({'codex': '0.166.0', 'home': str(home),
                'auth': (account.get('account') or {}).get('type'),
                'token_present': (ROOT / 'profile/telegram-token.dpapi').exists()}))
        return 0
    asyncio.run(serve(home))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as error:
        # Deliberately exclude exception contents and tracebacks from unattended logs.
        print('Gateway stopped: ' + type(error).__name__ + '. Run Check.ps1 and consult acceptance report.')
        sys.exit(1)
