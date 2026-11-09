"""Narrow safety adapter around the installed Hermes wire client."""
import asyncio
import json
import threading
import tomllib
from gateway.core.codex_wire import CodexAppServerClient
from gateway.core.bootstrap import CODEX


class Client(CodexAppServerClient):
    def __init__(self, home, *, browser=True, authorize=None):
        self.authorize = authorize
        if authorize is not None: authorize()
        self._write_lock = threading.Lock()
        self._id_lock = threading.Lock()
        self.failure = None
        tmp = home / 'temp'
        tmp.mkdir(parents=True, exist_ok=True)
        extra_args = []
        if not browser:
            config = home / 'codex-home/config.toml'
            servers = tomllib.loads(config.read_text(encoding='utf-8-sig')).get('mcp_servers', {}) if config.exists() else {}
            for name in ('playwright',):
                if name in servers:
                    extra_args.extend(['-c', f'mcp_servers.{name}.enabled=false'])
        super().__init__(codex_bin=str(CODEX), codex_home=str(home / 'codex-home'),
                         extra_args=extra_args,
                         env={'CODEX_SQLITE_HOME': str(home / 'codex-home'),
                              'TEMP': str(tmp), 'TMP': str(tmp),
                              'XDG_CACHE_HOME': str(home / 'cache'),
                              'UV_CACHE_DIR': str(home / 'cache/uv'),
                              'PIP_CACHE_DIR': str(home / 'cache/pip'),
                              'npm_config_cache': str(home / 'cache/npm')})

    def _take_id(self):
        with self._id_lock:
            return super()._take_id()

    def _send(self, obj):
        data = (json.dumps(obj, ensure_ascii=False) + '\n').encode('utf-8')
        with self._write_lock:
            if self._closed or not self._proc.stdin:
                raise RuntimeError('Codex connection closed')
            view = memoryview(data)
            while view:
                n = self._proc.stdin.write(view)
                if not n:
                    raise RuntimeError('Codex write incomplete')
                view = view[n:]
            self._proc.stdin.flush()

    def _dispatch(self, msg):
        method = msg.get('method', '')
        # Only final items are rendered; do not accumulate token/progress deltas.
        if 'id' not in msg and (method.endswith('/delta') or method.endswith('Delta')):
            return
        if method and max(self._notifications.qsize(), self._server_requests.qsize()) >= 2048:
            self.failure = 'event backlog exceeded limit'
            self._proc.terminate()
            return
        super()._dispatch(msg)

    def _read_stdout(self):
        try:
            super()._read_stdout()
        finally:
            self.failure = self.failure or 'Codex disconnected'
            with self._pending_lock:
                pending = list(self._pending.values())
                self._pending.clear()
            for p in pending:
                p.queue.put_nowait({'error': {'code': -32099, 'message': self.failure}})

    async def call(self, method, params=None):
        if method in {'turn/start', 'turn/steer', 'thread/compact/start'}:
            authorize = getattr(self, 'authorize', None)
            if authorize is not None: authorize()
        return await asyncio.to_thread(self.request, method, params, 30)
