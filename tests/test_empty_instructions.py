import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from gateway.core import tool_runtime

class EmptyInstructionsTests(unittest.TestCase):
    def test_new_install_without_shared_guidance_keeps_personal_guidance(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);personal=root/'personal.md';personal.write_text('personal-only',encoding='utf-8')
            with patch.object(tool_runtime,'SHARED_AGENTS',root/'missing.md'):
                self.assertIn('personal-only',tool_runtime.load_instructions(personal))
                self.assertEqual(tool_runtime.load_instructions(),'')

    def test_existing_shared_instructions_remain_present(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'AGENTS.md';path.write_text('host guidance',encoding='utf-8')
            with patch.object(tool_runtime,'SHARED_AGENTS',path):
                self.assertIn('host guidance',tool_runtime.load_instructions())
