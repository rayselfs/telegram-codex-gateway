import importlib.util
import tempfile
from pathlib import Path
import unittest
from gateway.core.members import Members

class CapabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_bound_actor_scope_and_request_never_come_from_model(self):
        self.assertIsNotNone(importlib.util.find_spec('gateway.core.capabilities'),'capability dispatch missing')
        from gateway.core.capabilities import Capabilities,CapabilityTools
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);root=Path(tmp.name)
        members=Members(root/'members.sqlite',root);self.addCleanup(members.close)
        members.import_existing([dict(owner_id=n,name=str(n),home=str(root/f'{n}/profile'),workspace=str(root/f'{n}/workspace')) for n in (101,102)],101)
        calls=[]
        registry=Capabilities(members)
        registry.register('shared_records',lambda context,args:calls.append((dict(context),args)) or {'ok':True}, {'query':'active','publish':'admin'})
        tools=CapabilityTools(registry,102)
        with self.assertRaises(PermissionError):await tools.apply({'capability':'shared_records','action':'publish','arguments':{}},'request1')
        self.assertEqual(calls,[])
        with self.assertRaises(ValueError):await tools.apply({'capability':'shared_records','action':'query','arguments':{},'owner_id':101},'request2')
        await tools.apply({'capability':'shared_records','action':'query','arguments':{}},'request3')
        self.assertEqual(calls[0][0]['owner_id'],102)
        self.assertEqual(calls[0][0]['request_id'],'request3')
        members.suspend(101,102,'test')
        with self.assertRaises(PermissionError):await tools.apply({'capability':'shared_records','action':'query','arguments':{}},'request4')
        self.assertEqual(len(calls),1)

if __name__=='__main__':unittest.main()
