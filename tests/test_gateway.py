import asyncio
import io
import json
from pathlib import Path
import queue
import threading
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch
import uuid

from bootstrap import ROOT
from controller import Controller
from lifecycle import SingleInstance, ChildJob, decrypt_token
from rpc import Client
from state import Journal, safe_file, snapshot_file
from transport import Transport


def directory():
    p = ROOT / 'test-state' / uuid.uuid4().hex
    p.mkdir(parents=True)
    if not (ROOT/'AGENTS.md').exists():(ROOT/'AGENTS.md').write_text('Test policy',encoding='utf-8')
    return p


class FakeRPC:
    def __init__(self, workspace):
        self.failure = None
        self.calls, self.responses, self.errors = [], [], []
        self.notifications, self.requests = queue.Queue(), queue.Queue()
        self.workspace = workspace

    async def call(self, method, params=None):
        self.calls.append((method, params))
        if method == 'thread/start':
            return {'thread': {'id': 'thread1'}, 'sandbox':{'type':'workspaceWrite'}}
        if method == 'thread/resume':
            return {'thread': {'id':params['threadId']}, 'sandbox':{'type':'workspaceWrite'}}
        if method == 'turn/start':
            return {'turn': {'id': 'turn1'}}
        if method == 'thread/read':
            return {'thread': {'id': params['threadId'], 'cwd': str(self.workspace), 'status': {'type': 'idle'}}}
        if method == 'thread/items/list':
            return {'data': [{'item': {'type': 'agentMessage', 'text': 'result'}}], 'nextCursor': None}
        if method == 'thread/turns/list':
            return {'data': [{'id': 'turn1', 'status': 'completed'}]}
        return {}

    def respond(self, rid, result):
        self.responses.append((rid, result))

    def respond_error(self, rid, code, message):
        self.errors.append((rid, code))

    def take_notification(self):
        return None if self.notifications.empty() else self.notifications.get_nowait()

    def take_server_request(self):
        return None if self.requests.empty() else self.requests.get_nowait()


class MetadataTests(unittest.TestCase):
    def test_snapshot_opened_path_and_dpapi(self):
        root, outbox = directory(), directory()
        (root / 'result.txt').write_text('result', encoding='utf-8')
        copy, name = snapshot_file(root, 'result.txt', outbox)
        self.assertEqual(name, 'result.txt')
        self.assertEqual(copy.read_text(encoding='utf-8'), 'result')
        from vault import crypt
        fixture=root/'dpapi.bin';fixture.write_bytes(crypt(b'offline-secret-test',b'',True))
        self.assertEqual(decrypt_token(fixture), 'offline-secret-test')

    def test_duplicate_updates_and_restart_unknown(self):
        root = directory()
        journal = Journal(root / 'db.sqlite')
        self.assertTrue(journal.receive(44))
        self.assertFalse(journal.receive(44))
        journal.job('t', 'u', 'running')
        journal.delivery('t:u', 'pending')
        journal.close()
        journal = Journal(root / 'db.sqlite')
        self.assertEqual(journal.db.execute('SELECT state FROM jobs').fetchone()[0], 'unknown')
        self.assertEqual(journal.db.execute('SELECT state FROM delivery').fetchone()[0], 'unknown')
        journal.close()

    def test_paths(self):
        root = directory()
        (root / 'ok.txt').write_text('ok')
        self.assertEqual(safe_file(root, 'ok.txt'), root / 'ok.txt')
        for value in ('../secret', 'C:/secret', '\\\\server\\share\\secret', 'ok.txt:secret', ''):
            with self.assertRaises((ValueError, FileNotFoundError)):
                safe_file(root, value)

    def test_junction_escape(self):
        import subprocess
        root, outside = directory(), directory()
        (outside / 'secret').write_text('test')
        subprocess.run(['cmd.exe', '/c', 'mklink', '/J', str(root / 'escape'), str(outside)],
                       check=True, stdout=subprocess.DEVNULL)
        with self.assertRaises(ValueError):
            safe_file(root, 'escape/secret')

    def test_instance_lock(self):
        root = directory()
        first = SingleInstance(root / 'lock')
        try:
            with self.assertRaises(RuntimeError):
                SingleInstance(root / 'lock')
        finally:
            first.close()
        SingleInstance(root / 'lock').close()


class WireTests(unittest.TestCase):
    def test_concurrent_frames_and_ids(self):
        class PartialWriter(io.BytesIO):
            def write(self, value):
                return super().write(value[:13])
        client = Client.__new__(Client)
        client._write_lock, client._id_lock = threading.Lock(), threading.Lock()
        client._next_id, client._closed = 1, False
        client._proc = NS(stdin=PartialWriter())
        def send():
            for _ in range(25):
                client._send({'id': client._take_id(), 'text': '文字' * 100})
        workers = [threading.Thread(target=send) for _ in range(4)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        frames = [json.loads(line) for line in client._proc.stdin.getvalue().splitlines()]
        self.assertEqual(len(frames), 100)
        self.assertEqual(len({f['id'] for f in frames}), 100)

    def test_delta_backpressure(self):
        client = Client.__new__(Client)
        client._notifications, client._server_requests = queue.Queue(), queue.Queue()
        client.failure = None
        client._proc = NS(terminate=Mock())
        for _ in range(1000):
            client._dispatch({'method': 'item/agentMessage/delta', 'params': {'delta': 'x'}})
        self.assertTrue(client._notifications.empty())
        for _ in range(2049):
            client._dispatch({'method': 'item/completed', 'params': {}})
        self.assertEqual(client._notifications.qsize(), 2048)
        client._proc.terminate.assert_called_once()

    def test_real_child_environment_and_job_cleanup(self):
        from bootstrap import CODEX
        if not CODEX.exists():self.skipTest('Pinned Codex binary not installed')
        import os
        import subprocess
        home = directory()
        (home / 'codex-home').mkdir()
        original = subprocess.Popen
        captured = {}
        def popen(*args, **kwargs):
            captured.update(kwargs['env'])
            return original(*args, **kwargs)
        with patch('codex_wire.subprocess.Popen', side_effect=popen):
            client = Client(home)
        job = ChildJob(client._proc)
        try:
            client.initialize(client_name='gateway_contract_test', capabilities={'experimentalApi': True})
            self.assertEqual(captured['CODEX_HOME'], str(home / 'codex-home'))
            self.assertFalse(any(k in captured for k in ('TELEGRAM_BOT_TOKEN', 'OPENAI_API_KEY', 'CODEX_API_KEY', 'HERMES_KANBAN_TASK')))
            job.close()
            client._proc.wait(timeout=5)
            self.assertIsNotNone(client._proc.returncode)
        finally:
            client.close()
            job.close()


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tg = Transport('123456:fake-token-for-offline-tests', 1001, directory())
        self.tg.controller = NS(callback=AsyncMock(), journal=NS(receive=Mock()), handle=AsyncMock())

    async def test_unauthorized_ingress_has_no_side_effects(self):
        for user, chat, bot, kind in [(1002,1002,False,'private'), (1001,1001,True,'private'),
                                    (1001,99,False,'private'), (1001,1001,False,'group')]:
            update = NS(effective_user=NS(id=user, is_bot=bot), effective_chat=NS(id=chat, type=kind))
            await self.tg.ingress(update, None)
        self.tg.controller.journal.receive.assert_not_called()
        self.tg.controller.callback.assert_not_called()
        self.tg.controller.handle.assert_not_called()

    async def test_empty_owner(self):
        with self.assertRaises(ValueError):
            Transport('fake', 0, directory())

    async def test_handlers_do_not_wire_hermes(self):
        app = NS(add_handler=Mock())
        self.tg._wire_plugin_handlers(app)
        self.tg._start_post_connect_housekeeping()
        self.tg._register_handlers(app)
        self.assertEqual(app.add_handler.call_count, 2)
        for call in app.add_handler.call_args_list:
            self.assertEqual(call.args[0].callback, self.tg.route)
            self.assertFalse(call.args[0].block)
        self.tg._register_handlers(NS(add_handler=Mock()))  # Same virtual seam on rebuild.

    async def test_readiness_requires_polling(self):
        self.assertEqual(self.tg.health(), 'degraded')
        self.tg._polling_last_progress_monotonic = time.monotonic()
        self.assertEqual(self.tg.health(), 'ready')

    async def test_unconfirmed_delivery_is_failure(self):
        self.tg.send = AsyncMock(return_value=NS(success=False))
        with self.assertRaises(RuntimeError):
            await self.tg.say('test')

    async def test_document_ingress_and_mismatch(self):
        import httpx
        self.tg.say = AsyncMock(return_value=[1])
        self.tg.controller = NS(busy=False, commands=asyncio.Lock(),
            journal=NS(receive=Mock(return_value=True)), start=AsyncMock())
        attachment = NS(file_size=3, file_name='../../report.txt',
            get_file=AsyncMock(return_value=NS(file_path='https://example.invalid/file')))
        message = NS(text=None, caption='請看', media_group_id=None, document=attachment, photo=[])
        update = NS(effective_user=NS(id=1001,is_bot=False), effective_chat=NS(id=1001,type='private'),
            callback_query=None, message=message, edited_message=None, update_id=1)
        original = httpx.AsyncClient
        fake = httpx.MockTransport(lambda request: httpx.Response(200, content=b'abc'))
        with patch('httpx.AsyncClient', side_effect=lambda **kw: original(transport=fake, **kw)):
            await self.tg.ingress(update, None)
        self.tg.controller.start.assert_awaited_once()
        self.assertEqual(len(list(self.tg.inbox.iterdir())), 1)
        self.assertIn('請看', self.tg.controller.start.call_args.args[0][0]['text'])
        attachment.file_size = 2
        update.update_id = 2
        self.tg.controller.start.reset_mock()
        with patch('httpx.AsyncClient', side_effect=lambda **kw: original(transport=fake, **kw)):
            await self.tg.ingress(update, None)
        self.tg.controller.start.assert_not_awaited()
        self.assertEqual(len(list(self.tg.inbox.iterdir())), 1)

    async def test_oversize_before_download(self):
        self.tg.say = AsyncMock(return_value=[1])
        self.tg.controller = NS(busy=False, commands=asyncio.Lock(),
            journal=NS(receive=Mock(return_value=True)), start=AsyncMock())
        attachment = NS(file_size=20*1024*1024, get_file=AsyncMock())
        message = NS(text=None, caption=None, media_group_id=None, document=attachment, photo=[])
        update = NS(effective_user=NS(id=1001,is_bot=False), effective_chat=NS(id=1001,type='private'),
            callback_query=None, message=message, edited_message=None, update_id=1)
        await self.tg.ingress(update, None)
        attachment.get_file.assert_not_awaited()
        self.tg.controller.start.assert_not_awaited()


class ControllerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.root = directory()
        self.rpc = FakeRPC(self.root)
        self.tg = NS(say=AsyncMock(return_value=[123]), buttons=AsyncMock(), health=lambda:'ready')
        self.journal = Journal(self.root / 'db.sqlite')
        self.c = Controller(self.rpc, self.tg, self.journal, self.root)
        self.c.model = 'test-model'

    async def asyncTearDown(self):
        self.c.closed = True
        self.journal.close()

    async def test_start_resume_native_no_history(self):
        await self.c.start([{'type':'text', 'text':'hello'}])
        self.assertEqual(self.c.thread, 'thread1')
        self.assertEqual(self.c.turn, 'turn1')
        params = next(p for m,p in self.rpc.calls if m == 'turn/start')
        self.assertEqual(params['input'], [{'type':'text','text':'hello'}])
        self.assertEqual(params['collaborationMode']['mode'], 'default')
        self.assertNotIn('history', params)
        self.c.busy = False
        await self.c.resume('thread1')
        self.assertIn('thread/resume', [m for m,p in self.rpc.calls])

    async def test_cross_workspace_resume_denied(self):
        self.rpc.workspace = directory()
        with self.assertRaises(ValueError):
            await self.c.resume('foreign')
        self.assertNotIn('thread/resume', [m for m,p in self.rpc.calls])

    async def test_busy_reject_and_control_not_queued(self):
        self.c.busy, self.c.thread, self.c.turn = True, 'thread1', 'turn1'
        await self.c.commands.acquire()
        started = time.monotonic()
        await self.c.handle('/steer additional')
        await self.c.handle('/stop')
        await self.c.handle('/steer after-stop')
        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual([m for m,p in self.rpc.calls], ['turn/steer', 'turn/interrupt'])
        self.c.commands.release()
        await self.c.start([{'type':'text','text':'not queued'}])
        self.assertNotIn('turn/start', [m for m,p in self.rpc.calls])

    def approval(self, method='item/commandExecution/requestApproval'):
        self.c.busy, self.c.thread, self.c.turn = True, 'thread1', 'turn1'
        return {'id': 6, 'method': method, 'params': {'threadId':'thread1','turnId':'turn1',
                'availableDecisions':['accept','decline'], 'command':'test'}}

    async def test_approval_single_use_and_expiry(self):
        await self.c.server_request(self.approval())
        key = next(iter(self.c.pending))
        await self.c.show_request(key)
        q = NS(data=f'cg:{key}:1', answer=AsyncMock())
        await self.c.callback(q)
        await self.c.callback(q)
        self.assertEqual(self.rpc.responses, [(6, {'decision':'decline'})])
        await self.c.server_request(self.approval())
        key = next(iter(self.c.pending))
        self.c.pending[key]['expires'] = 0
        with self.assertRaises(ValueError):
            self.c.valid_pending(key)

    async def test_empty_native_choices_never_expand_authority(self):
        request = self.approval()
        request['params']['availableDecisions'] = []
        await self.c.server_request(request)
        key = next(iter(self.c.pending))
        with self.assertRaises(ValueError):
            await self.c.show_request(key)
        self.tg.buttons.assert_not_awaited()

    async def test_unsupported_requests_fail_closed(self):
        for method in ['item/tool/call','account/chatgptAuthTokens/refresh','attestation/generate',
                       'execCommandApproval','applyPatchApproval','currentTime/read']:
            await self.c.server_request(self.approval(method))
        self.assertEqual(len(self.rpc.errors), 6)
        self.assertEqual(self.rpc.responses, [])

    async def test_mcp_consent_routes_once_to_current_owner_and_turn(self):
        request=self.approval('mcpServer/elicitation/request')
        request['params'].update(serverName='canva',mode='form',message='Create the requested design?',requestedSchema={'type':'object','properties':{}})
        await self.c.server_request(request)
        key=next(iter(self.c.pending));await self.c.show_request(key)
        self.assertIn('canva',self.tg.buttons.call_args.args[0])
        query=NS(data=f'cg:{key}:0',answer=AsyncMock())
        await self.c.callback(query);await self.c.callback(query)
        self.assertEqual(self.rpc.responses,[(request['id'],{'action':'accept','content':{}})])
        request['params']['turnId']='another-turn'
        await self.c.server_request(request);self.assertTrue(self.rpc.errors)

    async def test_mcp_password_form_declined_without_chat_collection(self):
        request=self.approval('mcpServer/elicitation/request')
        request['params'].update(serverName='canva',mode='form',requestedSchema={'type':'object','properties':{'password':{'type':'string'}}})
        await self.c.server_request(request)
        self.assertEqual(self.rpc.responses,[(request['id'],{'action':'decline'})])
        self.tg.buttons.assert_not_awaited()

    async def test_mcp_nullable_turn_binds_to_active_turn(self):
        request=self.approval('mcpServer/elicitation/request')
        request['params'].update(turnId=None,serverName='canva',mode='form',requestedSchema={'type':'object'})
        await self.c.server_request(request)
        key=next(iter(self.c.pending))
        self.assertEqual(self.c.valid_pending(key)['request']['params']['turnId'],self.c.turn)
        self.c.turn='next-turn'
        with self.assertRaises(ValueError):self.c.valid_pending(key)

    async def test_questions_reply_original_request(self):
        request = self.approval('item/tool/requestUserInput')
        request['params']['questions'] = [{'id':'q1','question':'Choose','options':[{'label':'A'}], 'isOther':False}]
        await self.c.server_request(request)
        key = next(iter(self.c.pending))
        with self.assertRaises(ValueError):
            await self.c.answer(f'{key} 1 B')
        await self.c.answer(f'{key} 1 A')
        self.assertEqual(self.rpc.responses[0], (6, {'answers':{'q1':{'answers':['A']}}}))
        self.assertEqual(self.rpc.calls, [])

    async def test_turn_event_precedes_approval(self):
        self.c.thread, self.c.turn, self.c.busy = 'thread1', None, True
        self.rpc.notifications.put({'method':'turn/started','params':{'threadId':'thread1','turn':{'id':'turn1'}}})
        self.rpc.requests.put({'id':1,'method':'item/fileChange/requestApproval',
                               'params':{'threadId':'thread1','turnId':'turn1'}})
        task = asyncio.create_task(self.c.events())
        await asyncio.sleep(.07)
        self.c.closed = True
        await task
        self.assertEqual(len(self.c.pending), 1)
        self.assertEqual(self.rpc.errors, [])

    async def test_completed_delivery_and_failure_separate(self):
        await self.c.deliver_result('thread1','turn1',status='completed')
        self.assertTrue(self.journal.delivered('thread1:turn1'))
        self.tg.say.side_effect = TimeoutError()
        with self.assertRaises(TimeoutError):
            await self.c.deliver_result('thread1','turn2',status='completed')
        self.assertFalse(self.journal.delivered('thread1:turn2'))

    async def test_crash_unknown_never_replays(self):
        self.c.unknown = True
        await self.c.start([{'type':'text','text':'side effect'}])
        self.assertEqual(self.rpc.calls, [])

    async def test_partial_result_never_suppresses_final_delivery(self):
        self.c.thread, self.c.busy = 'thread1', True
        await self.c.handle('/result')
        self.assertEqual(self.rpc.calls, [])
        await self.c.deliver_result('thread1','turn1',status='inProgress')
        self.assertFalse(self.journal.delivered('thread1:turn1'))

    async def test_permissions_only_requested_grant(self):
        request = self.approval('item/permissions/requestApproval')
        request['params']['permissions'] = {'network':{'enabled':True}}
        await self.c.server_request(request)
        key = next(iter(self.c.pending))
        await self.c.show_request(key)
        self.assertEqual(self.c.pending[key]['choices'][0][1],
            {'permissions':{'network':{'enabled':True}},'scope':'turn'})

    async def test_plan_is_native(self):
        await self.c.handle('/plan')
        await self.c.start([{'type':'text','text':'plan'}])
        p = next(p for m,p in self.rpc.calls if m == 'turn/start')
        self.assertEqual(p['collaborationMode']['mode'], 'plan')

    async def test_outbound_requests_match_installed_schema(self):
        import jsonschema
        await self.c.start([{'type':'text','text':'schema check'}])
        await self.c.handle('/steer refinement')
        await self.c.handle('/stop')
        schemas = ROOT / 'evidence/schema-experimental/v2'
        if not schemas.is_dir():self.skipTest('Generate schema with the pinned Codex CLI to run protocol compatibility checks')
        names = {'thread/start':'ThreadStartParams', 'turn/start':'TurnStartParams',
                 'turn/steer':'TurnSteerParams', 'turn/interrupt':'TurnInterruptParams'}
        for method, params in self.rpc.calls:
            if method in names:
                schema = json.loads((schemas / (names[method] + '.json')).read_text(encoding='utf-8'))
                jsonschema.validate(params, schema)

    async def test_quiet_normal_completion_and_chinese_approval(self):
        await self.c.start([{'type':'text','text':'hello'}])
        self.tg.say.assert_not_awaited()
        await self.c.deliver_result('thread1','turn1',status='completed')
        self.assertEqual([c.args[0] for c in self.tg.say.call_args_list], ['result'])
        await self.c.server_request(self.approval())
        key=next(iter(self.c.pending))
        await self.c.show_request(key)
        text=self.tg.buttons.call_args.args[0]
        self.assertIn('確認',text)
        self.assertNotIn('threadId',text)
        self.assertNotIn('{',text)

    async def test_failed_capacity_turn_does_not_replay_starting_promise(self):
        async def reply(method,params):
            if method=='thread/turns/list':
                return {'data':[{'id':'turn1','status':'failed','error':{'message':'Selected model is at capacity. Please try a different model.','codexErrorInfo':'serverOverloaded'}}]}
            return {'data':[{'item':{'type':'agentMessage','phase':'commentary','text':'我現在開始用 Canva 製作，完成後先給你預覽。'}}],'nextCursor':None}
        self.rpc.call=AsyncMock(side_effect=reply)
        await self.c.deliver_result('thread1',None)
        texts='\n'.join(c.args[0] for c in self.tg.say.call_args_list)
        self.assertNotIn('我現在開始',texts)
        self.assertIn('模型服務目前滿載',texts)
        self.assertIn('不代表外部操作已取消',texts)

    async def test_failed_event_preserves_error_for_sender(self):
        error={'codexErrorInfo':'serverOverloaded','message':'private diagnostic'}
        self.rpc.notifications.put({'method':'turn/completed','params':{'threadId':self.c.thread,'turn':{'id':'turn1','status':'failed','error':error}}})
        task=asyncio.create_task(self.c.events())
        await asyncio.sleep(.07)
        self.c.closed=True
        await task
        job=self.c.outbox.get_nowait()
        self.assertEqual(job[3: ],('failed',error))
        from ui import failure_text
        self.assertNotIn('private diagnostic',failure_text(error))

    async def test_permission_downgrade_is_detected(self):
        with self.assertRaises(RuntimeError):
            self.c.check_permissions({'sandbox':{'type':'readOnly'}})

    async def test_selected_skill_is_native_input_once(self):
        self.c.skill_choices['test']={'input':{'type':'skill','name':'demo','path':'D:/demo/SKILL.md'},
                                      'expires':time.monotonic()+60}
        q=NS(data='sk:test',answer=AsyncMock())
        await self.c.callback(q)
        await self.c.start([{'type':'text','text':'do work'}])
        params=next(p for m,p in self.rpc.calls if m=='turn/start')
        self.assertEqual(params['input'][1]['type'],'skill')
        self.assertIsNone(self.c.selected_skill)

    async def test_details_does_not_grant_approval(self):
        await self.c.server_request(self.approval())
        key=next(iter(self.c.pending))
        await self.c.callback(NS(data=f'cg:{key}:details',answer=AsyncMock()))
        self.assertIn(key,self.c.pending)
        self.assertEqual(self.rpc.responses,[])


if __name__ == '__main__':
    unittest.main()
