import asyncio,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from gateway.core.service_faults import supervise,check_recovery,record_error
class FaultTests(unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.home=Path(self.tmp.name);self.item=dict(profile={'home':self.home},controller=NS(closed=False),service_faults={})
 async def test_failed_service_is_not_replayed_and_peer_survives(self):
  calls=[];done=asyncio.Event();peer=asyncio.create_task(done.wait())
  async def fail():calls.append(1);raise RuntimeError('SECRET TOKEN https://private.example')
  await supervise(self.item,'sender',fail)
  await supervise(self.item,'sender',fail)
  self.assertEqual(calls,[1]);self.assertFalse(peer.done());self.assertIn('sender',self.item['service_faults'])
  text=(self.home/'service-errors.jsonl').read_text();self.assertNotIn('SECRET',text);self.assertNotIn('private.example',text);self.assertNotIn(str(self.home),text)
  done.set();await peer
 async def test_clean_exit_and_cancel(self):
  async def ends():pass
  await supervise(self.item,'sender',ends);self.assertIn('sender',self.item['service_faults'])
  async def cancelled():raise asyncio.CancelledError
  with self.assertRaises(asyncio.CancelledError):await supervise(self.item,'typing',cancelled)
  self.assertNotIn('typing',self.item['service_faults'])
 async def test_recovery_failure_consumed_and_shutdown_normal(self):
  async def fail():raise ValueError('private contents')
  task=asyncio.create_task(fail());await asyncio.sleep(0);self.item['recovery_task']=task
  self.assertFalse(await check_recovery(self.item));self.assertIsNone(self.item['recovery_task'])
  self.item['controller'].closed=True
  async def end():pass
  await supervise(self.item,'typing',end);self.assertNotIn('typing',self.item['service_faults'])
 def test_diagnostic_failure_cannot_raise(self):
  path=self.home/'not-directory';path.write_text('x')
  record_error(path,'fatal',RuntimeError('private'))
 async def test_fault_blocks_new_turn_without_touching_existing_worker(self):
  from unittest.mock import AsyncMock
  from gateway.core.controller import Controller
  c=Controller.__new__(Controller);c.require_member=lambda:None;c.background_faults={'sender':{}};c.tg=NS(say=AsyncMock())
  c.busy=True;c.rpc=NS(call=AsyncMock())
  await c.start([{'type':'text','text':'new request'}])
  c.rpc.call.assert_not_called();self.assertTrue(c.busy);self.assertIn('尚未啟動',c.tg.say.call_args.args[0])
 async def test_real_main_background_wiring_keeps_other_tasks_alive(self):
  import ast
  from unittest.mock import AsyncMock
  tree=ast.parse((Path(__file__).parents[1]/'gateway/core/main.py').read_text(encoding='utf-8'))
  node=next(n for n in ast.walk(tree) if isinstance(n,ast.Expr) and ast.unparse(n).startswith('tasks.extend('))
  gate=asyncio.Event();calls=[]
  async def sender():calls.append(1);raise RuntimeError('secret')
  controller=NS(closed=False,sender=sender);self.item['controller']=controller;self.item['schedules']=object()
  tg=NS(typing_loop=gate.wait);tasks=[]
  async def deliver(*args):await gate.wait()
  exec(compile(ast.Module(body=[node],type_ignores=[]),'<background wiring>','exec'),dict(tasks=tasks,asyncio=asyncio,supervise=supervise,item=self.item,controller=controller,tg=tg,deliver_schedules=deliver))
  await asyncio.sleep(.01)
  self.assertTrue(tasks[0].done());self.assertFalse(tasks[1].done());self.assertFalse(tasks[2].done());self.assertEqual(calls,[1])
  controller.closed=True;gate.set();await asyncio.gather(*tasks)
