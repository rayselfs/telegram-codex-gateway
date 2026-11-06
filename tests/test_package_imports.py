import importlib
import unittest

GROUPS = {
    'telegram': 'transport ui artifacts voice voice_transcribe',
    'core': 'controller state rpc codex_wire profiles personal_memory shared_skills managed_skills tool_runtime autonomy_tools autonomy_policy availability recovery worker_recovery worker_supervisor gateway_guard main release',
    'scheduling': 'schedules schedule_worker schedule_delivery',
    'mcp': 'oauth_login mcp_consent vault',
}

class PackageImportsTests(unittest.TestCase):
    def test_legacy_imports_are_same_modules(self):
        for group, modules in GROUPS.items():
            for name in modules.split():
                with self.subTest(module=name):
                    legacy = importlib.import_module(name)
                    packaged = importlib.import_module(f'gateway.{group}.{name}')
                    self.assertIs(legacy, packaged)

if __name__ == '__main__': unittest.main()
