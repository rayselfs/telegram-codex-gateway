from bootstrap import CODE_ROOT
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
    lock = json.loads((CODE_ROOT / 'versions.json').read_text(encoding='utf-8-sig'))
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
    from gateway.core.members import Members
    if not (home/'members.sqlite').exists():
        raise RuntimeError('Member registry migration or init required')
    registry = Members(home/'members.sqlite', ROOT)
    from gateway.core.shared_policy import SharedPolicy
    shared_policy = SharedPolicy(registry)
    if not registry.db.execute('SELECT 1 FROM shared_versions').fetchone():
        shared_policy.import_initial('', 'admin_only')
    from gateway.core.capabilities import Capabilities, CapabilityTools
    capabilities = Capabilities(registry)
    from gateway.mcp.presets import Presets
    mcp_policy=Presets(registry)
    profiles = registry.active_profiles()
    token = decrypt_token(home / 'telegram-token.dpapi')
    instance = SingleInstance(home / 'gateway.lock')
    from gateway.platforms import current
    try: bot_instance=current().BotInstance(token)
    except BaseException:
        instance.close();registry.close();raise
    stop = home / 'stop.request'
    stop.unlink(missing_ok=True)
    resources, tasks = [], []
    connection = None
    started_at = time.time()
    def build(profile):
        item = {'profile': profile, 'members': registry}
        resources.append(item)
        rpc = item['rpc'] = OfflineClient()
        item['job'] = None
        tg = item['tg'] = Transport(token, profile['owner_id'], profile['home'] / 'inbox')
        tg.members = registry
        tg.display_name = profile['name']
        journal = item['journal'] = Journal(profile['home'] / 'transport.sqlite')
        controller = item['controller'] = Controller(rpc, tg, journal, profile['workspace'],
            personal_instructions=profile['home']/'codex-home/AGENTS.md')
        from autonomy_policy import configure
        item['skills_store']=configure(controller,profile['owner_id'])
        controller.shared_policy = shared_policy
        if capabilities.handlers: controller.capability_tools = CapabilityTools(capabilities,profile['owner_id'])
        controller.authorize = lambda owner=profile['owner_id']: mcp_policy.authorize_runtime(owner)
        controller.execution_lock_path = profile['home'] / 'execution.lock'
        item['availability'] = Availability(tg, journal)
        schedules = item['schedules'] = Schedules(profile['home'] / 'schedules/schedules.sqlite',members=registry,owner=profile['owner_id'],capabilities=capabilities)
        with schedules.db:
            schedules.db.execute("UPDATE runs SET notification='unknown' WHERE notification='sending'")
            schedules.db.execute("UPDATE attachments SET state='unknown' WHERE state='sending'")
        controller.schedule_ui = ScheduleUI(schedules, controller)
        tg.login = ServiceLogin(tg, profile['home'],policy=mcp_policy)
        # Cold startup honors persisted failure gates without spending budget on success.
        return item

    async def start_item(item):
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

    attach_failed=set()
    async def attach(row):
        owner = row['owner_id']
        if owner in connection.peers: return
        from bootstrap import CONFIG
        if (home/'background-installed').exists():
            if CONFIG is None:raise RuntimeError('Background setup requires a persistent deployment configuration')
            from gateway.core.background import definitions
            try:await asyncio.to_thread(current().install_background,definitions(CONFIG,[row],owner=owner))
            except Exception:
                attach_failed.add(owner)
                raise RuntimeError('Owner background registration needs retry; existing workers remain active') from None
        attach_failed.discard(owner)
        profile = {'owner_id':owner,'name':row['display_name'], 'home':Path(row['home']),'workspace':Path(row['workspace'])}
        item = build(profile)
        connection.peers[owner] = item['tg']
        await start_item(item)

    try:
        for profile in profiles:
            build(profile)
        connection = resources[0]['tg'] if resources else Transport(token, 1, home/'registration-inbox')
        connection.members = registry
        connection.peers = {r['profile']['owner_id']: r['tg'] for r in resources}
        # shortcut: private reconnect flag preserves pending updates; revalidate on Hermes upgrades.
        if not await connection.connect(is_reconnect=True):
            raise RuntimeError('Telegram connection failed')
        from gateway.core.member_relay import RelayRegistration as Registration
        from gateway.core.onboarding import login, check
        async def send(owner, text):
            receipts=[]
            for offset in range(0, max(1,len(text)), 3500):
                message=await connection._bot.send_message(chat_id=owner, text=text[offset:offset+3500] or '（空白）')
                receipts.append(message.message_id)
            return receipts
        connection.registration = Registration(registry, send,
            lambda row: login(row, send, registry), lambda row: check(row, registry), attach, connection._bot.username, shared_policy=shared_policy)
        connection.registration.extensions['/mcp']=mcp_policy.command
        for item in resources:
            await start_item(item)
        while not stop.exists():
            for profile in registry.active_profiles():
                if profile['owner_id'] not in connection.peers and profile['owner_id'] not in attach_failed:
                    try:await attach(registry.require_active(profile['owner_id']))
                    except Exception:attach_failed.add(profile['owner_id'])
            users = [{'name': r['profile']['name'], 'codex': 'offline' if r['rpc'].failure else 'ready',
                      'busy': r['controller'].busy, 'unknown': r['controller'].unknown,
                      'seen': r['tg'].seen_update, 'login_active': r['tg'].login.active} for r in resources]
            status = {'time': time.time(), 'pid': os.getpid(), 'started_at': started_at,
                      'poll_failures': connection.poll_failures, 'last_poll_error': connection.last_poll_error,
                      'poll_age_seconds': (round(time.monotonic()-connection._polling_last_progress_monotonic,1)
                          if connection._polling_last_progress_monotonic else None), 'telegram': connection.health(),
                      'codex': 'ready' if users and all(u['codex']=='ready' for u in users) else 'offline',
                      'busy': any(u['busy'] for u in users),
                      'login_active': any(u['login_active'] for u in users),
                      'unknown': any(u['unknown'] for u in users), 'users': users}
            write_json(home/'health.json',status)
            await asyncio.gather(*(r['availability'].update(
                not r['rpc'].failure, 'Codex worker 連線異常。') for r in resources))
            for item in resources:
                try:
                    registry.require_active(item['profile']['owner_id'])
                except PermissionError:
                    if not item.get('suspended'):
                        recovery_task=item.get('recovery_task')
                        if recovery_task:
                            recovery_task.cancel(); await asyncio.gather(recovery_task, return_exceptions=True)
                        await quiesce(item)
                        item['suspended']=True
                    continue
                if item.pop('suspended',False):
                    item['recovery_task']=asyncio.create_task(restore(item))
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
            if getattr(connection,'registration',None):await connection.registration.close()
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
        bot_instance.close()
        registry.close()
        write_json(home/'health.json',{'time':time.time(),'telegram':'offline','codex':'offline'})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['serve', 'login', 'check', 'migrate-members', 'pairing-status', 'confirm-bootstrap', 'activate-bootstrap'])
    parser.add_argument('--owner', type=int)
    parser.add_argument('--claim')
    args = parser.parse_args()
    home = isolate()
    from gateway.core.members import Members
    from gateway.core.init import Bootstrap
    if args.action == 'migrate-members':
        from profiles import load_profiles
        registry=Members(home/'members.sqlite',ROOT)
        from gateway.core.migration import migrate
        try: migrate(registry,load_profiles(home/'users.json'),args.owner)
        finally: registry.close()
        print('Member registry imported; existing runtime data untouched')
        return 0
    if args.action in {'pairing-status','confirm-bootstrap','activate-bootstrap'}:
        registry=Members(home/'members.sqlite',ROOT)
        try:
            pairing=Bootstrap(registry)
            if args.action=='pairing-status':
                row=registry.db.execute('SELECT claim_id,owner,confirmed,consumed FROM bootstrap').fetchone()
                print(json.dumps(dict(row) if row else {}))
            elif args.action=='confirm-bootstrap':
                pairing.confirm_bootstrap(args.claim,args.owner)
            else:
                from gateway.core.onboarding import check
                row=registry.db.execute('SELECT owner FROM bootstrap WHERE claim_id=?',(args.claim,)).fetchone()
                if not row: raise PermissionError('Unknown pairing claim')
                ready=asyncio.run(check(registry.require_onboarding(row['owner']),registry))
                pairing.activate(args.claim,lambda _:ready)
        finally: registry.close()
        return 0
    # No raw Hermes/httpx exception logs: Telegram URLs embed credentials.
    logging.disable(logging.CRITICAL)
    verify_install()
    if args.action != 'serve':
        if args.owner is None: raise ValueError('--owner is required')
        from gateway.core.onboarding import authorize
        registry=Members(home/'members.sqlite',ROOT)
        try: home=Path(authorize(registry,args.owner)['home'])
        finally: registry.close()
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
