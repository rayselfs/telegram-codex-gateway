import importlib.util
import unittest
from pathlib import Path

from gateway.core import background


class PackageEntrypointTests(unittest.TestCase):
    def test_package_entry_exists_without_root_cli(self):
        self.assertIsNotNone(importlib.util.find_spec('gateway.__main__'))
        self.assertFalse((Path(__file__).resolve().parents[1] / 'main.py').exists())

    def test_background_uses_package_and_code_working_directory(self):
        config = {'code_root': 'C:/Gateway Code', 'data_root': 'C:/Gateway Data',
                  'python': 'C:/Runtime/python.exe', 'deployment_id': 'a' * 32}
        guard, worker = background.definitions(config, [{'owner_id': 101}])
        self.assertEqual(guard['arguments'][:4], ['-B', '-m', 'gateway', 'guard'])
        self.assertEqual(worker['arguments'][:4], ['-B', '-m', 'gateway', 'worker'])
        self.assertEqual(guard['cwd'], str(Path(config['code_root'])))
        self.assertEqual(worker['cwd'], guard['cwd'])
