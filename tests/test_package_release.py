import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

class PackageReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source, self.target, self.evidence = [self.root / n for n in ('source', 'target', 'evidence')]
        self.source.mkdir(); self.target.mkdir()
        (self.source / 'gateway/core').mkdir(parents=True)
        (self.source / 'gateway/core/a.py').write_text('VALUE = 2\n')
        (self.source / 'main.py').write_text('VALUE = 2\n')
        (self.target / 'main.py').write_text('VALUE = 1\n')

    def api(self):
        import importlib.util
        self.assertIsNotNone(importlib.util.find_spec('gateway'), 'package structure missing')
        from gateway.core import releases
        return releases

    def test_explicit_nested_files_and_compatibility_import(self):
        api = self.api()
        rows = api.checked_files(self.source, ['main.py', 'gateway/core/a.py'])
        self.assertEqual(len(rows), 2)
        from health_file import write_json as old
        from gateway.core.health_file import write_json as new
        self.assertIs(old, new)

    def test_reject_path_aliases_and_escape(self):
        api = self.api()
        for name in ('../main.py', '/main.py', 'C:/main.py', 'gateway//core/a.py',
                     'gateway/./core/a.py', 'gateway\\core\\a.py', 'main.py:stream', 'main.py.', 'CON.py'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                api.checked_files(self.source, [name])
        with self.assertRaises(ValueError):
            api.checked_files(self.source, ['main.py', 'MAIN.py'])

    def test_apply_and_rollback_remove_only_new_code(self):
        api = self.api()
        api.apply_package(self.source, self.target, ['main.py', 'gateway/core/a.py'], self.evidence)
        (self.target / 'new-receipt.json').write_text('receipt after release')
        api.rollback_package(self.target, self.evidence)
        self.assertEqual((self.target / 'main.py').read_text(), 'VALUE = 1\n')
        self.assertFalse((self.target / 'gateway/core/a.py').exists())
        self.assertEqual((self.target / 'new-receipt.json').read_text(), 'receipt after release')
        api.rollback_package(self.target, self.evidence)

    def test_interrupted_release_restores_old_and_removes_new(self):
        api = self.api()
        original = api.os.replace
        def fail(src, dst):
            if Path(dst) == self.target / 'main.py':
                raise OSError('synthetic interruption')
            return original(src, dst)
        with patch.object(api.os, 'replace', side_effect=fail), self.assertRaises(OSError):
            api.apply_package(self.source, self.target, ['gateway/core/a.py', 'main.py'], self.evidence)
        self.assertEqual((self.target / 'main.py').read_text(), 'VALUE = 1\n')
        self.assertFalse((self.target / 'gateway/core/a.py').exists())

    def test_rollback_refuses_overwriting_changed_files(self):
        api = self.api()
        api.apply_package(self.source, self.target, ['main.py', 'gateway/core/a.py'], self.evidence)
        (self.target / 'gateway/core/a.py').write_text('newer edit')
        with self.assertRaises(RuntimeError):
            api.rollback_package(self.target, self.evidence)
        self.assertEqual((self.target / 'main.py').read_text(), 'VALUE = 2\n')
        self.assertEqual((self.target / 'gateway/core/a.py').read_text(), 'newer edit')

    def test_invalid_python_never_changes_target(self):
        api = self.api()
        (self.source / 'gateway/core/a.py').write_text('def invalid')
        with self.assertRaises(SyntaxError):
            api.apply_package(self.source, self.target, ['main.py', 'gateway/core/a.py'], self.evidence)
        self.assertEqual((self.target / 'main.py').read_text(), 'VALUE = 1\n')

    def test_overlapping_roots_rejected(self):
        api = self.api()
        with self.assertRaises(ValueError):
            api.apply_package(self.source, self.source, ['main.py'], self.evidence)

    def test_reparse_ancestor_rejected_before_read(self):
        api = self.api()
        original = Path.is_junction
        with patch.object(Path, 'is_junction', lambda p: p.name == 'gateway' or original(p)):
            with self.assertRaises(ValueError):
                api.checked_files(self.source, ['gateway/core/a.py'])

if __name__ == '__main__': unittest.main()
