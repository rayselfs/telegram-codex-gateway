"""Fixed per-owner process recovery. Never dispatches or replays a model turn."""
import asyncio
import time


class WorkerSupervisor:
    def __init__(self, journal, quiesce, restore):
        self.journal, self.quiesce, self.restore = journal, quiesce, restore
        self.lock = asyncio.Lock()
        self.state = journal.get('worker_supervisor',
            {'phase':'ready','attempt':0,'generation':0,'due':0})
        if self.state['phase'] == 'starting':
            self.state['phase'] = 'unknown'
            self.save()

    def save(self): self.journal.set('worker_supervisor',self.state)

    async def start(self, *, now=None):
        """One Gateway cold start; successful logins do not spend the crash budget."""
        now=time.time() if now is None else now
        async with self.lock:
            state=self.state
            if state['phase']!='ready':return
            state['phase']='starting';self.save()
            try:
                await self.restore()
            except Exception:
                state.update(attempt=state['attempt']+1)
                state.update(phase='exhausted' if state['attempt']>=3 else 'waiting',
                             due=now+15*(2**(state['attempt']-1)))
            else:
                state.update(phase='ready',generation=state['generation']+1,due=0)
            self.save()

    def request(self, args):
        if args != {'action':'restart'}:
            raise ValueError('Only current-owner restart request is supported; no PID/path/command')
        if self.state['phase'] in ('exhausted','unknown'):
            raise RuntimeError('Maintenance inspection required; restart budget is not reset by requests')
        self.state['requested'] = True
        self.save()
        return {'state':'requested','generation':self.state['generation']}

    async def tick(self, *, failed, now=None):
        now = time.time() if now is None else now
        if self.lock.locked(): return
        async with self.lock:
            state = self.state
            if state['phase'] in ('exhausted','unknown'): return
            if state['phase'] == 'ready':
                if not failed and not state.get('requested'):
                    if not state.get('healthy_since'):state['healthy_since']=now;self.save()
                    elif now-state['healthy_since']>=3600 and state['attempt']:
                        state['attempt']=0;self.save()
                else:state.pop('healthy_since',None)
                if not failed and not state.get('requested'): return
                state.update(phase='quiescing',requested=False)
                self.save()
                try: await self.quiesce()
                except Exception:
                    state['phase']='unknown';self.save();return
                if state['attempt']>=3:
                    state['phase']='exhausted';self.save();return
                state.update(phase='waiting',due=now+15)
                self.save()
                return
            if state['phase']=='quiescing':
                state['phase']='unknown';self.save();return
            if state['phase']!='waiting' or state['due']>now: return
            if not failed:
                # A live process during a persisted wait is inconsistent; never overwrite it.
                state['phase']='unknown';self.save();return
            state.update(phase='starting',attempt=state['attempt']+1)
            self.save()
            try:
                await self.restore()
            except Exception:
                state.update(phase='exhausted' if state['attempt']>=3 else 'waiting',
                             due=now+15*(2**state['attempt']))
            else:
                state.update(phase='ready',generation=state['generation']+1,due=0)
            self.save()
