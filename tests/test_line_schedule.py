import asyncio
import io
import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch,AsyncMock
from test_gateway import directory
from schedules import Schedules,validate,ScheduleUI
from lifecycle import SingleInstance
import line_schedule


def spec():return {'name':'LINE fixture','kind':'line_message','recipient':'測試群組','content':'FAKE ONLY','schedule':{'kind':'daily','time':'09:00'}}

class LineScheduleTests(unittest.TestCase):
    def setUp(self):
        self.root=directory();self.store=Schedules(self.root/'schedules.sqlite')
        key,_=self.store.propose('create',spec=spec());self.store.confirm(key)
        task=self.store.tasks()[0]['id']
        with self.store.db:self.store._insert_run(task,time.time()-1,json.dumps(spec()),None,None)
        self.run=self.store.claim();self.profile={'owner_id':1000000001,'home':self.root}
        self.lease=patch('line_schedule.LOCK',self.root/'line.lock');self.lease.start()
    def tearDown(self):self.lease.stop();self.store.close()
    def process(self,outcome='sent'):
        store=self.store;rid=self.run['id']
        class Input(io.StringIO):
            def write(self,value):
                if json.loads(value).get('send'):
                    row=store.db.execute('SELECT state FROM line_deliveries WHERE run=?',(rid,)).fetchone()
                    assert row['state']=='sending','Must commit reservation before send permission'
                return super().write(value)
        events=[{'stage':'ready','recipient':'測試群組','before':[]}]
        if outcome=='sent':events.append({'stage':'sent','messageId':'123456'})
        elif outcome=='local':events.append({'stage':'sent','messageId':'local-1'})
        elif outcome=='blocked':events=[{'stage':'blocked'}]
        return SimpleNamespace(stdin=Input(),stdout=io.StringIO(''.join(json.dumps(e)+'\n' for e in events)),wait=Mock(return_value=0),kill=Mock())
    def execute(self,outcome='sent'):
        process=self.process(outcome)
        with patch('line_schedule.subprocess.Popen',return_value=process) as popen,patch('line_schedule.ChildJob'):
            result=line_schedule.execute(self.profile,self.run,self.store)
        return result,popen.call_count
    def test_sent_occurrence_is_not_sent_twice(self):
        self.assertEqual(self.execute()[0][0],'completed')
        result,count=self.execute();self.assertEqual(result[0],'completed');self.assertEqual(count,0)
    def test_crash_after_permission_is_unknown_and_pauses(self):
        self.assertEqual(self.execute('crash')[0][0],'unknown')
        self.assertFalse(self.store.tasks()[0]['enabled'])
        result,count=self.execute();self.assertEqual(result[0],'unknown');self.assertEqual(count,0)
    def test_restart_recovers_receipt_or_pauses_ambiguous_occurrence(self):
        self.store.db.execute('CREATE TABLE line_deliveries(run TEXT PRIMARY KEY,state TEXT NOT NULL,receipt TEXT)')
        with self.store.db:self.store.db.execute('INSERT INTO line_deliveries VALUES(?,?,?)',(self.run['id'],'sending','{}'))
        self.store.recover()
        self.assertFalse(self.store.tasks()[0]['enabled'])
        self.assertEqual(self.store.runs()[0]['state'],'unknown')
        with self.store.db:
            self.store.db.execute("UPDATE runs SET state='running' WHERE id=?",(self.run['id'],))
            self.store.db.execute("UPDATE line_deliveries SET state='sent',receipt='confirmed' WHERE run=?",(self.run['id'],))
        self.store.recover()
        self.assertEqual(self.store.runs()[0]['state'],'completed')
        self.assertEqual(self.store.runs()[0]['result'],'confirmed')
    def test_local_id_is_not_success(self):self.assertEqual(self.execute('local')[0][0],'unknown')
    def test_pre_send_login_failure_does_not_reserve_or_send(self):
        self.assertEqual(self.execute('blocked')[0][0],'blocked')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM line_deliveries').fetchone()[0],0)
    def test_shared_browser_busy_requeues_both_owners_without_launch(self):
        lock=SingleInstance(self.root/'line.lock')
        try:
            for owner in (1000000001,1000000002):
                self.profile['owner_id']=owner
                result,count=self.execute();self.assertEqual(result[0],'queued');self.assertEqual(count,0)
        finally:lock.close()
    def test_expired_occurrence_never_launches(self):
        self.run['due']=time.time()-901;result,count=self.execute()
        self.assertEqual(result[0],'missed');self.assertEqual(count,0)
    def test_recipient_scope_and_message_validation(self):
        for change in [{'recipient':''},{'recipient':'a\nb'},{'content':'a'*5001},{'owner':1000000002},{'kind':'codex'}]:
            with self.assertRaises(ValueError):validate({**spec(),**change})
    def test_tick_routes_fixed_sender_without_model(self):
        from schedule_worker import tick
        self.store.finish(self.run['id'],'queued','')
        profile={**self.profile,'home':self.root,'workspace':self.root}
        with patch('schedules.Schedules',return_value=self.store),patch('line_schedule.execute',return_value=('completed','fake checked')) as send,patch('schedule_worker.execute',new_callable=AsyncMock) as model:
            self.store.close=Mock()
            self.assertEqual(asyncio.run(tick(profile)),'processed')
            send.assert_called_once();model.assert_not_awaited()
        self.store.db.close()
        self.store.close=Mock()

class LineScheduleUITests(unittest.IsolatedAsyncioTestCase):
    async def test_confirmation_shows_recipient_and_content(self):
        root=directory();store=Schedules(root/'s.sqlite')
        c=SimpleNamespace(tg=SimpleNamespace(display_name='User B',say=AsyncMock(),buttons=AsyncMock()),model=None,effort=None)
        try:
            ui=ScheduleUI(store,c);await ui.tool({'action':'create','spec':spec()})
            self.assertIn('測試群組',c.tg.say.call_args.args[0]);self.assertIn('FAKE ONLY',c.tg.say.call_args.args[0])
            self.assertEqual(store.tasks(),[])
        finally:store.close()
