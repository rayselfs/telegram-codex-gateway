import asyncio
import json
import time
import unittest
from datetime import datetime
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from test_gateway import directory, FakeRPC
from gateway.core.controller import Controller
from gateway.core.state import Journal
from gateway.platforms.lifecycle import SingleInstance
from gateway.scheduling.schedules import Schedules, ScheduleUI, validate, next_time, LOCAL_ZONE as TAIPEI


def sample():
    return {'name':'驗收提醒','kind':'reminder','content':'測試','schedule':{'kind':'daily','time':'09:00'}}


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        self.root=directory();self.s=Schedules(self.root/'schedule.sqlite')
    def tearDown(self):self.s.close()
    def create(self):
        key,_=self.s.propose('create',spec=sample(),model='test',effort='medium')
        self.s.confirm(key)
        return self.s.tasks()[0]['id']
    def test_timezone_weekly_interval_validation(self):
        now=datetime(2026,11,2,9,tzinfo=TAIPEI).timestamp()
        self.assertEqual(next_time({'kind':'daily','time':'09:00'},now),now+86400)
        self.assertEqual(next_time({'kind':'weekly','days':[0],'time':'09:00'},now),now+7*86400)
        for bad in [{'kind':'interval','minutes':True},{'kind':'daily','time':'25:00'},
                    {'kind':'weekly','days':[],'time':'09:00'},{'kind':'once','at':'2027-01-01T09:00:00'}]:
            with self.assertRaises(ValueError):validate({**sample(),'schedule':bad})
        with self.assertRaises(ValueError):validate({**sample(),'owner':'other'})
    def test_confirmation_is_owner_bound_single_use(self):
        other=Schedules(directory()/'db')
        try:
            key,_=self.s.propose('create',spec=sample())
            self.assertEqual(self.s.tasks(),[])
            with self.assertRaises(ValueError):other.confirm(key)
            self.s.confirm(key)
            with self.assertRaises(ValueError):self.s.confirm(key)
            with self.assertRaises(ValueError):other.propose('pause',task=self.s.tasks()[0]['id'])
        finally:other.close()
    def test_stale_update_and_cancel(self):
        tid=self.create()
        k1,_=self.s.propose('pause',task=tid);k2,_=self.s.propose('delete',task=tid)
        self.s.confirm(k1)
        with self.assertRaises(ValueError):self.s.confirm(k2)
        k,_=self.s.propose('resume',task=tid);self.s.confirm(k,False)
        self.assertFalse(self.s.tasks()[0]['enabled'])
    def test_duplicate_due_missed_and_crash(self):
        tid=self.create();due=time.time()-10
        with self.s.db:self.s.db.execute('UPDATE tasks SET next=? WHERE id=?',(due,tid))
        self.s.enqueue();self.s.enqueue()
        self.assertEqual(len(self.s.runs()),1)
        run=self.s.claim();self.assertIsNone(self.s.claim())
        self.s.recover();self.assertEqual(self.s.runs()[0]['state'],'unknown')
        with self.s.db:self.s._insert_run(tid,time.time()-1000,json.dumps(sample()),None,None)
        self.s.enqueue();self.assertIn('missed',[r['state'] for r in self.s.runs()])
    def test_interval_anchor_and_future_run_not_claimed(self):
        now=time.time();spec={**sample(),'schedule':{'kind':'interval','minutes':5}}
        k,_=self.s.propose('create',spec=spec);self.s.confirm(k)
        tid=self.s.tasks()[0]['id']
        with self.s.db:self.s.db.execute('UPDATE tasks SET next=?',(now-10,))
        self.s.enqueue(now)
        self.assertAlmostEqual(self.s.tasks()[0]['next'],now+290)
        self.s.claim()
        with self.s.db:self.s._insert_run(tid,now+3600,json.dumps(spec),None,None)
        self.assertIsNone(self.s.claim())
    def test_pause_cancels_queued_but_not_running(self):
        tid=self.create()
        with self.s.db:
            for due in (time.time(),time.time()+60):
                self.s._insert_run(tid,due,json.dumps(sample()),None,None)
        run=self.s.claim()
        k,_=self.s.propose('pause',task=tid);self.s.confirm(k)
        self.assertEqual({r['state'] for r in self.s.runs()},{'running','cancelled'})
    def test_os_lease_excludes_other_owner_work_process(self):
        a=SingleInstance(self.root/'execution.lock')
        try:
            with self.assertRaises(RuntimeError):SingleInstance(self.root/'execution.lock')
            b=SingleInstance(directory()/'execution.lock');b.close()
        finally:a.close()
        b=SingleInstance(self.root/'execution.lock');b.close()


class ScheduleControllerTests(unittest.IsolatedAsyncioTestCase):
    async def test_tick_waits_for_foreground_and_does_not_repeat(self):
        from gateway.scheduling.schedule_worker import tick
        root=directory();profile={'home':root,'workspace':root,'name':'test','owner_id':101}
        store=Schedules(root/'schedules/schedules.sqlite')
        key,_=store.propose('create',spec=sample());store.confirm(key)
        with store.db:store.db.execute('UPDATE tasks SET next=?',(time.time()-1,))
        lease=SingleInstance(root/'execution.lock')
        try:
            self.assertEqual(await tick(profile),'foreground_busy')
            self.assertEqual(store.runs()[0]['state'],'queued')
        finally:lease.close()
        self.assertEqual(await tick(profile),'processed')
        self.assertEqual(store.runs()[0]['state'],'completed')
        self.assertEqual(await tick(profile),'idle')
        self.assertEqual(len(store.runs()),1);store.close()
    async def test_delivery_failure_never_reruns_or_retries(self):
        from gateway.scheduling.schedule_delivery import deliver
        store=Schedules(directory()/'db')
        with store.db:store._insert_run('t',time.time(),json.dumps(sample()),None,None)
        row=store.claim();store.finish(row['id'],'completed','ok')
        controller=NS(closed=False)
        async def fail(text):
            controller.closed=True
            raise RuntimeError('ambiguous send')
        tg=NS(controller=controller,health=lambda:'ready',say=fail)
        task=asyncio.create_task(deliver(store,tg));await asyncio.sleep(.05);task.cancel()
        await asyncio.gather(task,return_exceptions=True)
        self.assertEqual(store.runs()[0]['state'],'completed')
        self.assertEqual(store.runs()[0]['notification'],'unknown')
        self.assertIsNone(store.claim());store.close()
    async def test_native_tool_only_current_thread_and_owner_ui(self):
        root=directory();s=Schedules(root/'schedules.sqlite');journal=Journal(root/'chat.sqlite')
        tg=NS(say=AsyncMock(),buttons=AsyncMock(),display_name='User A')
        c=Controller(FakeRPC(root),tg,journal,root);c.schedule_ui=ScheduleUI(s,c)
        c.thread='thread1';c.turn='turn1'
        p={'tool':'gateway_schedule','threadId':'wrong','turnId':'turn1','arguments':{'action':'create','spec':sample()}}
        await c.server_request({'id':1,'method':'item/tool/call','params':p})
        self.assertFalse(s.tasks());self.assertTrue(c.rpc.errors)
        p['threadId']='thread1'
        await c.server_request({'id':2,'method':'item/tool/call','params':p})
        self.assertTrue(c.rpc.responses[-1][1]['success']);self.assertFalse(s.tasks())
        self.assertEqual(tg.buttons.await_count,1)
        s.close();journal.close()
    async def test_foreground_lease_blocks_second_controller(self):
        root=directory();j=Journal(root/'j');tg=NS(say=AsyncMock())
        c=Controller(FakeRPC(root),tg,j,root);c.execution_lock_path=root/'execution.lock'
        lock=SingleInstance(c.execution_lock_path)
        await c.start([{'type':'text','text':'test'}]);self.assertFalse(c.busy)
        lock.close();await c.start([{'type':'text','text':'test'}]);self.assertTrue(c.busy)
        c.release_execution();j.close()


class ManualRunTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.root=directory();self.s=Schedules(self.root/'s');self.j=Journal(self.root/'j')
        self.c=Controller(FakeRPC(self.root),NS(say=AsyncMock(),buttons=AsyncMock(),display_name='test'),self.j,self.root)
        self.c.busy=True;self.c.turn='turn';self.j.set('active_reply',123)
        self.ui=ScheduleUI(self.s,self.c)
        key,_=self.s.propose('create',spec={**sample(),'content':'PRIVATE LONG CONFIG'})
        self.s.confirm(key);self.task=self.s.tasks()[0]['id']
    async def asyncTearDown(self):self.s.close();self.j.close()
    async def test_authorized_once_keeps_schedule_and_invalidates_old_button(self):
        before=self.s.tasks();old,_=self.s.propose('run',self.task)
        args={'action':'run','task':self.task,'authorized':True}
        self.assertEqual((await self.ui.tool(args))['state'],'queued')
        self.assertEqual((await self.ui.tool(args))['state'],'already_queued')
        self.assertEqual(len(self.s.runs()),1);self.assertEqual(self.s.tasks(),before)
        self.c.tg.say.assert_not_called();self.c.tg.buttons.assert_not_called()
        with self.assertRaises(ValueError):self.s.confirm(old)
        # Durable across reopen, including after the job finishes.
        self.s.db.execute("UPDATE runs SET state='completed'");self.s.db.commit()
        self.s.close();self.s=Schedules(self.root/'s');self.ui=ScheduleUI(self.s,self.c)
        self.assertEqual((await self.ui.tool(args))['state'],'already_queued')
        self.j.set('active_reply',124)
        self.assertEqual((await self.ui.tool(args))['state'],'queued')
    async def test_unapproved_is_compact_and_background_cannot_authorize(self):
        result=await self.ui.tool({'action':'run','task':self.task})
        self.assertEqual(result['state'],'awaiting_user_confirmation');self.assertFalse(self.s.runs())
        self.assertNotIn('PRIVATE LONG CONFIG',self.c.tg.buttons.call_args.args[0])
        self.c.interactive=False
        with self.assertRaises(ValueError):await self.ui.tool({'action':'run','task':self.task,'authorized':True})
    async def test_new_request_cannot_duplicate_pending_or_unknown_work(self):
        args={'action':'run','task':self.task,'authorized':True}
        await self.ui.tool(args);self.j.set('active_reply',125)
        with self.assertRaises(ValueError):await self.ui.tool(args)
        key,_=self.s.propose('run',self.task)
        with self.assertRaises(ValueError):self.s.confirm(key)
        self.s.db.execute("UPDATE runs SET state='unknown'");self.s.db.commit()
        with self.assertRaises(ValueError):await self.ui.tool(args)
        with self.assertRaises(ValueError):await self.ui.tool({**args,'task':'someone-else'})
        with self.assertRaises(ValueError):await self.ui.tool({**args,'spec':sample()})
