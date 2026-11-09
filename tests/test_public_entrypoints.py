import asyncio
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from gateway.core import bootstrap, init, main
from gateway.scheduling import schedule_worker
from gateway.telegram import ui


class PublicEntrypointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=bootstrap.ROOT / 'temp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    @unittest.skipUnless(sys.platform == 'win32', 'Windows initialization contract')
    def test_init_accepts_documented_python_version(self):
        code = self.root / 'code'
        code.mkdir()
        binary = self.root / 'fixture.exe'
        binary.write_bytes(b'synthetic executable')
        (code / 'versions.json').write_text(json.dumps({
            'codex_sha256': hashlib.sha256(binary.read_bytes()).hexdigest()
        }), encoding='utf-8')
        data = self.root / 'fresh data'
        args = ['gateway init', '--root', str(data), '--codex', str(binary)]
        with patch.object(init, '__file__', str(code / 'gateway/core/init.py')), \
                patch.object(init.getpass, 'getpass', return_value='123:synthetic'), \
                patch.object(sys, 'argv', args), patch.object(sys, 'version_info', (3, 15, 2)), \
                redirect_stdout(io.StringIO()):
            init.main()
        config = json.loads((data / 'deployment.json').read_text(encoding='utf-8'))
        self.assertEqual(Path(config['code_root']), code)
        self.assertEqual(Path(config['data_root']), data)
        self.assertTrue((data / 'profile/members.sqlite').is_file())
        self.assertNotEqual((data / 'profile/telegram-token.dpapi').read_bytes(), b'123:synthetic')

    def test_hold_blocks_direct_service_and_worker_before_runtime_checks(self):
        profile = self.root / 'profile'
        profile.mkdir()
        (profile / 'maintenance.request').touch()
        with patch.object(main, 'ROOT', self.root), patch.object(schedule_worker, 'ROOT', self.root), \
                patch.object(main, 'verify_install') as verify, \
                patch.object(main, 'isolate', return_value=profile) as isolate, \
                patch.object(sys, 'argv', ['gateway', 'serve']):
            self.assertEqual(main.main(), 0)
            self.assertEqual(asyncio.run(main.serve(profile)), 'maintenance')
            self.assertEqual(asyncio.run(schedule_worker.tick({'owner_id': 101, 'home': self.root / 'absent'})), 'maintenance')
            with patch.object(sys, 'argv', ['gateway worker', '--owner', '101']):
                self.assertIsNone(schedule_worker.main())
            verify.assert_not_called()
            isolate.assert_not_called()

    def test_private_features_are_not_advertised(self):
        from gateway.core.controller import Controller
        menu = dict(ui.COMMANDS)
        self.assertFalse({'line', 'browser'} & menu.keys())
        fake = SimpleNamespace(tg=SimpleNamespace(owner=101, say=AsyncMock()))
        asyncio.run(Controller._handle(fake, '/secrets add'))
        self.assertNotIn('.ps1', fake.tg.say.await_args.args[0])

    def test_held_transport_blocks_http_and_records_queued_owner_updates(self):
        from telegram.request import HTTPXRequest
        from gateway.core import maintenance
        from gateway.core.state import Journal
        from gateway.telegram.transport import MaintenanceRequest, Transport

        profile = self.root / 'profile'
        profile.mkdir()
        hold = profile / 'maintenance.request'
        request = object.__new__(MaintenanceRequest)
        transport = Transport('123:synthetic', 101, self.root / 'inbox')
        journal = Journal(profile / 'transport.sqlite')
        self.addCleanup(journal.close)
        transport.controller = SimpleNamespace(journal=journal)
        transport.registration = SimpleNamespace(handle=AsyncMock())
        update = SimpleNamespace(update_id=1, effective_user=SimpleNamespace(id=101, is_bot=False),
                                 effective_chat=SimpleNamespace(id=101, type='private'))

        with patch.object(maintenance, 'ROOT', self.root), \
                patch.object(HTTPXRequest, 'do_request', AsyncMock(return_value=(200, b'{}'))) as http:
            self.assertEqual(asyncio.run(request.do_request()), (200, b'{}'))
            hold.touch()
            with self.assertRaises(PermissionError):
                asyncio.run(request.do_request())
            http.assert_awaited_once()
            asyncio.run(transport.route(update, None))
        transport.registration.handle.assert_not_awaited()
        self.assertEqual(journal.db.execute('SELECT state FROM updates').fetchone()[0],
                         'held-maintenance')
