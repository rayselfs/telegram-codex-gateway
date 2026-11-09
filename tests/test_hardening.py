import asyncio
import json
import sys
import time
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,patch
from test_gateway import directory
from gateway.core.state import Journal
from gateway.telegram.transport import Transport
from gateway.scheduling.schedules import Schedules,validate,deadline
from gateway.core.worker_supervisor import WorkerSupervisor
from gateway.core.health_file import write_json
from gateway.core.release import checked_files

class HardeningTests(unittest.IsolatedAsyncioTestCase):
    def test_release_preserves_historical_unknown_without_blocking_new_idle_thread(self):
        from gateway.core.release import CHAT_UNRESOLVED
        j=Journal(directory()/'j');self.addCleanup(j.close)
        j.job('old',None,'unknown')
        self.assertIsNotNone(j.db.execute(CHAT_UNRESOLVED).fetchone())
        j.set('thread','old')
        self.assertIsNotNone(j.db.execute(CHAT_UNRESOLVED).fetchone())
        j.set('thread','new');j.job('new','done','completed')
        self.assertIsNone(j.db.execute(CHAT_UNRESOLVED).fetchone())
        self.assertEqual(j.db.execute("SELECT state FROM jobs WHERE thread='old'").fetchone()[0],'unknown')
        j.job('old','active','running')
        self.assertIsNotNone(j.db.execute(CHAT_UNRESOLVED).fetchone())
    def test_release_blocks_completed_schedule_with_pending_notification(self):
        from gateway.core.release import SCHEDULE_UNRESOLVED
        store=Schedules(directory()/'s');self.addCleanup(store.close)
        store.db.execute("INSERT INTO runs(id,state,notification) VALUES('fixture','completed','ready')")
        self.assertIsNotNone(store.db.execute(SCHEDULE_UNRESOLVED).fetchone())
        store.db.execute("UPDATE runs SET notification='delivered'")
        self.assertIsNone(store.db.execute(SCHEDULE_UNRESOLVED).fetchone())
    async def test_restoring_worker_rejects_input_without_dispatch(self):
        from gateway.core.controller import Controller
        from test_gateway import FakeRPC
        root=directory();j=Journal(root/'j');self.addCleanup(j.close)
        rpc=FakeRPC(root);tg=NS(say=AsyncMock())
        c=Controller(rpc,tg,j,root);c.initializing=True
        await c.start([{'type':'text','text':'fixture'}])
        await c.handle('   ')
        self.assertEqual(rpc.calls,[]);self.assertFalse(c.busy)
        self.assertEqual(j.db.execute('SELECT count(*) FROM jobs').fetchone()[0],0)
    async def test_partial_delivery_retains_receipt_and_never_retries_unknown(self):
        root=directory();j=Journal(root/'journal');self.addCleanup(j.close)
        tg=Transport('fake',1,root);tg.controller=NS(journal=j,thread='t',turn='r')
        tg._bot=NS(send_message=AsyncMock(side_effect=[NS(message_id=11),TimeoutError()]))
        with self.assertRaises(TimeoutError):await tg.say('x'*2100)
        self.assertEqual(j.db.execute('SELECT part,state,receipt FROM outbound_chunks ORDER BY part').fetchall(),[(0,'delivered',11),(1,'unknown',None)])
        self.assertEqual(tg._bot.send_message.await_count,2)
    async def test_worker_budget_resets_only_after_one_healthy_hour(self):
        j=Journal(directory()/'j');self.addCleanup(j.close)
        s=WorkerSupervisor(j,AsyncMock(),AsyncMock());s.state['attempt']=2
        await s.tick(failed=False,now=100);await s.tick(failed=False,now=3699)
        self.assertEqual(s.state['attempt'],2)
        await s.tick(failed=False,now=3700);self.assertEqual(s.state['attempt'],0)
    def test_atomic_health_and_code_manifest_validation(self):
        root=directory();p=root/'health.json';write_json(p,{'ready':True});self.assertEqual(json.loads(p.read_text()),{'ready':True})
        self.assertFalse(list(root.glob('*.tmp')))
        (root/'ok.py').write_text('x=1')
        self.assertEqual(checked_files(root,['ok.py'])[0]['name'],'ok.py')
        for name in ['../ok.py','auth.sqlite','D:/auth.json']:
            with self.assertRaises(ValueError):checked_files(root,[name])
    def test_grace_changes_expiry_without_changing_default(self):
        spec={'name':'fake','kind':'reminder','content':'fixture','schedule':{'kind':'daily','time':'19:00'}}
        validate(spec);self.assertEqual(deadline({'due':0,'spec':json.dumps(spec)}),900)
        validate({**spec,'grace_minutes':60})
        for v in [0,121,True]:
            with self.assertRaises(ValueError):validate({**spec,'grace_minutes':v})
    def test_real_guard_child_exits_are_bounded_and_persisted(self):
        from gateway.core import gateway_guard
        from pathlib import Path
        root=directory();(root/'profile').mkdir();(root/'bridge').mkdir()
        (root/'bridge/gateway').mkdir()
        (root/'bridge/gateway/__main__.py').write_text('raise SystemExit(1)')
        (root/'bridge/gateway/__init__.py').touch()
        with patch.object(gateway_guard,'ROOT',root),patch.object(gateway_guard,'CODE_ROOT',root/'bridge'),patch.object(gateway_guard,'PYTHON',Path(sys.executable)),patch.object(gateway_guard.time,'sleep'):
            gateway_guard.main()
            saved=json.loads((root/'profile/guard-state.json').read_text())
            self.assertEqual(saved['phase'],'exhausted');self.assertEqual(len(saved['restarts']),3)
            with patch.object(gateway_guard.subprocess,'Popen') as spawn:
                gateway_guard.main();spawn.assert_not_called()
    def test_guard_respects_intentional_stop(self):
        from gateway.core import gateway_guard
        root=directory();(root/'profile').mkdir();(root/'profile/stop.request').touch()
        with patch.object(gateway_guard,'ROOT',root),patch.object(gateway_guard.subprocess,'Popen') as spawn:
            gateway_guard.main();spawn.assert_not_called()
