"""Windows launches one short-lived worker per owner; Telegram is never polled here."""
import argparse
import asyncio
import json
import time
from pathlib import Path
from bootstrap import isolate, ROOT


async def execute(profile, run, store, *, capture=False, members=None, capture_prompt=None):
    policy=None
    if members is not None:
        from gateway.mcp.presets import Presets
        policy=Presets(members);policy.authorize_runtime(profile['owner_id'],scheduled=True)
    from controller import Controller
    from rpc import Client
    from lifecycle import ChildJob
    from state import Journal

    class Output:
        def __init__(self):
            self.messages = []
            self.display_name = profile['name']
        async def say(self, text):
            self.messages.append(text)
            return []

    class BackgroundController(Controller):
        blocked = False
        async def server_request(self, request):
            if self.auto_consent(request):return
            self.blocked = True
            self.rpc.respond_error(request['id'], -32000, 'Unattended task needs user action; stopped without approval')
            if self.thread and self.turn:
                await self.rpc.call('turn/interrupt', {'threadId':self.thread,'turnId':self.turn})

    folder = profile['home'] / 'schedules' / 'runs' / run['id']
    folder.mkdir(parents=True,exist_ok=False)
    rpc = Client(profile['home'], browser=False, authorize=(lambda: policy.authorize_runtime(profile['owner_id'],scheduled=True)) if policy else None); job = ChildJob(rpc._proc)
    journal = Journal(folder/'transport.sqlite'); output = Output()
    c = BackgroundController(rpc,output,journal,profile['workspace'],
        personal_instructions=profile['home']/'codex-home/AGENTS.md')
    from autonomy_policy import configure
    shared_store=configure(c,profile['owner_id'])
    c.interactive = False
    if members is not None:
        from gateway.core.shared_policy import SharedPolicy
        c.shared_policy = SharedPolicy(members)
        c.authorize = lambda: policy.authorize_runtime(profile['owner_id'],scheduled=True)
    import sqlite3
    with sqlite3.connect((profile['home']/'transport.sqlite').as_uri()+'?mode=ro', uri=True) as personal:
        memory = personal.execute("SELECT value FROM kv WHERE key='personal_memories'").fetchone()
        if memory:
            journal.set('personal_memories',json.loads(memory[0]))
    c.model,c.effort=run['model'],run['effort']
    events = None
    try:
        await asyncio.to_thread(rpc.initialize, 'gateway_scheduled_task', 'Codex Gateway scheduled task', '1', {'experimentalApi':True})
        config=(await rpc.call('config/read',{}))['config']
        if config.get('features',{}).get('apps') is not False or config.get('mcp_oauth_credentials_store')!='file':
            raise RuntimeError('User account isolation unavailable')
        from managed_skills import extra_roots
        await rpc.call('skills/extraRoots/set',{'extraRoots':extra_roots()})
        await c.initialize()
        spec=json.loads(run['spec'])
        prompt=('這是本人已確認的單次排程執行。只完成以下工作，不建立或修改任何排程；'
                '不要自行傳 Telegram 訊息，結果由 Gateway 交付。需要額外批准、登入或問題回答時停止並說明。'
                '輸出存本人的工作區，回報相對路徑；不得更動另一使用者資料。\n'
                '任務：'+spec['name']+'\n工作內容：'+spec['content'])
        if capture_prompt:prompt += '\n'+capture_prompt
        await c.start([{'type':'text','text':prompt}])
        (folder/'native.json').write_text(json.dumps({'thread':c.thread,'turn':c.turn}),encoding='utf-8')
        events=asyncio.create_task(c.events())
        deadline=time.monotonic()+300
        while c.busy and not c.unknown and not rpc.failure and time.monotonic()<deadline:
            if events.done():
                await events
                break
            await asyncio.sleep(.1)
        if c.busy or c.unknown or rpc.failure:
            if c.turn and not rpc.failure:
                await rpc.call('turn/interrupt',{'threadId':c.thread,'turnId':c.turn})
            if capture:return 'unknown','生成逾時或連線中斷，未發送。'
            store.finish(run['id'],'unknown','執行逾時或連線中斷；未重跑，請先核對工作區結果。')
            return
        status=journal.db.execute('SELECT state FROM jobs WHERE thread=?',(c.thread,)).fetchone()[0]
        await c.deliver_result(c.thread,c.turn,status=status)
        state='blocked' if c.blocked else status
        result=(output.messages[-1] if output.messages else '') if status=='completed' else '\n\n'.join(output.messages)
        if c.blocked:result='需要本人處理批准或登入；本次已停止，沒有自動重跑。\n'+result
        if capture:return state,'\n'.join(output.messages)
        store.finish(run['id'],state,result or 'Codex 未提供文字結果。')
    finally:
        c.closed=True
        if events:
            events.cancel()
            await asyncio.gather(events,return_exceptions=True)
        await asyncio.to_thread(rpc.close)
        job.close();journal.close();shared_store.close()


async def execute_capability(profile,run,store,capabilities):
    spec=json.loads(run['spec'])
    try:
        store.authorize(spec,scheduled=True)
        if capabilities is None:raise PermissionError('Capability unavailable')
        result=await capabilities.dispatch(profile['owner_id'],spec['capability'],spec['action'],
            spec['arguments'],'schedule:'+run['id'])
        store.finish(run['id'],'completed','[SILENT]' if isinstance(result,dict) and result.get('silent') is True else json.dumps(result,ensure_ascii=False))
    except PermissionError:
        store.finish(run['id'],'blocked','Current authorization does not permit execution')
    except Exception as error:
        store.finish(run['id'],'unknown','Capability result unconfirmed: '+type(error).__name__+'. No automatic retry.')


async def tick(profile, members=None, capabilities=None):
    if members is not None: members.require_schedule(profile['owner_id'])
    from schedules import Schedules
    from lifecycle import SingleInstance
    base=profile['home']/'schedules';base.mkdir(exist_ok=True)
    try:worker_lock=SingleInstance(base/'worker.lock')
    except RuntimeError:return 'worker_busy'
    store=Schedules(base/'schedules.sqlite',members=members,owner=profile['owner_id'],capabilities=capabilities)
    try:
        store.recover();store.enqueue()
        # The same kernel-backed lock is held throughout foreground turns.
        try:lease=SingleInstance(profile['home']/'execution.lock')
        except RuntimeError:
            with store.db:store.db.execute("UPDATE runs SET result='等待本人的前景工作釋放；到期超過設定期限則跳過。' WHERE state='queued'")
            return 'foreground_busy'
        try:
            if members is not None: members.require_schedule(profile['owner_id'])
            run=store.claim()
            if not run:return 'idle'
            spec=json.loads(run['spec'])
            store.authorize(spec,scheduled=True)
            if spec['kind']=='capability':
                await execute_capability(profile,run,store,capabilities)
            elif spec['kind']=='reminder':
                store.finish(run['id'],'completed',spec['content'])
            elif spec['kind']=='codex':
                try:await execute(profile,run,store,members=members)
                except Exception as error:
                    store.finish(run['id'],'unknown','執行未確認：'+type(error).__name__+'。未自動重試。')
            else:
                store.finish(run['id'],'blocked','Task kind is not installed; no inference started.')
            return 'processed'
        finally:lease.close()
    finally:
        store.close();worker_lock.close()


def main():
    p=argparse.ArgumentParser();p.add_argument('--owner',required=True,type=int);a=p.parse_args()
    isolate()
    from main import verify_install
    from profiles import load_profiles
    verify_install()
    from gateway.core.members import Members
    if not (ROOT/'profile/members.sqlite').exists(): raise RuntimeError('Member registry required')
    members=Members(ROOT/'profile/members.sqlite',ROOT)
    members.require_schedule(a.owner)
    rows=members.active_profiles()
    profile=next((r for r in rows if r['owner_id']==a.owner),None)
    if profile is None:raise ValueError('Unknown owner')
    from gateway.core.capabilities import Capabilities
    capabilities=Capabilities(members)
    services=None
    try:
        result=asyncio.run(tick(profile,members,capabilities))
    finally:
        if services:services.close()
        members.close()
    (profile['home']/'schedules/worker-health.json').write_text(json.dumps({'time':time.time(),'state':result}),encoding='utf-8')


if __name__=='__main__':
    try:main()
    except Exception as error:
        print('Scheduled worker failed: '+type(error).__name__)
        raise SystemExit(1)
