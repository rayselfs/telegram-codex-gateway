"""Per-owner native Codex OAuth. Callback codes never enter model input or logs."""
import asyncio
import hmac
import os
import re
import subprocess
import threading
import time
from urllib.parse import urlsplit, parse_qs
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from bootstrap import CODEX
from lifecycle import ChildJob


PROVIDERS = {'notion': ('Notion', 'mcp.notion.com'), 'canva': ('Canva', 'mcp.canva.com')}

def authorization_details(url, service='notion'):
    parsed = urlsplit(url)
    q = parse_qs(parsed.query)
    if parsed.scheme != 'https' or parsed.hostname != PROVIDERS[service][1] or parsed.username:
        raise ValueError('Unexpected authorization origin')
    if any(len(q.get(k, [])) != 1 for k in ('state', 'redirect_uri')):
        raise ValueError('Invalid authorization parameters')
    redirect = urlsplit(q['redirect_uri'][0])
    if redirect.scheme != 'http' or redirect.hostname not in ('127.0.0.1', 'localhost', '::1'):
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
    def __init__(self, transport, home):
        self.tg, self.home = transport, home
        self.proc = self.job = self.task = None
        self.output = bytearray()
        self.state = self.redirect = None
        self.started = 0
        self.submitted = False
        self.lock = asyncio.Lock()
        self.link_message = None
        self.service = 'notion'

    @property
    def label(self):
        return PROVIDERS[self.service][0]

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
                service = text.partition(' ')[2].strip().lower()
                if service not in PROVIDERS:
                    await self.tg.say('請輸入 /login notion 或 /login canva，使用你自己的帳號授權。')
                elif self.active:
                    await self.tg.say('已有登入進行中；完成授權或用 /login_cancel 取消。')
                elif self.tg.controller.busy or self.tg.controller.pending:
                    await self.tg.say('請等目前工作完成後再登入外部服務。')
                else:
                    self.service = service
                    await self.start()
            elif command == '/login_cancel':
                await self.close()
                await self.tg.say('登入流程已取消，既有授權未移除。')
            elif command == '/login_status':
                service = text.partition(' ')[2].strip().lower()
                if service and service not in PROVIDERS:
                    await self.tg.say('可查詢 /login_status notion 或 /login_status canva。')
                else:
                    await self.status(service or None)
            else:
                if not self.active or not self.state or time.monotonic()-self.started > 600:
                    await self.tg.say('沒有有效的登入流程，請重新輸入 /login notion 或 /login canva。')
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
        env = dict(os.environ, CODEX_HOME=str(self.home/'codex-home'),
                   CODEX_SQLITE_HOME=str(self.home/'codex-home'),
                   TEMP=str(self.home/'temp'), TMP=str(self.home/'temp'))
        self.output = bytearray()
        self.started, self.submitted = time.monotonic(), False
        self.proc = subprocess.Popen([str(CODEX), 'mcp', 'login', self.service, '--no-browser'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            env=env, cwd=str(self.home), creationflags=subprocess.CREATE_NO_WINDOW)
        try:
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
            await self.tg.say(f'目前無法建立 {self.label} 登入連結，請稍後重試。')

    async def status(self, service=None):
        if self.active:
            await self.tg.say(('正在驗證 '+self.label+' 授權。') if self.submitted else ('等待你完成 '+self.label+' 授權並貼回網址。'))
            return
        try:
            response = await self.tg.controller.rpc.call('mcpServerStatus/list', {'limit':100})
            lines=[]
            for key in ([service] if service else PROVIDERS):
                server = next((s for s in response['data'] if s['name']==key), {})
                ready = server.get('authStatus') == 'oAuth' and bool(server.get('tools'))
                label=PROVIDERS[key][0]
                lines.append(label+' 已連線，工具可用。' if ready else label+' 尚未確認可用，請輸入 /login '+key+' 登入。')
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
        self.state = self.redirect = None
        self.output.clear()
        if self.link_message:
            try:
                await self.tg._bot.delete_message(chat_id=self.tg.owner, message_id=self.link_message)
            except Exception:
                pass
            self.link_message = None
