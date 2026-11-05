import asyncio
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
from controller import Controller
from state import Journal
from test_gateway import directory,FakeRPC

class ProgressTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        root=directory();self.j=Journal(root/'progress.sqlite')
        self.rpc=FakeRPC(root);self.tg=SimpleNamespace(say=AsyncMock(return_value=[42]))
        self.c=Controller(self.rpc,self.tg,self.j,root)
        self.c.thread='thread';self.c.turn='turn';self.c.busy=True
        self.p={'threadId':'thread','turnId':'turn','item':{'type':'agentMessage','phase':'commentary','id':'i1','text':'正在確認指定資料。'}}
    async def asyncTearDown(self):
        self.c.closed=True;self.j.close()
    async def test_actual_event_and_sender_deliver_before_completion(self):
        self.rpc.notifications.put({'method':'item/completed','params':self.p})
        tasks=[asyncio.create_task(self.c.events()),asyncio.create_task(self.c.sender())]
        try:
            for _ in range(30):
                if self.tg.say.await_count:break
                await asyncio.sleep(.01)
            self.tg.say.assert_awaited_once_with('💭 正在確認指定資料。')
            self.assertTrue(self.c.busy)
        finally:
            self.c.closed=True
            for t in tasks:t.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
    async def test_other_owner_turn_reasoning_tools_and_background_are_ignored(self):
        for field,value in [('threadId','other'),('turnId','old')]:
            p=copy.deepcopy(self.p);p[field]=value;self.c.queue_progress(p)
        for field,value in [('type','reasoning'),('type','commandExecution'),('phase','final_answer')]:
            p=copy.deepcopy(self.p);p['item'][field]=value;self.c.queue_progress(p)
        self.c.interactive=False;self.c.queue_progress(self.p)
        self.assertTrue(self.c.outbox.empty())
    async def test_throttle_cap_and_repeat_event(self):
        with patch('controller.time.monotonic',return_value=100):
            self.c.queue_progress(self.p);self.c.queue_progress(self.p)
            p=copy.deepcopy(self.p);p['item']['id']='too-soon';self.c.queue_progress(p)
        for i in range(1,10):
            p=copy.deepcopy(self.p);p['item']['id']=str(i)
            with patch('controller.time.monotonic',return_value=100+i*10):self.c.queue_progress(p)
        self.assertEqual(self.c.outbox.qsize(),6)
    async def test_ambiguous_send_is_not_replayed(self):
        self.c.queue_progress(self.p);job=self.c.outbox.get_nowait()
        self.tg.say.side_effect=TimeoutError()
        await self.c.send_progress(*job[1:])
        self.c.progress_count=0;self.c.queue_progress(self.p)
        self.assertTrue(self.c.outbox.empty())
        self.assertEqual(self.j.db.execute('select state from delivery where key=?',(job[1],)).fetchone()[0],'unknown')
    async def test_completed_turn_progress_is_dropped(self):
        self.c.queue_progress(self.p);job=self.c.outbox.get_nowait();self.c.busy=False
        await self.c.send_progress(*job[1:]);self.tg.say.assert_not_awaited()
    async def test_tool_icon_uses_event_without_exposing_command_or_tool_name(self):
        self.c.queue_tool_progress({**self.p,'item':{'type':'commandExecution','id':'cmd','command':'SECRET COMMAND'}})
        job=self.c.outbox.get_nowait();await self.c.send_progress(*job[1:])
        self.tg.say.assert_awaited_once_with('⚙️ 正在執行工作步驟。')
    async def test_reply_survives_other_messages_and_manual_retrieval(self):
        self.j.set('active_reply',123);self.c.bind_reply('turn')
        self.j.set('active_reply',456)
        with patch('artifacts.deliver',new_callable=AsyncMock):
            await self.c.deliver_result('thread','turn',status='completed')
        self.tg.say.assert_awaited_once_with('result',reply_to=123)
        self.tg.say.reset_mock()
        await self.c.deliver_result('thread','turn',manual=True,status='completed')
        self.assertEqual(self.tg.say.call_args.kwargs,{'reply_to':123})
    async def test_start_snapshots_message_source_and_recovery_retains_it(self):
        from transport import SOURCE_MESSAGE
        self.c.busy=False
        token=SOURCE_MESSAGE.set(123)
        try:await self.c.start([{'type':'text','text':'fixture'}])
        finally:SOURCE_MESSAGE.reset(token)
        self.assertEqual(self.j.get('reply:thread:turn1'),123)
        self.c.busy=False
        await self.c.start([{'type':'text','text':'resume'}],recovery_model='model')
        self.assertEqual(self.j.get('reply:thread:turn1'),123)
    async def test_final_does_not_repeat_commentary_manual_result_can_retrieve_it(self):
        self.rpc.call=AsyncMock(return_value={'data':[{'item':self.p['item']},{'item':{'type':'agentMessage','phase':'final_answer','text':'完成。'}}],'nextCursor':None})
        with patch('artifacts.deliver',new_callable=AsyncMock):
            await self.c.deliver_result('thread','turn',status='completed')
        self.tg.say.assert_awaited_once_with('完成。')
        self.tg.say.reset_mock()
        await self.c.deliver_result('thread','turn',status='completed',manual=True)
        self.assertIn('正在確認指定資料。',[c.args[0] for c in self.tg.say.call_args_list])
