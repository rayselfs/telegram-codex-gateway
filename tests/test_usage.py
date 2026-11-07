import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from gateway.core.members import Members
from gateway.core.usage import summarize,Usage

class UsageTests(unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.members=Members(self.root/'members.sqlite',self.root);self.addCleanup(self.members.close)
  self.members.import_existing([dict(owner_id=n,name='Member '+str(n),home=str(self.root/f'{n}/profile'),workspace=str(self.root/f'{n}/workspace')) for n in (101,102)],101)
 def fixture(self,owner=102):
  p=self.root/str(owner)/'profile/codex-home/sessions';p.mkdir(parents=True,exist_ok=True)
  rows=[dict(type='session_meta',payload={'id':'session-a'}),dict(type='response_item',payload={'secret':'PRIVATE CONTENT'})]
  for value in [10,10,20]:
   rows.append(dict(type='event_msg',payload=dict(type='token_count',info=dict(total_token_usage=dict(input_tokens=value,cached_input_tokens=2,output_tokens=5,reasoning_output_tokens=3,total_tokens=value+5)))))
  (p/'a.jsonl').write_text('\n'.join(json.dumps(r) for r in rows)+'\n')
  (p/'duplicate.jsonl').write_text((p/'a.jsonl').read_text())
  return p
 def test_cumulative_events_and_duplicate_files_not_added_twice(self):
  p=self.fixture();result=summarize(p)
  self.assertEqual(result['tokens']['total_tokens'],25)
  self.assertEqual(result['sessions'],1)
  self.assertNotIn('PRIVATE CONTENT',json.dumps(result))
 def test_missing_and_corrupt_are_visible(self):
  p=self.fixture();(p/'broken.jsonl').write_text('{broken\n')
  result=summarize(p);self.assertGreater(result['incomplete_files'],0)
  self.assertTrue(summarize(self.root/'missing')['missing'])
 async def test_staff_cannot_read_all_or_select_other_user(self):
  usage=Usage(self.members)
  with patch('gateway.core.usage.summarize') as scan:
   for arg in ['all','101','../other']:
    with self.assertRaises((PermissionError,ValueError)):await usage.command(102,arg,'request')
   scan.assert_not_called()
 async def test_admin_all_and_staff_self(self):
  self.fixture(101);self.fixture(102);usage=Usage(self.members)
  text=await usage.command(102,'','request');self.assertIn('Member 102',text);self.assertNotIn('Member 101',text)
  text=await usage.command(101,'all','request');self.assertIn('Member 101',text);self.assertIn('Member 102',text);self.assertNotIn('PRIVATE CONTENT',text)
 async def test_member_home_cannot_escape_deployment(self):
  self.members.db.execute('UPDATE members SET home=? WHERE owner_id=102',(str(self.root.parent/'outside'),))
  with self.assertRaises(ValueError):await Usage(self.members).command(102,'','request')
 async def test_registration_command_returns_plain_summary(self):
  from types import SimpleNamespace as NS
  from unittest.mock import AsyncMock
  from gateway.telegram.registration import Registration
  self.fixture();send=AsyncMock();r=Registration(self.members,send,AsyncMock(),AsyncMock(),AsyncMock(),'test_bot')
  r.extensions['/usage']=Usage(self.members).command
  update=NS(effective_user=NS(id=102,is_bot=False),effective_chat=NS(id=102,type='private'),message=NS(text='/usage',message_id=1))
  self.assertTrue(await r.handle(update));text=send.call_args.args[1]
  self.assertTrue(text.startswith('Member 102'));self.assertIn('Member 102',text)
 def test_counter_regression_is_marked_incomplete(self):
  p=self.fixture();file=p/'a.jsonl';rows=file.read_text().splitlines()
  with file.open('a') as out:out.write(rows[2]+'\n')
  result=summarize(p);self.assertEqual(result['tokens']['total_tokens'],25);self.assertEqual(result['incomplete_files'],1)
 async def test_compact_usage_and_menu(self):
  from gateway.telegram.ui import COMMANDS
  self.fixture();text=await Usage(self.members).command(102,'','test')
  self.assertIn('已使用',text);self.assertIn('API 標準價估算',text)
  self.assertNotIn('資料缺口',text);self.assertNotIn('快取／推理',text)
  self.assertIn('usage',dict(COMMANDS))
 def test_standard_price_with_cache_and_output(self):
  p=self.fixture();f=p/'a.jsonl';rows=f.read_text().splitlines()
  rows.insert(1,json.dumps(dict(type='turn_context',payload={'model':'gpt-6.1-sol'})))
  f.write_text('\n'.join(rows)+'\n');(p/'duplicate.jsonl').write_text(f.read_text())
  result=summarize(p)
  self.assertAlmostEqual(result['estimated_usd'],(18*2+2*.1+5*10)/1000000)

 def test_unknown_model_does_not_invent_price(self):
  self.assertIsNone(summarize(self.fixture())['estimated_usd'])
 def test_long_context_and_model_rates(self):
  from gateway.core.usage import estimate
  d=dict(input_tokens=300000,cached_input_tokens=100000,cache_write_input_tokens=0,output_tokens=1000)
  self.assertAlmostEqual(estimate(d,'gpt-6.1-sol',300000),.835)
  self.assertAlmostEqual(estimate(d,'gpt-6-astra',300000),4.275)
