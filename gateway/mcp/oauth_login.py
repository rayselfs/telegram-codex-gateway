from gateway.platforms import current
process_options = current().process_options
"""Per-owner native Codex OAuth. Callback codes never enter model input or logs."""
import asyncio
import hmac
import os
import re
import subprocess
import threading
import time
import tomllib
from urllib.parse import urlsplit, parse_qs
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from gateway.core.bootstrap import CODEX
from gateway.platforms.lifecycle import ChildJob


KNOWN_ORIGINS = {'notion': ('Notion', 'mcp.notion.com'), 'canva': ('Canva', 'mcp.canva.com')}

def authorization_details(url, service='notion'):
    parsed = urlsplit(url)
    q = parse_qs(parsed.query)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.fragment
            or any(c in url for c in '\r\n\x00')
            or (service in KNOWN_ORIGINS and parsed.hostname != KNOWN_ORIGINS[service][1])):
        raise ValueError('Unexpected authorization origin')
    if any(len(q.get(k, [])) != 1 for k in ('state', 'redirect_uri')):
        raise ValueError('Invalid authorization parameters')
    redirect = urlsplit(q['redirect_uri'][0])
    if (redirect.scheme != 'http' or redirect.hostname not in ('127.0.0.1', 'localhost', '::1')
            or redirect.username or not redirect.port or redirect.fragment or redirect.query):
        raise ValueError('Expected native loopback callback')
    return q['state'][0], q['redirect_uri'][0]


def validate_callback(url, state, redirect):
    if len(url) > 16000 or any(c in url for c in '\r\n\x00'):
        raise ValueError('Invalid callback')
    got, expected = urlsplit(url), urlsplit(redirect)
    if (got.scheme, got.netloc, got.path) != (expected.scheme, expected.netloc, expected.path) or got.fragment:
        raise ValueError('Callback belongs to another login')
    q = parse_qs(got.query)
    if len(q.get('state', [])) != 1 or not hmac.compare_digest(q['state'][0], state):
        raise ValueError('Callback state mismatch')
    if len(q.get('code', [])) != 1 or not q['code'][0] or 'error' in q:
        raise ValueError('Authorization code missing')
    return url


class ServiceLogin:
    def __init__(self, transport, home, *, policy=None):
        self.policy=policy
        self.tg, self.home = transport, home
        self.proc = self.job = self.task = None
        self.output = bytearray()
        self.state = self.redirect = None
        self.started = 0
        self.submitted = False
        self.lock = asyncio.Lock()
        self.link_message = None
        self.lease = None
        self.service = 'notion'

    @property
    def label(self):
        return KNOWN_ORIGINS.get(self.service,(self.service,None))[0]

    def servers(self):
        config=self.home/'codex-home/config.toml'
        data=tomllib.loads(config.read_text(encoding='utf-8-sig')) if config.exists() else {}
        return {name:value for name,value in data.get('mcp_servers',{}).items()
                if re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*',name) and value.get('enabled',True)
                and (self.policy is None or self.policy.allowed(self.tg.owner,name,value))}

    async def server_statuses(self):
        rows=[];cursor=None;seen=set()
        while True:
            result=await self.tg.controller.rpc.call('mcpServerStatus/list',{'limit':100,'cursor':cursor})
            rows.extend(result['data']);cursor=result.get('nextCursor')
            if not cursor:return {row['name']:row for row in rows}
            if cursor in seen:raise ValueError('Repeated status cursor')
            seen.add(cursor)

    @property
    def active(self):
        return self.proc is not None

    async def handle(self, message, text):
        command = text.split(' ', 1)[0].split('@')[0].lower()
        callback = bool(re.search(r'https?://(?:127\.0\.0\.1|localhost|\[::1\])[^\s]*[?&](?:code|state)=', text))
        if command not in ('/login', '/login_complete', '/login_status', '/login_cancel') and not callback:
            return False
        if command == '/login_complete' or callback:
            # Telegram deletion is best effort; the code is short-lived and PKCE-bound.
            try:
                await message.delete()
            except Exception:
                await self.tg.say('這則授權回傳訊息無法自動刪除，請自行刪除；不要轉傳給別人。')
        async with self.lock:
            if command == '/login':
                service = text.partition(' ')[2].strip()
                servers=self.servers()
                if service not in servers:
                    await self.tg.say('請用 /login 服務名稱，僅限本人已啟用的 MCP：'+('、'.join(servers) or '尚無'))
                elif self.active:
                    await self.tg.say('已有登入進行中；完成授權或用 /login_cancel 取消。')
                elif self.tg.controller.busy or self.tg.controller.pending:
                    await self.tg.say('請等目前工作完成後再登入外部服務。')
                else:
                    self.service = service
                    try:status=(await self.server_statuses()).get(service,{})
                    except Exception:
                        await self.tg.say('暫時無法核對既有登入；請稍後用 /login_status 查詢，未啟動新授權。')
                        return True
                    if status.get('authStatus') in ('oAuth','bearerToken') or status.get('tools'):
                        await self.tg.say(self.label+(' 已連線，沿用既有登入，工具可用。' if status.get('tools') else ' 已有授權，工具尚未就緒；未要求重新登入。'))
                    elif not str(servers[service].get('url','')).startswith('https://'):
                        await self.tg.say('此 MCP 不是 HTTPS OAuth 入口；若需 API key 或其他設定，請使用本機安全設定，不要貼憑證。')
                    else:
                        await self.start()
            elif command == '/login_cancel':
                await self.close()
                await self.tg.say('登入流程已取消，既有授權未移除。')
            elif command == '/login_status':
                service = text.partition(' ')[2].strip()
                if service and service not in self.servers():
                    await self.tg.say('請用 /login_status 查看本人已註冊 MCP，或加上服務名稱。')
                else:
                    await self.status(service or None)
            else:
                if not self.active or not self.state or time.monotonic()-self.started > 600:
                    await self.tg.say('沒有有效的登入流程，請重新輸入 /login 服務名稱。')
                elif self.submitted:
                    await self.tg.say('正在驗證授權，請稍候。')
                else:
                    url = text.partition(' ')[2].strip() if command == '/login_complete' else text.strip()
                    try:
                        validate_callback(url, self.state, self.redirect)
                        self.proc.stdin.write((url+'\n').encode('utf-8'))
                        self.proc.stdin.flush()
                        self.submitted = True
                    except Exception:
                        await self.tg.say('回傳網址不符合這次登入；請使用你剛才授權後的完整網址，或取消後重試。')
        return True

    async def start(self):
        if self.policy is not None and self.service not in self.servers():raise PermissionError('Service authorization changed')
        from gateway.platforms.lifecycle import SingleInstance
        try:self.lease=SingleInstance(self.home/'execution.lock')
        except RuntimeError:
            await self.tg.say('本人工作或排程正在執行，請完成後再登入。')
            return
        env = dict(os.environ, CODEX_HOME=str(self.home/'codex-home'),
                   CODEX_SQLITE_HOME=str(self.home/'codex-home'),
                   TEMP=str(self.home/'temp'), TMP=str(self.home/'temp'))
        self.output = bytearray()
        self.started, self.submitted = time.monotonic(), False
        try:
            self.proc = subprocess.Popen([str(CODEX), 'mcp', 'login', self.service, '--no-browser'],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                env=env, cwd=str(self.home), **process_options())
            self.job = ChildJob(self.proc)
            proc, output = self.proc, self.output
            def read():
                while chunk := proc.stdout.read(1):
                    if len(output) >= 65536:
                        proc.terminate()
                        return
                    output.extend(chunk)
            threading.Thread(target=read, daemon=True).start()
            for _ in range(200):
                match = re.search(rb'https://[^\s]+\r?\n', bytes(output))
                if match:
                    url = match.group().decode('utf-8').strip()
                    # Only the pinned native CLI for this owner's registered service supplies this URL.
                    self.state, self.redirect = authorization_details(url, self.service)
                    result = await self.tg._bot.send_message(chat_id=self.tg.owner,
                        text=f'請在外部瀏覽器開啟下方連結，用你自己的 {self.label} 帳號登入並授權。完成後若手機顯示無法開啟頁面，這是正常的：複製網址列的完整網址，回到這裡貼上。\n回傳網址含一次性授權碼，只用於本次登入，不會交給模型，收到後會嘗試刪除。10 分鐘內有效。',
                        protect_content=True, reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton('登入 '+self.label, url=url)]]))
                    self.link_message = result.message_id
                    self.task = asyncio.create_task(self.monitor())
                    return
                if proc.poll() is not None:
                    break
                await asyncio.sleep(.1)
            raise RuntimeError('OAuth link unavailable')
        except Exception:
            await self.close()
            await self.tg.say(f'目前無法建立 {self.label} 登入連結。此服務可能不支援原生 OAuth 或需本機設定；既有憑證未移除。')

    async def status(self, service=None):
        if self.active:
            await self.tg.say(('正在驗證 '+self.label+' 授權。') if self.submitted else ('等待你完成 '+self.label+' 授權並貼回網址。'))
            return
        try:
            response = await self.server_statuses()
            lines=[]
            for key in ([service] if service else self.servers()):
                server=response.get(key,{})
                label=KNOWN_ORIGINS.get(key,(key,None))[0]
                if server.get('tools'):
                    lines.append(label+' 已連線，工具可用。')
                elif server.get('authStatus')=='notLoggedIn':
                    lines.append(label+' 尚未登入，可使用 /login '+key+'。')
                else:
                    lines.append(label+' 工具尚未確認可用；請檢查服務設定或連線，現有憑證未變更。')
            await self.tg.say('\n'.join(lines))
        except Exception:
            await self.tg.say('暫時無法確認外部服務連線狀態。')

    async def monitor(self):
        try:
            while self.proc and self.proc.poll() is None and time.monotonic()-self.started < 600:
                await asyncio.sleep(.5)
            async with self.lock:
                success = self.proc is not None and self.proc.poll() == 0
                await self.close(cancel_task=False)
                if success:
                    await self.tg.controller.rpc.call('config/mcpServer/reload', {})
                    await self.tg.say(f'{self.label} 授權已完成，正在載入工具；可用 /login_status {self.service} 確認。')
                else:
                    await self.tg.say(f'{self.label} 登入未完成或已逾時，請重新輸入 /login {self.service}。')
        except asyncio.CancelledError:
            raise
        except Exception:
            await self.close(cancel_task=False)
            await self.tg.say('授權結果需要確認，請使用 /login_status；不要重送先前的回傳網址。')

    async def close(self, cancel_task=True):
        if cancel_task and self.task and self.task is not asyncio.current_task():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        self.task = None
        if self.job:
            self.job.close()
            self.job = None
        if self.proc:
            if self.proc.poll() is None:
                self.proc.terminate()
            await asyncio.to_thread(self.proc.wait, 10)
            self.proc.stdin.close()
            self.proc.stdout.close()
            self.proc = None
        if self.lease:
            self.lease.close();self.lease=None
        self.state = self.redirect = None
        self.output.clear()
        if self.link_message:
            try:
                await self.tg._bot.delete_message(chat_id=self.tg.owner, message_id=self.link_message)
            except Exception:
                pass
            self.link_message = None
