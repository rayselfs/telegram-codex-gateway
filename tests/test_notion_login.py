import io
import time
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch
from oauth_login import ServiceLogin, authorization_details, validate_callback
from test_gateway import directory


class OAuthBoundaryTests(unittest.TestCase):
    def test_provider_origin_cannot_cross(self):
        url='https://mcp.canva.com/authorize?state=canva-state&redirect_uri=http%3A%2F%2F127.0.0.1%3A3406%2Fcallback'
        self.assertEqual(authorization_details(url,'canva')[0],'canva-state')
        with self.assertRaises(ValueError):authorization_details(url,'notion')
        with self.assertRaises(ValueError):authorization_details(url.replace('mcp.canva.com','mcp.notion.com'),'canva')
    def test_origin_state_and_redirect(self):
        url='https://mcp.notion.com/authorize?state=owner-one&redirect_uri=http%3A%2F%2F127.0.0.1%3A3406%2Fcallback'
        state,redirect=authorization_details(url)
        callback=redirect+'?code=one-time-code&state=owner-one'
        self.assertEqual(validate_callback(callback,state,redirect),callback)
        for bad in [callback.replace('3406','3407'),callback.replace('owner-one','owner-two'),
                    callback.replace('127.0.0.1','evil.example'),callback+'&state=owner-one',
                    callback+'\n',callback+'#fragment',redirect+'?state=owner-one']:
            with self.assertRaises(ValueError):validate_callback(bad,state,redirect)
        with self.assertRaises(ValueError):authorization_details(url.replace('mcp.notion.com','evil.example'))


class OAuthFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_canva_selection_and_active_login_not_retargeted(self):
        flow=self.login();flow.start=AsyncMock()
        self.assertTrue(await flow.handle(NS(),'/login canva'))
        self.assertEqual(flow.service,'canva')
        flow.start.assert_awaited_once()
        flow.proc=NS()
        await flow.handle(NS(),'/login notion')
        self.assertEqual(flow.service,'canva')
        self.assertEqual(flow.start.await_count,1)

    def login(self):
        tg=NS(say=AsyncMock(),controller=NS(busy=False,pending={},rpc=NS(call=AsyncMock())))
        home=directory();(home/'codex-home').mkdir()
        (home/'codex-home/config.toml').write_text('[mcp_servers.notion]\nurl="https://mcp.notion.com/mcp"\n[mcp_servers.canva]\nurl="https://mcp.canva.com/mcp"\n',encoding='utf-8')
        tg.controller.rpc.call.return_value={'data':[]}
        return ServiceLogin(tg,home)

    async def test_callback_consumed_deleted_and_not_logged(self):
        flow=self.login();flow.proc=NS(stdin=io.BytesIO());flow.state='member-a-state'
        flow.redirect='http://127.0.0.1:3406/callback';flow.started=time.monotonic()
        msg=NS(delete=AsyncMock())
        url=flow.redirect+'?code=one-time-secret&state=member-a-state'
        self.assertTrue(await flow.handle(msg,url))
        msg.delete.assert_awaited_once()
        self.assertTrue(flow.submitted)
        self.assertEqual(flow.proc.stdin.getvalue(),(url+'\n').encode())
        flow.tg.say.assert_not_awaited()
        self.assertTrue(await flow.handle(msg,url))
        self.assertEqual(flow.proc.stdin.getvalue(),(url+'\n').encode())

    async def test_other_owner_stale_and_expired_callbacks_not_forwarded(self):
        flow=self.login();msg=NS(delete=AsyncMock())
        url='http://127.0.0.1:3406/callback?code=secret&state=member-a-state'
        self.assertTrue(await flow.handle(msg,url))
        flow.proc=NS(stdin=io.BytesIO());flow.state='member-b-state'
        flow.redirect='http://127.0.0.1:3406/callback';flow.started=time.monotonic()
        self.assertTrue(await flow.handle(msg,url))
        self.assertEqual(flow.proc.stdin.getvalue(),b'')
        flow.state='member-a-state';flow.started-=601
        self.assertTrue(await flow.handle(msg,url))
        self.assertEqual(flow.proc.stdin.getvalue(),b'')
        self.assertNotIn('secret',str(flow.tg.say.call_args_list))

    async def test_busy_does_not_start_and_normal_text_not_consumed(self):
        flow=self.login();flow.start=AsyncMock();flow.tg.controller.busy=True
        self.assertTrue(await flow.handle(NS(),'/login notion'))
        flow.start.assert_not_awaited()
        self.assertFalse(await flow.handle(NS(),'ordinary work'))

    async def test_connected_requires_tools_and_native_auth(self):
        flow=self.login()
        flow.tg.controller.rpc.call.return_value={'data':[{'name':'notion','authStatus':'oAuth','tools':{}}]}
        await flow.status()
        self.assertNotIn('工具可用',flow.tg.say.call_args.args[0])
        flow.tg.controller.rpc.call.return_value={'data':[{'name':'notion','authStatus':'oAuth','tools':{'search':{}}}]}
        await flow.status()
        self.assertIn('工具可用',flow.tg.say.call_args.args[0])


class DynamicOAuthTests(OAuthFlowTests):
    async def test_dynamic_xmind_preserves_existing_login(self):
        flow=self.login();config=flow.home/'codex-home/config.toml'
        config.write_text(config.read_text()+'[mcp_servers.xmind]\nurl="https://app.xmind.com/api/mcp"\n',encoding='utf-8')
        flow.tg.controller.rpc.call.return_value={'data':[{'name':'xmind','authStatus':'oAuth','tools':{'list':{}}}]}
        flow.start=AsyncMock()
        await flow.handle(NS(),'/login xmind');flow.start.assert_not_awaited()
        self.assertIn('沿用既有登入',flow.tg.say.call_args.args[0])
        await flow.handle(NS(),'/login_status xmind')
        self.assertIn('工具可用',flow.tg.say.call_args.args[0])
    async def test_future_service_discovered_without_code_change_and_owner_scoped(self):
        flow=self.login();config=flow.home/'codex-home/config.toml'
        with config.open('a',encoding='utf-8') as f:f.write('[mcp_servers.future]\nurl="https://example.invalid/mcp"\n[mcp_servers.disabled]\nurl="https://example.invalid/mcp"\nenabled=false\n')
        flow.start=AsyncMock();await flow.handle(NS(),'/login future');flow.start.assert_awaited_once()
        for name in ['disabled','other-owner','--help']:
            await flow.handle(NS(),'/login '+name)
        self.assertEqual(flow.start.await_count,1)
        another=self.login();another.start=AsyncMock();await another.handle(NS(),'/login future');another.start.assert_not_awaited()
    async def test_status_pagination_and_stdio_no_oauth(self):
        flow=self.login();flow.tg.controller.rpc.call.side_effect=[{'data':[],'nextCursor':'second'},{'data':[{'name':'canva','authStatus':'oAuth','tools':{'x':{}}}]}]
        await flow.status('canva');self.assertIn('工具可用',flow.tg.say.call_args.args[0])
        flow.tg.controller.rpc.call.side_effect=None;flow.tg.controller.rpc.call.return_value={'data':[]}
        with (flow.home/'codex-home/config.toml').open('a') as f:f.write('[mcp_servers.local]\ncommand="fake"\n')
        flow.start=AsyncMock();await flow.handle(NS(),'/login local');flow.start.assert_not_awaited()
    async def test_spawn_failure_releases_owner_lock(self):
        from lifecycle import SingleInstance
        flow=self.login()
        with patch('oauth_login.subprocess.Popen',side_effect=OSError('fixture')):await flow.start()
        lock=SingleInstance(flow.home/'execution.lock');lock.close();self.assertFalse(flow.active)
    def test_native_discovered_external_authorization_host_still_requires_safe_callback(self):
        url='https://accounts.example.invalid/authorize?state=future&redirect_uri=http%3A%2F%2F127.0.0.1%3A3406%2Fcallback'
        state,redirect=authorization_details(url,'future');self.assertEqual(state,'future')
        for bad in [url.replace('https:','http:',1),url.replace('127.0.0.1','evil.example'),url+'#fragment']:
            with self.assertRaises(ValueError):authorization_details(bad,'future')
