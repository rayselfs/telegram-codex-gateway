import importlib.util
import tempfile
from pathlib import Path
import unittest

class MembersTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def registry(self):
        self.assertIsNotNone(importlib.util.find_spec('gateway.core.members'), 'member registry missing')
        from gateway.core.members import Members
        store = Members(self.root / 'members.sqlite', self.root)
        self.addCleanup(store.close)
        rows = [dict(owner_id=n, name=f'member_{n}', home=str(self.root/f'{n}/profile'),
                     workspace=str(self.root/f'{n}/workspace')) for n in (101, 102)]
        for row in rows:
            Path(row['home']).mkdir(parents=True, exist_ok=True)
            Path(row['workspace']).mkdir(parents=True, exist_ok=True)
        store.import_existing(rows, 101)
        return store, rows

    def test_migration_idempotent_preserves_active_and_paths(self):
        store, rows = self.registry()
        before = store.active_profiles()
        store.import_existing(rows, 101)
        self.assertEqual(before, store.active_profiles())
        self.assertEqual(store.require_admin(101)['role'], 'admin')
        self.assertEqual(store.require_active(102)['role'], 'staff')
        with self.assertRaises(PermissionError): store.require_admin(102)

    def test_unapproved_members_consume_no_ai_credits(self):
        store, _ = self.registry()
        token = store.invite(101)
        store.claim(token, 103, 'member_c')
        calls = []
        for owner in (999, 103):
            for entry in ('text', 'voice', 'attachment', 'callback', 'worker', 'billable_tool'):
                with self.assertRaises(PermissionError):
                    store.require_active(owner)
                    calls.append(entry)
        store.suspend(101, 102, 'test')
        with self.assertRaises(PermissionError):
            store.require_active(102); calls.append('suspended')
        self.assertEqual(calls, [])

    def test_only_admin_can_invite_and_activate(self):
        store, _ = self.registry()
        for actor in (102, 999):
            with self.assertRaises(PermissionError): store.invite(actor)
            with self.assertRaises(PermissionError): store.approve(actor, 103)
        token = store.invite(101); store.claim(token, 103, 'member_c')
        with self.assertRaises(PermissionError): store.activate(101, 103, lambda _: True)
        store.approve(101, 103)
        store.provision(101, 103)
        with self.assertRaises(PermissionError): store.activate(102, 103, lambda _: True)
        with self.assertRaises(RuntimeError): store.activate(101, 103, lambda _: False)
        store.activate(101, 103, lambda _: True)
        self.assertEqual(len(store.active_profiles()), 3)

    def test_restoring_never_activated_member_still_requires_health_activation(self):
        store,_=self.registry();store.claim(store.invite(101),103,'member_c')
        store.approve(101,103);store.provision(101,103)
        store.suspend(101,103,'registration hold');store.restore(101,103,'resume registration')
        with self.assertRaises(PermissionError):store.require_active(103)
        self.assertEqual(store.require_onboarding(103)['state'],'pending')
        with self.assertRaises(RuntimeError):store.activate(101,103,lambda _:False)

    def test_invite_replay_and_provision_retry(self):
        store, _ = self.registry()
        token = store.invite(101)
        store.claim(token, 103, 'member_c'); store.claim(token, 103, 'member_c')
        with self.assertRaises(PermissionError): store.claim(token, 104, 'same name')
        store.approve(101, 103)
        one = store.provision(101, 103); two = store.provision(101, 103)
        self.assertEqual(one, two)
        self.assertEqual(len(store.list_members(101)), 3)
        self.assertFalse((Path(one['home'])/'codex-home/auth.json').exists())

    def test_suspend_rollback_never_reactivates_member(self):
        store, rows = self.registry()
        store.suspend(101, 102, 'access ended')
        store.import_existing(rows, 101)
        self.assertEqual([r['owner_id'] for r in store.active_profiles()], [101])
        with self.assertRaises(PermissionError): store.suspend(101, 101, 'attempt')
        store.restore(101, 102, 'resume')
        self.assertTrue(store.require_active(102)['schedules_paused'])
        store.resume_own_schedules(102)
        self.assertFalse(store.require_active(102)['schedules_paused'])

    def test_migration_rejects_forgery_duplicates_and_overlap(self):
        store, rows = self.registry()
        with self.assertRaises(ValueError): store.import_existing(rows + rows, 101)
        bad = [dict(r) for r in rows]; bad[1]['workspace'] = bad[0]['workspace']
        with self.assertRaises(ValueError): store.import_existing(bad, 101)
        with self.assertRaises(ValueError): store.import_existing(rows, 102)
        with self.assertRaises(ValueError): store.require_active(True)

    def test_expired_invite_and_pending_login_do_not_affect_active(self):
        store, _ = self.registry()
        token = store.invite(101, now=10)
        with self.assertRaises(PermissionError): store.claim(token, 103, 'member_c', now=90000)
        self.assertEqual(len(store.active_profiles()), 2)
        token = store.invite(101); store.claim(token, 103, 'member_c')
        with self.assertRaises(PermissionError): store.require_onboarding(103)
        store.approve(101, 103)
        self.assertEqual(store.require_onboarding(103)['state'], 'pending')
        self.assertEqual(len(store.active_profiles()), 2)

if __name__ == '__main__': unittest.main()
