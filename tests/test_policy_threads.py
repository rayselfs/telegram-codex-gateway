from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock
from gateway.core.members import Members
from gateway.core.shared_policy import SharedPolicy
from gateway.core.controller import Controller
from gateway.core.state import Journal
from test_gateway import FakeRPC

class PolicyThreadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
        self.members=Members(self.root/'members.sqlite',self.root);self.addCleanup(self.members.close)
        self.members.import_existing([dict(owner_id=101,name='admin_a',home=str(self.root/'profile'),workspace=str(self.root/'workspace'))],101)
        self.policy=SharedPolicy(self.members);self.policy.import_initial('initial')
        self.journal=Journal(self.root/'transport.sqlite');self.addCleanup(self.journal.close)
        self.rpc=FakeRPC(self.root)
        self.tg=NS(say=AsyncMock(return_value=[1]),buttons=AsyncMock())
        self.c=Controller(self.rpc,self.tg,self.journal,self.root)
        self.c.shared_policy=self.policy

    async def test_approved_revision_reaches_existing_and_new_threads(self):
        await self.c.new()
        start=next(p for m,p in self.rpc.calls if m=='thread/start')
        self.assertEqual(start.get('developerInstructions'),'initial')
        p=self.policy.propose(101,1,'approved revision');self.policy.vote(101,p['id'],p['hash'],True)
        await self.c.start([{'type':'text','text':'test'}])
        injection=[p for m,p in self.rpc.calls if m=='thread/inject_items'][-1]
        self.assertEqual(injection['items'][0]['content'][0]['text'],'approved revision')
        from gateway.core.bootstrap import ROOT
        schema=ROOT/'evidence/schema-experimental/v2/ThreadInjectItemsParams.json'
        if schema.is_file():
            import json,jsonschema
            jsonschema.validate(injection,json.loads(schema.read_text(encoding='utf-8')))
        self.assertEqual(self.journal.get('persona_version:'+self.c.thread),2)

    async def test_sync_failure_blocks_next_inference(self):
        self.c.thread='existing';self.journal.set('thread','existing')
        original=self.rpc.call
        async def fail(method,params):
            if method=='thread/inject_items':raise RuntimeError('synthetic failure')
            return await original(method,params)
        self.rpc.call=fail
        await self.c.start([{'type':'text','text':'test'}])
        self.assertFalse(any(m=='turn/start' for m,p in self.rpc.calls))
        self.assertIsNone(self.journal.get('persona_version:existing'))

if __name__=='__main__':unittest.main()
