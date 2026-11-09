import json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from gateway.core.members import Members
from gateway.core.member_relay import RelayRegistration

class RelayTests(unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name);self.members=Members(self.root/'members.sqlite',self.root);self.addCleanup(self.members.close)
  self.members.import_existing([dict(owner_id=n,name=str(n),home=str(self.root/f'{n}/profile'),workspace=str(self.root/f'{n}/workspace')) for n in (101,102,103)],101)
  (self.root/'profile').mkdir()
  (self.root/'profile/member-relay.json').write_text(json.dumps(dict(contacts={'101':{'name':'Recipient','aliases':[]},'102':{'name':'Sender','aliases':['Ivy','林佳音','佳音']}},allowed_pairs=[[102,101],[101,102]])))
  self.send=AsyncMock(return_value=[987])
  self.registration=RelayRegistration(self.members,self.send,AsyncMock(),AsyncMock(),AsyncMock(),'test_bot')
 def update(self,text,actor=102,mid=1,kind='private'):
  return NS(effective_user=NS(id=actor,is_bot=False),effective_chat=NS(id=actor,type=kind),message=NS(text=text,message_id=mid))
 async def test_explicit_send_duplicate_and_changed_body(self):
  u=self.update('幫我告訴 Recipient：明天見')
  self.assertTrue(await self.registration.handle(u))
  self.assertEqual(self.send.call_args_list[0].args,(101,'Sender 委託轉達：\n\n明天見'))
  await self.registration.handle(u)
  await self.registration.handle(self.update('/tell Recipient: changed'))
  self.assertEqual(sum(c.args[0]==101 for c in self.send.call_args_list),1)
 async def test_explicit_message_accepts_newline_and_preserves_body(self):
  for i,delimiter in enumerate(("\n","\r\n")):
   update=self.update("發訊息給 Recipient"+delimiter+"明天見\n\n在原地等",mid=10+i)
   self.assertTrue(await self.registration.handle(update))
  self.assertEqual(sum(call.args==(101,"Sender 委託轉達：\n\n明天見\n\n在原地等")
                       for call in self.send.call_args_list),2)
  self.assertFalse(await self.registration.handle(self.update("通知 Recipient\n這只是一般對話",mid=20)))
  forwarded=self.update("發訊息給 Recipient\n不得轉送",mid=21)
  forwarded.message.forward_origin=object()
  await self.registration.handle(forwarded)
  self.assertFalse(any(call.args==(101,"Sender 委託轉達：\n\n不得轉送")
                       for call in self.send.call_args_list))
 async def test_unknown_not_retried_even_after_reload(self):
  async def send(owner,text):
   if owner==101:raise TimeoutError('ambiguous')
   return [1]
  self.send.side_effect=send
  await self.registration.handle(self.update('/tell Recipient: hello'))
  self.registration=RelayRegistration(self.members,self.send,AsyncMock(),AsyncMock(),AsyncMock(),'test_bot')
  await self.registration.handle(self.update('/tell Recipient: hello'))
  self.assertEqual(sum(c.args[0]==101 for c in self.send.call_args_list),1)
  self.assertIn('不會自動重送',self.send.call_args.args[1])
 async def test_scope_and_plain_chat(self):
  for u in [self.update('/tell Recipient: x',actor=103),self.update('/tell Recipient: x',actor=101),self.update('/tell Recipient: '+'x'*2001)]:
   self.assertTrue(await self.registration.handle(u))
  self.assertFalse(await self.registration.handle(self.update('/tell Recipient: x',kind='group')))
  self.assertFalse(await self.registration.handle(self.update('Recipient 說明天見')))
  self.assertTrue(await self.registration.handle(self.update('幫我告訴別人：x')))
  self.assertFalse(any(c.args[0]==101 and c.args[1].startswith('Sender 委託轉達：') for c in self.send.call_args_list))
 async def test_suspended_recipient(self):
  self.members.db.execute("UPDATE members SET state='suspended' WHERE owner_id=101");self.members.db.commit()
  await self.registration.handle(self.update('/tell Recipient: x'))
  self.assertFalse(any(c.args[0]==101 and c.args[1].startswith('Sender 委託轉達：') for c in self.send.call_args_list))
 async def test_forward_and_suspended_sender_refused(self):
  u=self.update('/tell Recipient: secret');u.message.forward_origin=object()
  await self.registration.handle(u)
  self.members.db.execute("UPDATE members SET state='suspended' WHERE owner_id=102");self.members.db.commit()
  await self.registration.handle(self.update('/tell Recipient: x',mid=2))
  self.assertFalse(any(c.args[0]==101 for c in self.send.call_args_list))
 async def test_concurrent_duplicate_and_restart_sending(self):
  import asyncio,sqlite3
  entered=asyncio.Event();finish=asyncio.Event()
  async def send(owner,text):
   if owner==101:entered.set();await finish.wait()
   return [987]
  self.send.side_effect=send
  pending=asyncio.create_task(self.registration.handle(self.update('/tell Recipient: x')))
  await entered.wait()
  await self.registration.handle(self.update('/tell Recipient: x'))
  self.assertEqual(sum(c.args[0]==101 for c in self.send.call_args_list),1)
  finish.set();await pending
  with sqlite3.connect(self.root/'profile/member-relay.sqlite') as db:db.execute("UPDATE relay SET state='sending'")
  db.close()
  self.registration=RelayRegistration(self.members,self.send,AsyncMock(),AsyncMock(),AsyncMock(),'test_bot')
  await self.registration.handle(self.update('/tell Recipient: x'))
  self.assertEqual(sum(c.args[0]==101 for c in self.send.call_args_list),1)

 async def test_aliases_bidirectional_and_ambiguous_refusal(self):
  for i,name in enumerate(['Ivy','林佳音','佳音']):
   await self.registration.handle(self.update('幫我告訴 '+name+'：你好',actor=101,mid=i+10))
  self.assertEqual(sum(c.args==(102,'Recipient 委託轉達：\n\n你好') for c in self.send.call_args_list),3)
  p=self.root/'profile/member-relay.json';config=json.loads(p.read_text())
  config['contacts']['103']={'name':'Other','aliases':['佳音']};p.write_text(json.dumps(config))
  self.send.reset_mock()
  await self.registration.handle(self.update('發訊息給佳音：你好',actor=101,mid=20))
  self.assertEqual(self.send.call_count,1)
  self.assertIn('未傳送',self.send.call_args.args[1])
