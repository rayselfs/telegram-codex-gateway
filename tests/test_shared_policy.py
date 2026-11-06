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

    def test_unknown_notification_is_retained_and_never_resent(self):
        import asyncio
        policy,members=self.policy();proposal=policy.propose(101,1,'new')
        sent=[]
        async def send(owner,text):
            sent.append((owner,text))
            if owner==102:raise TimeoutError('unknown result')
            return [owner*10]
        asyncio.run(policy.notify(101,proposal['id'],send))
        asyncio.run(policy.notify(101,proposal['id'],send))
        self.assertEqual(len(sent),3)
        self.assertTrue(all(proposal['hash'] in text for _,text in sent))
        self.assertEqual({r['owner']:r['state'] for r in policy.view(101,proposal['id'])['notifications']},
                         {101:'delivered',102:'unknown',103:'delivered'})

    def test_simultaneous_final_votes_publish_exactly_one_revision(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from gateway.core.members import Members
        from gateway.core.shared_policy import SharedPolicy
        policy,members=self.policy();proposal=policy.propose(101,1,'new')
        policy.vote(101,proposal['id'],proposal['hash'],True)
        barrier=Barrier(2)
        def vote(owner):
            m=Members(self.root/'members.sqlite',self.root)
            try:
                p=SharedPolicy(m);barrier.wait(timeout=5)
                return p.vote(owner,proposal['id'],proposal['hash'],True)
            finally:m.close()
        with ThreadPoolExecutor(max_workers=2) as executor:list(executor.map(vote,(102,103)))
        self.assertEqual(policy.active_persona()['version'],2)
        self.assertEqual(policy.db.execute('SELECT count(*) FROM shared_versions').fetchone()[0],2)

    def test_rollback_is_a_new_proposal_under_current_policy(self):
        policy,members=self.policy();proposal=policy.propose(101,1,'new')
        for owner in (101,102,103):policy.vote(owner,proposal['id'],proposal['hash'],True)
        back=policy.propose_rollback(102,2,1)
        for owner in (101,102):policy.vote(owner,back['id'],back['hash'],True)
        self.assertEqual(policy.active_persona()['body'],'new')
        policy.vote(103,back['id'],back['hash'],True)
        self.assertEqual(policy.active_persona()['body'],'original')
        self.assertEqual(policy.active_persona()['version'],3)

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
