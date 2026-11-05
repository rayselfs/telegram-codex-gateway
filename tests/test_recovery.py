import asyncio
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch
from test_gateway import FakeRPC, directory
from types import SimpleNamespace as NS
from controller import Controller
from state import Journal
from recovery import PLAN, SOL, PROMPT

ERROR={'codexErrorInfo':'serverOverloaded'}

class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.root=directory()
        self.j=Journal(self.root/'state.sqlite')
        self.rpc=FakeRPC(self.root)
        self.tg=NS(say=AsyncMock(return_value=[1]),buttons=AsyncMock(),health=lambda:'ready')
        self.c=Controller(self.rpc,self.tg,self.j,self.root)
        self.c.thread='thread1';self.c.model=SOL;self.c.effort='high'
        self.c.models=[{'model':m} for m in set(PLAN)]
        self.j.set('model',SOL);self.j.set('thread','thread1')
        self.latest=None
        original=self.rpc.call
        async def call(method,params):
            if method=='thread/turns/list':return {'data':[self.latest]}
            return await original(method,params)
        self.rpc.call=call

    async def asyncTearDown(self):self.c.closed=True;self.j.close()

    def fail(self,n,error=ERROR):
        self.latest={'id':f'failed-{n}','status':'failed','error':error}
        self.c.turn=self.latest['id'];self.c.busy=False
        self.j.job(self.c.thread,self.c.turn,'failed')
        return self.c.recovery.schedule(self.latest)

    async def due(self):
        with patch('recovery.time.time',return_value=self.c.recovery.state['due']):
            await self.c.recovery.tick()

    async def test_exact_five_attempts_then_stop_default_unchanged(self):
        for i,model in enumerate(PLAN):
            self.assertTrue(self.fail(i))
            before=len(self.rpc.calls)
            await self.c.recovery.tick()
            self.assertEqual(len(self.rpc.calls),before)
            await self.due()
            params=[p for m,p in self.rpc.calls if m=='turn/start'][-1]
            self.assertEqual(params['model'],model)
            self.assertEqual(params['collaborationMode']['settings']['model'],model)
            self.assertEqual(params['effort'],'high')
            self.assertEqual(params['input'],[{'type':'text','text':PROMPT}])
            self.assertIn('不要重新建立 Canva 設計',params['collaborationMode']['settings']['developer_instructions'])
        self.assertFalse(self.fail(5))
        self.assertEqual(len([1 for m,p in self.rpc.calls if m=='turn/start']),5)
        self.assertEqual(self.j.get('model'),SOL)
        await self.c.start([{'type':'text','text':'new work'}])
        self.assertEqual([p for m,p in self.rpc.calls if m=='turn/start'][-1]['model'],SOL)

    async def test_non_capacity_stop_and_missing_model_never_retry(self):
        for error in ({'codexErrorInfo':'usageLimitExceeded'},{'message':'at capacity'},{'codexErrorInfo':'unauthorized'},None):
            self.assertFalse(self.fail(1,error))
        self.c.stopping=True
        self.assertFalse(self.fail(2))
        self.c.stopping=False;self.c.models=[]
        self.assertFalse(self.fail(3))
        self.c.models=[{'model':m} for m in PLAN];self.c.interactive=False
        self.assertFalse(self.fail(4))

    async def test_stop_during_wait_prevents_start(self):
        self.fail(1)
        await self.c.handle('/stop')
        await self.c.recovery.tick()
        self.assertFalse(self.c.busy)
        self.assertIsNone(self.j.get('recovery'))
        self.assertNotIn('turn/start',[m for m,p in self.rpc.calls])

    async def test_changed_turn_and_ambiguous_dispatch_stop_without_replay(self):
        self.fail(1);self.latest['id']='other-turn'
        await self.due()
        self.assertTrue(self.c.unknown)
        self.assertNotIn('turn/start',[m for m,p in self.rpc.calls])
        self.c.unknown=False;self.fail(2)
        async def call(method,params):
            if method=='thread/turns/list':return {'data':[self.latest]}
            raise TimeoutError()
        self.rpc.call=call
        await self.due()
        self.assertTrue(self.c.unknown)
        self.assertIsNone(self.j.get('recovery'))

    async def test_restart_preserves_budget_and_uncertain_dispatch_not_replayed(self):
        self.fail(0);await self.due();self.fail(1)
        restored=Controller(self.rpc,self.tg,self.j,self.root)
        restored.recovery.restore()
        self.assertTrue(restored.busy)
        self.assertEqual(restored.recovery.state['attempt'],1)
        restored.recovery.state['phase']='dispatching';restored.recovery.save()
        again=Controller(self.rpc,self.tg,self.j,self.root)
        again.recovery.restore()
        self.assertTrue(again.unknown)
        self.assertIsNone(again.recovery.state)

    async def test_duplicate_terminal_does_not_consume_attempt(self):
        self.fail(1)
        self.assertFalse(self.c.recovery.schedule(self.latest))
        self.assertEqual(self.c.recovery.state['attempt'],0)

    async def test_new_input_during_wait_not_silently_queued(self):
        self.fail(1)
        await self.c.handle('different task')
        self.assertTrue(self.c.recovery.waiting)
        self.assertIn('尚未插入',self.tg.say.call_args.args[0])
        self.assertFalse(self.rpc.calls)

    async def test_events_schedule_then_success_restores_idle(self):
        self.c.turn='failed-1';self.c.busy=True
        self.rpc.notifications.put({'method':'turn/completed','params':{'threadId':'thread1','turn':{'id':'failed-1','status':'failed','error':ERROR}}})
        task=asyncio.create_task(self.c.events())
        await asyncio.sleep(.08)
        self.assertTrue(self.c.recovery.waiting)
        self.assertTrue(self.c.busy)
        self.assertEqual(self.c.outbox.get_nowait()[0],'notice')
        self.latest={'id':'failed-1','status':'failed','error':ERROR}
        self.c.recovery.state['due']=0
        await asyncio.sleep(.08)
        self.assertEqual(self.c.recovery.state['phase'],'running')
        self.rpc.notifications.put({'method':'turn/completed','params':{'threadId':'thread1','turn':{'id':'turn1','status':'completed'}}})
        await asyncio.sleep(.08)
        self.c.closed=True
        await task
        self.assertFalse(self.c.busy)
        self.assertIsNone(self.c.recovery.state)
        self.assertEqual(self.c.outbox.get_nowait()[0],'result')

    async def test_stop_while_dispatch_inflight_interrupts_new_turn(self):
        self.fail(1)
        original=self.rpc.call
        started=asyncio.Event();release=asyncio.Event()
        async def call(method,params):
            if method=='turn/start':
                started.set();await release.wait()
            return await original(method,params)
        self.rpc.call=call
        self.c.recovery.state['due']=0
        task=asyncio.create_task(self.c.recovery.tick())
        await started.wait()
        await self.c.handle('/stop')
        release.set();await task
        self.assertIn(('turn/interrupt',{'threadId':'thread1','turnId':'turn1'}),self.rpc.calls)
        self.assertIsNone(self.c.recovery.state)

    async def test_busy_schedule_does_not_consume_attempt(self):
        self.fail(1)
        with patch.object(self.c,'acquire_execution',side_effect=RuntimeError('busy')):
            await self.due()
        self.assertEqual(self.c.recovery.state['attempt'],0)
        self.assertTrue(self.c.recovery.waiting)
