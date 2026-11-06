import json
import time
import unittest
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from test_gateway import directory
from schedules import Schedules, validate
from line_dynamic import execute, parse

class DynamicLineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.root=directory();self.store=Schedules(self.root/'schedule.sqlite')
        self.spec={'name':'dynamic fixture','kind':'line_dynamic','recipient':'固定群組',
                   'content':'查詢明天所有資料，無資料不送；不含地點。','schedule':{'kind':'daily','time':'19:00'}}
        key,_=self.store.propose('create',spec=self.spec);self.store.confirm(key)
        with self.store.db:self.store._insert_run(self.store.tasks()[0]['id'],time.time()-1,json.dumps(self.spec),None,None)
        self.run=self.store.claim();self.profile={'owner_id':1000000001,'home':self.root}
    async def asyncTearDown(self):self.store.close()
    async def test_both_owners_generate_once_and_reuse_exact_payload_when_busy(self):
        for owner in (1000000001,1000000002):
            with self.store.db:self.store.db.execute('DELETE FROM line_prepared')
            self.profile['owner_id']=owner
            with patch('schedule_worker.execute',new_callable=AsyncMock,return_value=('completed','{"action":"send","text":"📣 明天聚會\\n🙏 禱告"}')) as generate,patch('line_dynamic.send_line',return_value=('queued','busy')) as send:
                await execute(self.profile,self.run,self.store)
                await execute(self.profile,self.run,self.store)
                self.assertEqual(generate.await_count,1)
                sent=json.loads(send.call_args.args[1]['spec'])
                self.assertEqual(sent['recipient'],'固定群組');self.assertEqual(sent['content'],'📣 明天聚會\n🙏 禱告')
                self.assertEqual(sent['kind'],'line_message')
                self.assertIn('本次排程基準時間（台北）',generate.call_args.args[1]['spec'])
    async def test_empty_success_silent_without_sender(self):
        with patch('schedule_worker.execute',new_callable=AsyncMock,return_value=('completed','{"action":"skip","text":""}')) as generate,patch('line_dynamic.send_line') as send:
            self.assertEqual(await execute(self.profile,self.run,self.store),('completed','[SILENT]'))
            self.assertEqual(await execute(self.profile,self.run,self.store),('completed','[SILENT]'))
            send.assert_not_called();self.assertEqual(generate.await_count,1)
    async def test_failed_incomplete_or_malformed_generation_never_sends(self):
        for state,text in [('failed','{"action":"send","text":"wrong"}'),('completed','not json'),
                           ('completed','{"action":"error","text":"login failed"}'),
                           ('completed','{"action":"send","text":"x","recipient":"other"}')]:
            with patch('schedule_worker.execute',new_callable=AsyncMock,return_value=(state,text)),patch('line_dynamic.send_line') as send:
                self.assertEqual((await execute(self.profile,self.run,self.store))[0],'blocked')
                send.assert_not_called();self.assertFalse(self.store.tasks()[0]['enabled'])
    async def test_overdue_never_generates(self):
        with patch('schedule_worker.execute',new_callable=AsyncMock) as generate:
            self.run['due']=time.time()-901
            self.assertEqual((await execute(self.profile,self.run,self.store))[0],'missed');generate.assert_not_called()
    async def test_native_worker_capture_returns_final_without_telegram_or_finishing_run(self):
        from schedule_worker import execute as generate
        from test_gateway import FakeRPC
        from controller import Controller
        class RPC(FakeRPC):
            _proc=None
            def initialize(self,*args):pass
            def close(self):pass
            async def call(self,method,params=None):
                if method=='config/read':return {'config':{'features':{'apps':False},'mcp_oauth_credentials_store':'file'}}
                if method=='thread/items/list':return {'data':[{'item':{'type':'agentMessage','phase':'final_answer','text':'{"action":"send","text":"fixture"}'}}]}
                return await super().call(method,params)
        async def start(c,inputs):
            self.assertIn('最終只輸出一個 JSON',inputs[0]['text'])
            c.thread='t';c.turn='r';c.busy=False;c.journal.job('t','r','completed')
        with sqlite3.connect(self.root/'transport.sqlite') as db:db.execute('CREATE TABLE kv(key TEXT,value TEXT)')
        profile={**self.profile,'name':'fixture','workspace':self.root}
        from unittest.mock import Mock
        with patch('rpc.Client',return_value=RPC(self.root)) as client,patch('lifecycle.ChildJob'),patch('autonomy_policy.configure',return_value=SimpleNamespace(close=lambda:None)),patch.object(Controller,'initialize',new_callable=AsyncMock),patch.object(Controller,'start',start),patch('managed_skills.extra_roots',return_value=[]):
            result=await generate(profile,self.run,self.store,capture=True)
            self.assertEqual(result,('completed','{"action":"send","text":"fixture"}'))
            self.assertEqual(client.call_args.kwargs,{'browser':False,'authorize':None})
            self.assertEqual(self.store.runs()[0]['state'],'running')
    def test_validation_and_strict_output(self):
        validate(self.spec)
        with self.assertRaises(ValueError):validate({k:v for k,v in self.spec.items() if k!='recipient'})
        for v in [{'action':'send','text':''},{'action':'send','text':'a'*5001},{'action':'skip','text':'query failed'}]:
            with self.assertRaises(ValueError):parse(json.dumps(v))
