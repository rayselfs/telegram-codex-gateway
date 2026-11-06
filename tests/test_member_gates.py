import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch
from gateway.core.members import Members
from transport import Transport
from rpc import Client

class MemberGateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Members(self.root/'members.sqlite', self.root); self.addCleanup(self.store.close)
        self.store.import_existing([dict(owner_id=n,name=str(n),home=str(self.root/f'{n}/profile'),
                                        workspace=str(self.root/f'{n}/workspace')) for n in (101,102)],101)
        self.store.claim(self.store.invite(101),103,'member_c')

    async def test_actual_routes_drop_all_nonactive_inputs_before_ingress(self):
        main = Transport('123:test', 101, self.root/'inbox')
        main.members = self.store
        for owner in (101,102,103,999):
            peer = Transport('123:test', owner, self.root/str(owner))
            peer.members = self.store; peer.ingress = AsyncMock()
            main.peers[owner] = peer
        self.store.suspend(101,102,'test')
        for owner in (102,103,999):
            for kind in ('text','voice','document','callback_query'):
                update = NS(effective_user=NS(id=owner,is_bot=False), effective_chat=NS(id=owner,type='private'),
                            message=NS(text='payload',voice=Mock(),document=Mock()),callback_query=None)
                await main.route(update,None)
            main.peers[owner].ingress.assert_not_awaited()
        update = NS(effective_user=NS(id=101,is_bot=False),effective_chat=NS(id=101,type='private'))
        await main.route(update,None)
        main.peers[101].ingress.assert_awaited_once()

    async def test_direct_voice_entry_checks_before_download(self):
        peer = Transport('123:test',103,self.root/'inbox'); peer.members=self.store
        attachment = NS(file_size=5,duration=1,get_file=AsyncMock())
        with self.assertRaises(PermissionError): await peer.receive_voice(attachment)
        attachment.get_file.assert_not_awaited()

    async def test_rpc_rechecks_member_at_inference_dispatch(self):
        rpc = Client.__new__(Client)
        rpc.authorize = lambda: self.store.require_active(102)
        rpc.request = Mock(return_value={})
        self.store.suspend(101,102,'test')
        for method in ('turn/start','turn/steer','thread/compact/start'):
            with self.assertRaises(PermissionError): await rpc.call(method,{})
        rpc.request.assert_not_called()

if __name__ == '__main__': unittest.main()
