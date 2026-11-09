import asyncio
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from gateway.telegram.transport import Transport,SOURCE_MESSAGE
from test_gateway import directory

class ReplyTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_long_answer_chunks_reply_to_original_deleted_source_can_fall_back(self):
        tg=Transport('fake',1,directory());tg._bot=NS(send_message=AsyncMock(return_value=NS(message_id=9)))
        await tg.say('x'*4000,reply_to=123)
        self.assertEqual(tg._bot.send_message.await_count,3)
        for call in tg._bot.send_message.call_args_list:
            self.assertEqual(call.kwargs['reply_parameters'].message_id,123)
            self.assertTrue(call.kwargs['reply_parameters'].allow_sending_without_reply)
        tg._bot.send_message.reset_mock();await tg.say('💭 progress')
        self.assertNotIn('reply_parameters',tg._bot.send_message.call_args.kwargs)
    async def test_concurrent_ingress_keeps_each_origin_task_local(self):
        tg=Transport('fake',1,directory());tg.menu_ready=True
        seen=[]
        async def handle(text):
            await asyncio.sleep(.01)
            seen.append((text,SOURCE_MESSAGE.get()))
        tg.controller=NS(journal=NS(receive=lambda _:True),handle=handle)
        def update(mid):return NS(effective_user=NS(id=1,is_bot=False),effective_chat=NS(id=1,type='private'),
            callback_query=None,edited_message=None,update_id=mid,message=NS(message_id=mid,text=str(mid),caption=None,media_group_id=None,document=None,photo=[]))
        await asyncio.gather(tg.ingress(update(10),None),tg.ingress(update(20),None))
        self.assertEqual(sorted(seen),[('10',10),('20',20)])
        self.assertIsNone(SOURCE_MESSAGE.get())
