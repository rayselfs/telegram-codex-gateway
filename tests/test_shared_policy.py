import importlib.util
import tempfile
from pathlib import Path
import unittest

class SharedPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def policy(self,mode='all_active_members'):
        self.assertIsNotNone(importlib.util.find_spec('gateway.core.shared_policy'),'shared approval missing')
        from gateway.core.members import Members
        from gateway.core.shared_policy import SharedPolicy
        members=Members(self.root/'members.sqlite',self.root);self.addCleanup(members.close)
        members.import_existing([dict(owner_id=n,name=str(n),home=str(self.root/f'{n}/profile'),workspace=str(self.root/f'{n}/workspace')) for n in (101,102,103)],101)
        policy=SharedPolicy(members)
        policy.import_initial('original',mode)
        return policy,members

    def test_all_active_members_must_approve_same_body(self):
        policy,members=self.policy();p=policy.propose(102,1,'new')
        policy.vote(101,p['id'],p['hash'],True)
        policy.vote(103,p['id'],p['hash'],True)
        self.assertEqual(policy.active_persona()['body'],'original')
        policy.vote(102,p['id'],p['hash'],True)
        self.assertEqual(policy.active_persona()['version'],2)
        with self.assertRaises(ValueError): policy.vote(102,p['id'],p['hash'],True)
        self.assertEqual(policy.active_persona()['version'],2)

    def test_membership_or_base_change_requires_new_approval(self):
        policy,members=self.policy();p=policy.propose(101,1,'new')
        policy.vote(101,p['id'],p['hash'],True)
        members.suspend(101,103,'test')
        with self.assertRaises(ValueError): policy.vote(102,p['id'],p['hash'],True)
        self.assertEqual(policy.active_persona()['version'],1)
        a=policy.propose(101,1,'a');b=policy.propose(101,1,'b')
        for owner in (101,102):policy.vote(owner,a['id'],a['hash'],True)
        with self.assertRaises(ValueError):policy.vote(101,b['id'],b['hash'],True)

    def test_policy_change_requires_current_policy_votes(self):
        policy,_=self.policy();p=policy.propose(101,1,'new',policy='admin_only')
        policy.vote(101,p['id'],p['hash'],True)
        self.assertEqual(policy.active_persona()['policy'],'all_active_members')
        for owner in (102,103):policy.vote(owner,p['id'],p['hash'],True)
        self.assertEqual(policy.active_persona()['policy'],'admin_only')

    def test_rejection_hash_and_expiry_cannot_publish(self):
        policy,_=self.policy();p=policy.propose(101,1,'new',now=0)
        with self.assertRaises(ValueError): policy.vote(101,p['id'],'wrong',True,now=1)
        with self.assertRaises(ValueError): policy.vote(101,p['id'],p['hash'],True,now=604801)
        p=policy.propose(101,1,'other')
        policy.vote(102,p['id'],p['hash'],False)
        with self.assertRaises(ValueError): policy.vote(101,p['id'],p['hash'],True)
        self.assertEqual(policy.active_persona()['version'],1)

    def test_public_admin_policy_and_empty_initial_body(self):
        policy,members=self.policy('admin_only')
        p=policy.propose(102,1,'')
        with self.assertRaises(PermissionError):policy.vote(102,p['id'],p['hash'],True)
        policy.vote(101,p['id'],p['hash'],True)
        self.assertEqual(policy.active_persona()['body'],'')
        with self.assertRaises(ValueError):policy.import_initial('overwrite','admin_only')

if __name__=='__main__':unittest.main()
