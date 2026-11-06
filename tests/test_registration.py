import importlib.util
import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from contextlib import closing
from unittest.mock import AsyncMock
from gateway.core.members import Members

class RegistrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_admin_telegram_flow_and_pending_gate(self):
        self.assertIsNotNone(importlib.util.find_spec('gateway.telegram.registration'),'registration routing missing')
        from gateway.telegram.registration import Registration
        tmp=tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        root=Path(tmp.name)
        with closing(Members(root/'members.sqlite',root)) as members:
            members.import_existing([dict(owner_id=n,name=str(n),home=str(root/f'{n}/profile'),workspace=str(root/f'{n}/workspace')) for n in (101,102)],101)
            send,login,activate=AsyncMock(),AsyncMock(),AsyncMock()
            service=Registration(members,send,login,AsyncMock(return_value=True),activate,'example_bot')
            def update(owner,text,kind='private'):
                return NS(effective_user=NS(id=owner,is_bot=False,full_name='same name'),effective_chat=NS(id=owner,type=kind),message=NS(text=text))
            self.assertTrue(await service.handle(update(102,'/invite')))
            self.assertEqual(members.list_members(101)[-1]['owner_id'],102)
            await service.handle(update(101,'/invite'))
            token=send.call_args.args[1].split('invite_',1)[1]
            await service.handle(update(103,'/start invite_'+token))
            await service.handle(update(103,'/login codex')); login.assert_not_awaited()
            await service.handle(update(101,'/members approve 103'))
            await service.handle(update(103,'/login codex')); await asyncio.sleep(0); login.assert_awaited_once()
            await service.handle(update(101,'/members activate 103'))
            await service.handle(update(101,'/members activate 103'))
            activate.assert_awaited_once()
            self.assertEqual(members.require_active(103)['role'],'staff')
            self.assertEqual(len(members.active_profiles()),3)
            self.assertFalse(await service.handle(update(101,'/invite','group')))
            await service.handle(update(101,'/members suspend 103 test'))
            self.assertTrue(await service.handle(update(103,'normal message')))
            self.assertEqual(members.require_active(102)['state'],'active')

    async def test_pending_login_does_not_block_active_members(self):
        from gateway.telegram.registration import Registration
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            with closing(Members(root/'members.sqlite',root)) as members:
                members.import_existing([dict(owner_id=n,name=str(n),home=str(root/f'{n}/profile'),workspace=str(root/f'{n}/workspace')) for n in (101,102)],101)
                members.claim(members.invite(101),103,'member_c');members.approve(101,103);members.provision(101,103)
                started=asyncio.Event();finish=asyncio.Event()
                async def login(row):started.set();await finish.wait()
                service=Registration(members,AsyncMock(),login,AsyncMock(),AsyncMock(),'example_bot')
                def update(owner,text):return NS(effective_user=NS(id=owner,is_bot=False),effective_chat=NS(id=owner,type='private'),message=NS(text=text))
                try:
                    await asyncio.wait_for(service.handle(update(103,'/login codex')),timeout=.2)
                    await asyncio.wait_for(started.wait(),timeout=.2)
                    self.assertFalse(await service.handle(update(102,'existing conversation')))
                    await service.handle(update(103,'/login codex'))
                    self.assertEqual(len(service.login_tasks),1)
                finally:
                    finish.set();await service.close()

if __name__=='__main__': unittest.main()
