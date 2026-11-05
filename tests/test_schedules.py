import asyncio
import json
import time
import unittest
from datetime import datetime
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from test_gateway import directory, FakeRPC
from controller import Controller
from state import Journal
from lifecycle import SingleInstance
from schedules import Schedules, ScheduleUI, validate, next_time, TAIPEI


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
        for _ in range(2):
            k,_=self.s.propose('run',task=tid);self.s.confirm(k)
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
        from schedule_worker import tick
        root=directory();profile={'home':root,'workspace':root,'name':'test'}
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
        from schedule_delivery import deliver
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
        p={'tool':'ezra_schedule','threadId':'wrong','turnId':'turn1','arguments':{'action':'create','spec':sample()}}
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
