import copy
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from controller import Controller
from state import Journal
from test_gateway import directory,FakeRPC

class TrustTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.root=directory();self.home=self.root/'codex-home';self.home.mkdir()
        self.config=self.home/'config.toml'
        self.config.write_text('[mcp_servers.notion]\nurl="https://example.invalid/notion"\n[mcp_servers.future]\ncommand="fake"\n',encoding='utf-8')
        self.j=Journal(self.root/'transport.sqlite');self.rpc=FakeRPC(self.root)
        self.tg=NS(say=AsyncMock(),buttons=AsyncMock())
        self.c=Controller(self.rpc,self.tg,self.j,self.root,personal_instructions=self.home/'AGENTS.md')
        self.c.thread='thread';self.c.turn='turn';self.c.busy=True
        self.req={'id':10,'method':'mcpServer/elicitation/request','params':{'serverName':'notion','threadId':'thread','turnId':'turn','mode':'form','requestedSchema':{'type':'object','properties':{'confirm':{'type':'boolean'}}}}}
    async def asyncTearDown(self):self.j.close()
    async def test_default_prompt_then_trust_button_auto_accepts_next_request(self):
        await self.c.server_request(copy.deepcopy(self.req))
        key=next(iter(self.c.pending));await self.c.show_request(key)
        self.assertEqual(len(self.c.pending[key]['choices']),3)
        await self.c.callback(NS(data='cg:'+key+':2',answer=AsyncMock()))
        self.assertIn('notion',self.j.get('mcp_trust'))
        await self.c.server_request(copy.deepcopy(self.req))
        self.assertEqual(len(self.rpc.responses),2);self.assertFalse(self.c.pending)
    async def test_revoke_change_of_endpoint_and_other_owner_do_not_inherit(self):
        await self.c.handle('/trust notion');self.assertTrue(self.c.auto_consent(self.req))
        other=Journal(self.root/'other.sqlite')
        try:
            c=Controller(FakeRPC(self.root),NS(),other,self.root,personal_instructions=self.home/'AGENTS.md')
            c.thread='thread';c.turn='turn';c.busy=True
            self.assertFalse(c.auto_consent(self.req))
        finally:other.close()
        self.config.write_text('[mcp_servers.notion]\nurl="https://example.invalid/changed"',encoding='utf-8')
        self.assertFalse(self.c.auto_consent(self.req))
        await self.c.handle('/trust notion');await self.c.handle('/trust revoke notion')
        self.assertFalse(self.c.auto_consent(self.req))
    async def test_trusted_service_still_rejects_login_data_and_stale_requests(self):
        await self.c.handle('/trust notion')
        for change in [{'mode':'url'},{'requestedSchema':{'type':'object','properties':{'password':{'type':'string'}}}}, {'threadId':'other'},{'turnId':'old'}]:
            req=copy.deepcopy(self.req);req['params'].update(change)
            self.assertFalse(self.c.auto_consent(req))
        self.c.busy=False;self.assertFalse(self.c.auto_consent(self.req))
    async def test_future_service_requires_explicit_opt_in(self):
        req=copy.deepcopy(self.req);req['params']['serverName']='future'
        self.assertFalse(self.c.auto_consent(req))
        await self.c.handle('/trust future');self.assertTrue(self.c.auto_consent(req))
    async def test_background_checks_live_revocation_not_snapshot(self):
        await self.c.handle('/trust notion');self.c.interactive=False
        self.assertTrue(self.c.auto_consent(self.req))
        self.j.set('mcp_trust',{});self.assertFalse(self.c.auto_consent(self.req))
