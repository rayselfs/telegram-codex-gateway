import importlib.util
import unittest
from unittest.mock import AsyncMock
from types import SimpleNamespace
from state import Journal
from test_gateway import directory


class SupervisorTests(unittest.IsolatedAsyncioTestCase):
    async def test_normal_gateway_starts_do_not_exhaust_crash_budget(self):
        from worker_supervisor import WorkerSupervisor
        j=Journal(directory()/'journal.sqlite');self.addCleanup(j.close)
        restore=AsyncMock()
        for _ in range(4):
            s=WorkerSupervisor(j,AsyncMock(),restore)
            await s.start(now=0)
            self.assertEqual(s.state['phase'],'ready')
            self.assertEqual(s.state['attempt'],0)
        self.assertEqual(s.state['generation'],4)
        self.assertEqual(restore.await_count,4)

    async def test_failed_cold_start_preserves_budget_and_backoff_across_gateway(self):
        from worker_supervisor import WorkerSupervisor
        j=Journal(directory()/'journal.sqlite');self.addCleanup(j.close)
        restore=AsyncMock(side_effect=RuntimeError('failed'))
        s=WorkerSupervisor(j,AsyncMock(),restore);await s.start(now=0)
        self.assertEqual(s.state['attempt'],1)
        s=WorkerSupervisor(j,AsyncMock(),restore);await s.start(now=1)
        await s.tick(failed=True,now=14)
        self.assertEqual(restore.await_count,1)
        await s.tick(failed=True,now=15)
        await s.tick(failed=True,now=75)
        await s.start(now=1000)
        self.assertEqual(restore.await_count,3)
        self.assertEqual(s.state['phase'],'exhausted')

    async def test_cold_start_does_not_bypass_persisted_failure_gates(self):
        from worker_supervisor import WorkerSupervisor
        j=Journal(directory()/'journal.sqlite');self.addCleanup(j.close)
        for phase in ('waiting','exhausted','unknown','starting'):
            j.set('worker_supervisor',{'phase':phase,'attempt':2,'generation':1,'due':100})
            restore=AsyncMock();s=WorkerSupervisor(j,AsyncMock(),restore)
            await s.start(now=200)
            restore.assert_not_awaited()

    async def test_persisted_wait_does_not_replace_a_live_worker(self):
        from worker_supervisor import WorkerSupervisor
        root=directory();j=Journal(root/'journal.sqlite');self.addCleanup(j.close)
        j.set('worker_supervisor',{'phase':'waiting','attempt':1,'generation':0,'due':0})
        restore=AsyncMock();s=WorkerSupervisor(j,AsyncMock(),restore)
        await s.tick(failed=False,now=100)
        restore.assert_not_awaited()
        self.assertEqual(s.state['phase'],'unknown')

    async def test_bounded_persisted_restart_never_replays_other_worker(self):
        self.assertIsNotNone(importlib.util.find_spec('worker_supervisor'), 'worker supervisor missing')
        from worker_supervisor import WorkerSupervisor
        root = directory(); journal=Journal(root/'journal.sqlite'); self.addCleanup(journal.close)
        restore=AsyncMock(side_effect=RuntimeError('startup failed'))
        quiesce=AsyncMock()
        peer=AsyncMock()
        s=WorkerSupervisor(journal,quiesce,restore)
        await s.tick(failed=True,now=100)
        self.assertEqual(quiesce.await_count,1)
        await s.tick(failed=True,now=114)
        self.assertEqual(restore.await_count,0)
        await s.tick(failed=True,now=115)
        self.assertEqual(restore.await_count,1)
        s=WorkerSupervisor(journal,quiesce,restore)
        await s.tick(failed=True,now=145)
        await s.tick(failed=True,now=205)
        await s.tick(failed=True,now=999)
        self.assertEqual(restore.await_count,3)
        self.assertEqual(s.state['phase'],'exhausted')
        peer.assert_not_awaited()

    async def test_restart_success_increments_generation_without_replay(self):
        self.assertIsNotNone(importlib.util.find_spec('worker_supervisor'), 'worker supervisor missing')
        from worker_supervisor import WorkerSupervisor
        root=directory();journal=Journal(root/'journal.sqlite');self.addCleanup(journal.close)
        restore=AsyncMock();quiesce=AsyncMock()
        s=WorkerSupervisor(journal,quiesce,restore)
        await s.tick(failed=True,now=0)
        await s.tick(failed=True,now=15)
        self.assertEqual(s.state['generation'],1)
        self.assertEqual(s.state['phase'],'ready')
        self.assertEqual(s.state['attempt'],1)
        restore.assert_awaited_once_with()
        # A worker crash loop does not regain an unlimited budget on success.
        await s.tick(failed=True,now=16)
        self.assertEqual(s.state['attempt'],1)

    async def test_interrupted_dispatch_needs_inspection_and_no_arbitrary_target(self):
        self.assertIsNotNone(importlib.util.find_spec('worker_supervisor'), 'worker supervisor missing')
        from worker_supervisor import WorkerSupervisor
        root=directory();journal=Journal(root/'journal.sqlite');self.addCleanup(journal.close)
        journal.set('worker_supervisor',{'phase':'starting','attempt':1,'generation':0,'due':0})
        restore=AsyncMock();s=WorkerSupervisor(journal,AsyncMock(),restore)
        await s.tick(failed=True,now=500)
        restore.assert_not_awaited()
        self.assertEqual(s.state['phase'],'unknown')
        with self.assertRaises(ValueError): s.request({'action':'restart','pid':123})
