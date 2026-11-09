import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

@unittest.skipUnless(os.name == 'nt', 'Windows background launch')
class WindowlessBackgroundTests(unittest.TestCase):
    def test_tasks_use_sibling_windowless_interpreter_and_preserve_binding(self):
        from gateway.core import bootstrap
        from gateway.platforms import windows
        with tempfile.TemporaryDirectory(dir=bootstrap.ROOT/'temp') as tmp:
            python=Path(tmp)/'python.exe'; python.touch()
            windowless=python.with_name('pythonw.exe'); windowless.touch()
            job=dict(name='test-owner',python=str(python),cwd=tmp,arguments=['-B','space script.py','--owner','101'],interval=60)
            with patch('subprocess.run') as run:
                windows.install_background([job])
            payload=json.loads(run.call_args.kwargs['input'])[0]
            self.assertEqual(payload['python'],str(windowless))
            self.assertEqual(payload['arguments'],job['arguments'])
            self.assertEqual(payload['cwd'],job['cwd'])
            self.assertEqual(job['python'],str(python))
            windowless.unlink()
            with patch('subprocess.run') as run:
                with self.assertRaises(FileNotFoundError):windows.install_background([job])
                run.assert_not_called()
