import importlib.util
import tempfile
from pathlib import Path
import unittest
from gateway.core.members import Members

class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def service(self):
        self.assertIsNotNone(importlib.util.find_spec('gateway.core.init'), 'safe bootstrap missing')
        from gateway.core.init import Bootstrap
        members=Members(self.root/'members.sqlite',self.root); self.addCleanup(members.close)
        return Bootstrap(members),members

    def test_claim_requires_local_confirmation_then_non_inference_health(self):
        service,members=self.service()
        token=service.issue(now=0)
        claim=service.claim_bootstrap(token,101,private=True,now=1)
        self.assertEqual(members.active_profiles(),[])
        with self.assertRaises(PermissionError): service.confirm_bootstrap(claim,102)
        service.confirm_bootstrap(claim,101,now=2)
        self.assertEqual(members.active_profiles(),[])
        with self.assertRaises(RuntimeError): service.activate(claim,lambda _:False)
        service.activate(claim,lambda _:True)
        self.assertEqual(members.require_admin(101)['state'],'active')
        with self.assertRaises(PermissionError): service.issue()

    def test_wrong_expired_replayed_and_group_claims_rejected(self):
        service,members=self.service(); token=service.issue(now=0)
        for value,private,now in ((token,False,1),('wrong',True,1),(token,True,901)):
            with self.assertRaises(PermissionError): service.claim_bootstrap(value,101,private=private,now=now)
        claim=service.claim_bootstrap(token,101,private=True,now=1)
        with self.assertRaises(PermissionError): service.claim_bootstrap(token,102,private=True,now=2)
        self.assertEqual(service.claim_bootstrap(token,101,private=True,now=2),claim)
        service.confirm_bootstrap(claim,101,now=2)
        service.activate(claim,lambda _:True)
        with self.assertRaises(PermissionError): service.claim_bootstrap(token,101,private=True,now=3)

    def test_pairing_code_is_hashed_and_reissue_revokes_old_claim(self):
        service,members=self.service(); token=service.issue(now=0)
        claim=service.claim_bootstrap(token,101,private=True,now=1)
        service.issue(now=2)
        with self.assertRaises(PermissionError): service.confirm_bootstrap(claim,101)
        self.assertNotIn(token,(self.root/'members.sqlite').read_bytes().decode('latin1'))

    def test_expired_claim_cannot_be_confirmed(self):
        service,members=self.service(); token=service.issue(now=0)
        claim=service.claim_bootstrap(token,101,private=True,now=1)
        with self.assertRaises(PermissionError): service.confirm_bootstrap(claim,101,now=901)
        self.assertEqual(members.active_profiles(),[])
        self.assertIsNone(members.db.execute('SELECT 1 FROM members').fetchone())

    def test_confirm_retries_interrupted_provisioning(self):
        from unittest.mock import patch
        service,members=self.service(); token=service.issue(now=0)
        claim=service.claim_bootstrap(token,101,private=True,now=1)
        with patch.object(Path,'mkdir',side_effect=OSError('interrupted')):
            with self.assertRaises(OSError): service.confirm_bootstrap(claim,101,now=2)
        service.confirm_bootstrap(claim,101,now=3)
        self.assertTrue(members.require_onboarding(101)['provisioned'])
        self.assertTrue((self.root/'users/101/profile/codex-home/config.toml').is_file())

if __name__ == '__main__': unittest.main()
