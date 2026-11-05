from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock,Mock
from availability import Availability

class AvailabilityTests(IsolatedAsyncioTestCase):
    async def test_short_failure_has_no_stop_recovery_pair(self):
        now=[0];tg=Mock(say=AsyncMock());a=Availability(tg,Mock(),clock=lambda:now[0])
        await a.update(True);await a.update(False)
        now[0]=119;await a.update(False);await a.update(True)
        self.assertEqual(tg.say.await_count,0)

    async def test_sustained_outage_and_stable_recovery(self):
        now=[0];tg=Mock(say=AsyncMock());a=Availability(tg,Mock(),clock=lambda:now[0])
        await a.update(True);await a.update(False)
        now[0]=120;await a.update(False);self.assertEqual(tg.say.await_count,1)
        await a.update(True);now[0]=134;await a.update(True);self.assertEqual(tg.say.await_count,1)
        now[0]=135;await a.update(True);self.assertEqual(tg.say.await_count,2)
        await a.update(False,force=True);self.assertEqual(tg.say.await_count,3)

    async def test_gateway_poll_outage_is_silent_but_worker_outage_notifies(self):
        import ast
        import asyncio
        from pathlib import Path
        from types import SimpleNamespace
        # Execute the real main-loop notification call with controlled health inputs.
        tree=ast.parse((Path(__file__).parents[1]/'main.py').read_text(encoding='utf-8'))
        call=next(n for n in ast.walk(tree) if isinstance(n,ast.Await)
                  and "r['availability'].update" in ast.unparse(n)
                  and "force=True" not in ast.unparse(n))
        code=compile(ast.Expression(call.value),'<gateway availability>','eval')
        now=[0];tg=Mock(say=AsyncMock());journal=Mock()
        a=Availability(tg,journal,clock=lambda:now[0])
        rpc=SimpleNamespace(failure=None)
        context={'asyncio':asyncio,'resources':[{'availability':a,'rpc':rpc}],
                 'connection':Mock(health=Mock(return_value='ready'))}
        async def tick(): await eval(code,context)
        await tick()
        context['connection'].health.return_value='degraded'
        await tick();now[0]=600;await tick()
        context['connection'].health.return_value='ready'
        await tick();now[0]=620;await tick()
        self.assertEqual(tg.say.await_count,0)
        rpc.failure='worker exited';await tick();now[0]=740;await tick()
        self.assertEqual(tg.say.await_count,1)
        rpc.failure=None;await tick();now[0]=755;await tick()
        self.assertEqual(tg.say.await_count,2)
