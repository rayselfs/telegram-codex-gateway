import json
import tempfile
import time
import unittest
from pathlib import Path
from gateway.core.members import Members
from gateway.core.capabilities import Capabilities
from schedules import Schedules

class ScheduleAuthorizationTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        self.members=Members(self.root/'members.sqlite',self.root);self.addCleanup(self.members.close)
        self.members.import_existing([dict(owner_id=n,name=str(n),home=str(self.root/str(n)/'profile'),workspace=str(self.root/str(n)/'workspace')) for n in (101,102)],101)
        self.capabilities=Capabilities(self.members)
        self.capabilities.register('shared_content',lambda context,args:None,{'publish':'admin'})

    def store(self,owner):
        store=Schedules(self.root/str(owner)/'schedules.sqlite',members=self.members,owner=owner,capabilities=self.capabilities)
        self.addCleanup(store.close);return store

    def spec(self):
        return dict(name='shared update',kind='capability',content='verified update',capability='shared_content',action='publish',arguments={},schedule={'kind':'interval','minutes':5})

    def test_staff_denied_at_propose_confirm_manual_enqueue_and_claim(self):
        store=self.store(102);spec=self.spec()
        with self.assertRaises(PermissionError):store.propose('create',spec=spec)
        # Legacy or tampered rows cannot bypass the execution boundary.
        store.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?)',('task',json.dumps(spec),1,time.time()-1,1,None,None))
        store.db.commit()
        with self.assertRaises(PermissionError):store.run_authorized('task','telegram:102:1')
        store.enqueue()
        self.assertEqual(store.runs(),[])
        store.db.execute('INSERT INTO proposals VALUES(?,?,?,?,?,?,?,?)',('p','create',None,json.dumps(spec),0,time.time()+60,None,None))
        store.db.commit()
        with self.assertRaises(PermissionError):store.confirm('p')
        store._insert_run('task',time.time()-1,json.dumps(spec),None,None);store.db.commit()
        self.assertIsNone(store.claim())
        self.assertEqual(store.runs()[0]['state'],'blocked')

    def test_admin_authorized_then_revocation_before_claim_stops_run(self):
        store=self.store(101)
        key,_=store.propose('create',spec=self.spec());store.confirm(key)
        task=store.tasks()[0]['id'];store.run_authorized(task,'telegram:101:1')
        self.capabilities.handlers.clear()
        self.assertIsNone(store.claim())
        self.assertEqual(store.runs()[0]['state'],'blocked')

    def test_suspended_member_cannot_confirm_personal_work(self):
        store=self.store(102);spec=dict(name='reminder',kind='reminder',content='hello',schedule={'kind':'interval','minutes':5})
        key,_=store.propose('create',spec=spec)
        self.members.suspend(101,102,'test')
        with self.assertRaises(PermissionError):store.confirm(key)
        with self.assertRaises(PermissionError):store.enqueue()
        self.assertEqual(store.db.execute('SELECT count(*) FROM tasks').fetchone()[0],0)

    def test_worker_dispatch_binds_actor_and_run_and_revocation_prevents_handler(self):
        import asyncio
        from schedule_worker import execute_capability
        calls=[]
        self.capabilities.handlers['shared_content']=(lambda context,args:calls.append(dict(context)) or {'done':True},{'publish':'admin'})
        store=self.store(101);key,_=store.propose('create',spec=self.spec());store.confirm(key)
        task=store.tasks()[0]['id'];store.run_authorized(task,'telegram:101:1');run=store.claim()
        asyncio.run(execute_capability({'owner_id':101},run,store,self.capabilities))
        self.assertEqual(calls[0]['owner_id'],101)
        self.assertEqual(calls[0]['request_id'],'schedule:'+run['id'])
        self.assertEqual(store.runs()[0]['state'],'completed')
        store.run_authorized(task,'telegram:101:2');run=store.claim()
        self.capabilities.handlers.clear()
        asyncio.run(execute_capability({'owner_id':101},run,store,self.capabilities))
        self.assertEqual(len(calls),1)
        self.assertEqual(store.runs()[0]['state'],'blocked')

if __name__=='__main__':unittest.main()
