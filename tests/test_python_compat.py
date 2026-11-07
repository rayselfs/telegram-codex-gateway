import importlib,os,tempfile,unittest
from pathlib import Path
import gateway
@unittest.skipUnless(os.name == 'nt', 'Windows junction semantics')
class PythonPathCompatibilityTests(unittest.TestCase):
 def test_missing_method_is_backported_with_real_junction_rejection(self):
  import _winapi,bootstrap
  from gateway.core.deployment import validate_root
  original=Path.is_junction
  try:
   del Path.is_junction;importlib.reload(gateway)
   with tempfile.TemporaryDirectory(dir=bootstrap.ROOT/'temp') as tmp:
    base=Path(tmp);target=base/'target';target.mkdir();link=base/'link';_winapi.CreateJunction(str(target),str(link))
    self.assertTrue(link.is_junction());self.assertFalse(target.is_junction());self.assertFalse((base/'missing').is_junction())
    with self.assertRaises(ValueError):validate_root(str(link),str(base/'code'))
    link.rmdir()
  finally:Path.is_junction=original
