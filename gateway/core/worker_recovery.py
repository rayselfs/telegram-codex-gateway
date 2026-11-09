"""Bound worker lifecycle integration; no shared process kill or task replay."""
import asyncio
from gateway.platforms.lifecycle import ChildJob
from gateway.core.rpc import Client
from gateway.core.managed_skills import extra_roots


class OfflineClient:
    failure='Worker not started'
    async def call(self,*args,**kwargs):
        raise RuntimeError('Current worker is offline')
    def close(self): pass


async def quiesce(item):
    c=item['controller']
    if c.busy or c.pending:
        c.unknown=True
        if c.thread: c.journal.job(c.thread,c.turn,'unknown')
    c.busy=False;c.pending.clear();c.stopping=False
    event=item.get('event_task')
    if event:
        event.cancel()
        await asyncio.gather(event,return_exceptions=True)
    await asyncio.to_thread(item['rpc'].close)
    if item.get('job'): item['job'].close()
    c.release_execution()


async def restore(item):
    c=item['controller']
    # Foreground and scheduled work continue to use the existing per-user lock.
    c.acquire_execution()
    rpc=None;job=None
    c.initializing=True
    try:
        authorize = c.authorize if item.get('members') else None
        rpc=Client(item['profile']['home'], authorize=authorize);job=ChildJob(rpc._proc)
        await asyncio.to_thread(rpc.initialize,'gateway_worker_recovery','Codex Gateway worker','1',{'experimentalApi':True})
        config=(await rpc.call('config/read',{}))['config']
        if config.get('features',{}).get('apps') is not False or config.get('mcp_oauth_credentials_store')!='file':
            raise RuntimeError('Per-user external account isolation unavailable')
        await rpc.call('skills/extraRoots/set',{'extraRoots':extra_roots()})
        old_rpc=c.rpc
        c.rpc=rpc;c.models=[]
        try:
            # Loading an idle thread is not replay. Unknown work remains blocked for inspection.
            await c.initialize(resume_thread=not c.unknown)
        except BaseException:
            c.rpc=old_rpc
            raise
        item['rpc']=rpc;item['job']=job
        item['event_task']=asyncio.create_task(c.events())
    except BaseException:
        try:
            if rpc: await asyncio.to_thread(rpc.close)
        finally:
            if job: job.close()
        raise
    finally:
        c.initializing=False
        c.release_execution()
