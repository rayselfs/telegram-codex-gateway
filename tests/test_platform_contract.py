import importlib.util
import tempfile
from pathlib import Path
import sys
import unittest

class PlatformContractTests(unittest.TestCase):
    def backend(self):
        self.assertIsNotNone(importlib.util.find_spec('gateway.platforms'), 'platform boundary missing')
        from gateway.platforms import current
        return current()

    def test_unsupported_platform_fails_closed(self):
        self.backend()
        from gateway.platforms import current
        with self.assertRaises(RuntimeError): current('linux')

    def test_native_lock_exclusion_and_reopen(self):
        api = self.backend()
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'instance.lock'
            first = api.SingleInstance(p)
            try:
                with self.assertRaises(RuntimeError): api.SingleInstance(p)
            finally: first.close()
            api.SingleInstance(p).close()

    @unittest.skipUnless(sys.platform == 'win32', 'Windows native DPAPI only')
    def test_native_secret_roundtrip_and_owner_entropy(self):
        api = self.backend()
        encrypted = api.crypt(b'offline-secret-test', b'owner-101', True)
        self.assertNotIn(b'offline-secret-test', encrypted)
        self.assertEqual(api.crypt(encrypted, b'owner-101', False), b'offline-secret-test')
        with self.assertRaises(RuntimeError): api.crypt(encrypted, b'owner-102', False)
        with tempfile.TemporaryDirectory() as tmp:
            token = Path(tmp) / 'test.dpapi'
            token.write_bytes(api.crypt(b'offline-secret-test', b'', True))
            self.assertEqual(api.decrypt_token(token), 'offline-secret-test')

    def test_native_open_handle_checks(self):
        api = self.backend()
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / '中文 file.txt'; p.write_text('test')
            with p.open('rb') as f:
                self.assertEqual(api.opened_path(f).resolve(), p.resolve())

    def test_process_options_are_platform_specific(self):
        api = self.backend()
        kwargs = api.process_options()
        if sys.platform == 'win32':
            self.assertIn('creationflags', kwargs)
            self.assertNotIn('start_new_session', kwargs)
        else:
            self.assertEqual(kwargs, {'start_new_session': True})

    def test_same_bot_cannot_poll_from_two_data_roots(self):
        api=self.backend()
        with tempfile.TemporaryDirectory() as folder:
            first=api.BotInstance('101:synthetic-secret',lock_root=Path(folder))
            try:
                with self.assertRaises(RuntimeError):api.BotInstance('101:rotated-secret',lock_root=Path(folder))
                other=api.BotInstance('102:synthetic-secret',lock_root=Path(folder));other.close()
            finally:first.close()
            api.BotInstance('101:synthetic-secret',lock_root=Path(folder)).close()

    @unittest.skipUnless(sys.platform == 'darwin', 'Requires a native macOS test host')
    def test_native_keychain_roundtrip_and_owner_binding(self):
        api=self.backend();reference=api.crypt(b'offline-secret-test',b'owner-101',True)
        try:
            self.assertNotIn(b'offline-secret-test',reference)
            self.assertEqual(api.crypt(reference,b'owner-101',False),b'offline-secret-test')
            with self.assertRaises(RuntimeError):api.crypt(reference,b'owner-102',False)
        finally:api.delete_secret(reference,b'owner-101')

if __name__ == '__main__': unittest.main()
