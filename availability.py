"""Local availability transitions. Sudden power loss requires an external observer."""
import asyncio
import secrets
import time


class Availability:
    def __init__(self, transport, journal, *, offline_delay=120, recovery_delay=15, clock=time.monotonic):
        self.tg, self.journal = transport, journal
        saved = journal.get('availability_state')
        self.state = saved if saved in ('online', 'offline') else None
        self.candidate = None
        self.since = 0
        self.clock, self.offline_delay, self.recovery_delay = clock, offline_delay, recovery_delay

    async def update(self, online, reason='', *, force=False):
        target = 'online' if online else 'offline'
        if self.state is None and online:
            # Establish the initial baseline silently; startup is not an outage recovery.
            self.state = target
            self.journal.set('availability_state', target)
            self.candidate = None
            return
        if self.state == target or (self.state is None and not online):
            self.candidate = None
            return
        if not force and self.state is not None:
            if self.candidate != target:
                self.candidate, self.since = target, self.clock()
            delay = self.recovery_delay if online else self.offline_delay
            if self.clock() - self.since < delay:
                return
        self.candidate = None
        self.state = target
        self.journal.set('availability_state', target)
        key = 'availability:' + secrets.token_hex(12)
        self.journal.delivery(key, 'pending')
        text = ('Codex Gateway 已恢復使用，可以繼續傳送訊息。\n可用 /status 查看自動接續或待核對狀態；送出結果不明的工作不會自動重送。'
                if online else 'Codex Gateway 暫時無法使用。'+reason+'恢復後會通知你。')
        try:
            receipts = await asyncio.wait_for(self.tg.say(text), timeout=8)
            self.journal.delivery(key, 'delivered', receipts)
        except Exception:
            # No retries after ambiguous sends; report recovery once reachable again.
            self.journal.delivery(key, 'unknown')
