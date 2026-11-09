import tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
class PersistentStartTests(unittest.TestCase):
 def test_start_delegates_to_scheduler_and_never_clears_holds(self):
  from gateway.core import bootstrap
  from gateway.core import background
  with tempfile.TemporaryDirectory(dir=bootstrap.ROOT/'temp') as temp:
   root=Path(temp);(root/'profile').mkdir()
   config=dict(data_root=str(root),code_root=str(root/'code'),python=str(root/'python.exe'),deployment_id='b'*32)
   backend=Mock()
   with patch('gateway.platforms.current',return_value=backend):
    background.start(config)
    self.assertEqual(backend.start_background.call_count,1)
    self.assertEqual(backend.start_background.call_args.args[0]['arguments'][:4], ['-B','-m','gateway','guard'])
    for name in ['maintenance.request','stop.request']:
     hold=root/'profile'/name;hold.write_text('retain')
     with self.assertRaises(RuntimeError):background.start(config)
     self.assertEqual(hold.read_text(),'retain');hold.unlink()
    self.assertEqual(backend.start_background.call_count,1)
