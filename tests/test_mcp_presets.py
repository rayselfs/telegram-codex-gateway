import importlib.util
import tempfile
from pathlib import Path
import unittest
from gateway.core.members import Members

class PresetTests(unittest.TestCase):
    def test_defaults_owner_binding_and_unchanged_legacy_configuration(self):
        self.assertIsNotNone(importlib.util.find_spec('gateway.mcp.presets'),'preset policy missing')
        from gateway.mcp.presets import Presets
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);root=Path(tmp.name)
        members=Members(root/'members.sqlite',root);self.addCleanup(members.close)
        members.import_existing([dict(owner_id=n,name=str(n),home=str(root/f'{n}/profile'),workspace=str(root/f'{n}/workspace')) for n in (101,102,103)],101)
        policy=Presets(members)
        server={'url':'https://mcp.notion.com/mcp'}
        self.assertFalse(policy.allowed(102,'notion',server))
        with self.assertRaises(PermissionError):policy.set_allowed(102,'notion',True)
        policy.set_allowed(101,'notion',True)
        self.assertTrue(policy.allowed(102,'notion',server))
        self.assertFalse(policy.allowed(102,'notion',{'command':'arbitrary'}))
        self.assertEqual(policy.connection(103,'notion')['home'],str(root/'103/profile'))
        legacy={'command':'unchanged-entry','args':['--owner','102']}
        policy.import_existing(102,{'custom':legacy})
        self.assertTrue(policy.allowed(102,'custom',legacy))
        self.assertFalse(policy.allowed(103,'custom',legacy))
        self.assertFalse(policy.allowed(102,'custom',{**legacy,'args':['--owner','103']}))
        home=root/'102/profile';(home/'codex-home').mkdir(parents=True)
        config=home/'codex-home/config.toml'
        config.write_text('[mcp_servers.custom]\ncommand="unchanged-entry"\nargs=["--owner","102"]\n',encoding='utf-8')
        self.assertEqual(policy.authorize_runtime(102)['owner_id'],102)
        from oauth_login import ServiceLogin
        from types import SimpleNamespace
        login=ServiceLogin(SimpleNamespace(owner=102),home,policy=policy)
        self.assertEqual(set(login.servers()),{'custom'})
        config.write_text('[mcp_servers.custom]\ncommand="new-entry"\n',encoding='utf-8')
        with self.assertRaises(PermissionError):policy.authorize_runtime(102)
        self.assertEqual(login.servers(),{})
        third=root/'103/profile/codex-home';third.mkdir(parents=True)
        (third/'config.toml').write_text('mcp_oauth_credentials_store="file"\n[features]\napps=false\n',encoding='utf-8')
        self.assertEqual(policy.connect(103,'notion')['status'],'configured')
        before=(third/'config.toml').read_bytes()
        self.assertEqual(policy.connect(103,'notion')['status'],'existing_configuration_retained')
        self.assertEqual((third/'config.toml').read_bytes(),before)
        self.assertFalse((third/'auth.json').exists())
        with self.assertRaises(PermissionError):policy.connect(103,'canva')
        policy.set_allowed(101,'notion',False)
        self.assertFalse(policy.allowed(102,'notion',server))
        members.suspend(101,102,'test')
        with self.assertRaises(PermissionError):policy.connection(102,'notion')

if __name__=='__main__':unittest.main()
