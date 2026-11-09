import asyncio
from contextvars import ContextVar
DELIVERY_REF = ContextVar('delivery_reference', default=None)
SOURCE_MESSAGE = ContextVar("telegram_source_message", default=None)
from pathlib import Path
import time
import uuid
from types import SimpleNamespace
from telegram.ext import Application, MessageHandler, CallbackQueryHandler, filters
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, BotCommandScopeChat, ReplyParameters
from telegram.request import HTTPXRequest
from gateway.core.maintenance import held, require_live
from gateway.telegram.ui import COMMANDS

MAX_INPUT = 19 * 1024 * 1024


class MaintenanceRequest(HTTPXRequest):
    async def do_request(self, *args, **kwargs):
        require_live()
        return await super().do_request(*args, **kwargs)


class Transport:
    def __init__(self, token, owner, inbox):
        if type(owner) is not int or owner <= 0:
            raise ValueError('A verified numeric owner is required')
        self.token = token
        self._polling_last_progress_monotonic = None
        self.poll_failures = 0
        self.last_poll_error = None
        self._app = None
        self._poller = None
        self.owner = owner
        self.inbox = Path(inbox)
        self.controller = None
        self.seen_update = False
        self.peers = {owner: self}
        self.connection = self
        self.display_name = '使用者'
        self.menu_ready = False
        self.login = None
        self.voice_task = None

    members = None

    def require_member(self):
        if self.members is not None:
            return self.members.require_active(self.owner)

    async def connect(self, *, is_reconnect=False):
        require_live()
        self._app = (Application.builder().token(self.token)
                     .request(MaintenanceRequest()).get_updates_request(MaintenanceRequest()).build())
        self._register_handlers(self._app)
        await self._app.initialize()
        self._bot = self._app.bot
        await self._app.start()
        self._poller = asyncio.create_task(self._poll())
        return True

    async def _poll(self):
        offset = None
        while True:
            try:
                updates = await self._bot.get_updates(offset=offset, timeout=25,
                    read_timeout=35, allowed_updates=['message','callback_query'])
                if held():
                    return
                self._polling_last_progress_monotonic = time.monotonic()
                self.poll_failures = 0
                for update in updates:
                    await self._app.update_queue.put(update)
                    offset = update.update_id + 1
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.poll_failures += 1
                self.last_poll_error = type(error).__name__
                await asyncio.sleep(3)

    async def disconnect(self):
        if self._poller:
            self._poller.cancel()
            await asyncio.gather(self._poller, return_exceptions=True)
        if self._app:
            if self._app.running:await self._app.stop()
            await self._app.shutdown()

    async def send(self, chat_id, text, metadata=None):
        import hashlib
        ids=[];batch=uuid.uuid4().hex
        journal=getattr(self.controller,'journal',None)
        record=getattr(journal,'chunk',None)
        ref=DELIVERY_REF.get() or (str(getattr(self.controller,'thread',None))+':'+str(getattr(self.controller,'turn',None)))
        # Plain text avoids malformed Markdown and keeps all original text.
        remaining=text or '（無文字）'
        while remaining:
            chunk=remaining[:1900];remaining=remaining[len(chunk):]
            reply=(metadata or {}).get('reply_to')
            part=len(ids);digest=hashlib.sha256(chunk.encode()).hexdigest()
            if record:record(batch,part,ref,digest,'sending')
            try:
                message=await self._bot.send_message(chat_id=chat_id,text=chunk,
                    **({'reply_parameters':ReplyParameters(message_id=reply,allow_sending_without_reply=True)} if reply else {}))
            except BaseException:
                if record:record(batch,part,ref,digest,'unknown')
                raise
            if record:record(batch,part,ref,digest,'delivered',message.message_id)
            ids.append(message.message_id)
        return SimpleNamespace(success=True,raw_response={'message_ids':ids})

    async def send_document(self, chat_id, path, file_name=None):
        with open(path,'rb') as file:
            message=await self._bot.send_document(chat_id=chat_id,document=file,filename=file_name,
                write_timeout=120,read_timeout=120)
        return SimpleNamespace(success=True,message_id=message.message_id)

    async def send_typing(self, chat_id):
        await self._bot.send_chat_action(chat_id=chat_id,action='typing')

    def _register_handlers(self, app):
        app.add_handler(CallbackQueryHandler(self.route, block=False))
        app.add_handler(MessageHandler(filters.ALL, self.route, block=False))

    async def route(self, update, context):
        if held():
            user = update.effective_user
            peer = self.peers.get(user.id) if user else None
            if peer and peer.controller and peer.authorized(update):
                peer.controller.journal.receive(update.update_id, state='held-maintenance')
            return
        registration = getattr(self, 'registration', None)
        if registration is not None and await registration.handle(update): return
        user = update.effective_user
        peer = self.peers.get(user.id) if user else None
        if peer and peer.authorized(update):
            await peer.ingress(update, context)

    async def typing_tick(self):
        try: self.require_member()
        except PermissionError: return
        c = self.controller
        if c and (c.busy or self.voice_task) and not c.pending and not c.unknown and not c.rpc.failure and not c.closed:
            await self.send_typing(str(self.owner))

    async def typing_loop(self):
        while not self.controller.closed:
            try:
                await self.typing_tick()
            except Exception:
                pass  # Status decoration must not interrupt the user's work.
            await asyncio.sleep(4)

    def _wire_plugin_handlers(self, native=None):
        pass

    def _start_post_connect_housekeeping(self):
        pass

    def authorized(self, update):
        try:
            self.require_member()
        except PermissionError:
            return False
        user, chat = update.effective_user, update.effective_chat
        return bool(user and chat and not user.is_bot and user.id == self.owner
                    and chat.type == 'private' and chat.id == self.owner)

    def health(self):
        if self.connection is not self:
            return self.connection.health()
        last = self._polling_last_progress_monotonic
        return 'ready' if last and time.monotonic() - last < 90 else 'degraded'

    async def ingress(self, update, context):
        if not self.authorized(update):
            return
        self.seen_update = True
        if not self.menu_ready:
            try:
                await self.install_menu()
            except Exception:
                pass  # Menu setup can retry on the next authenticated update.
        if update.callback_query:
            try:
                await self.controller.callback(update.callback_query)
            except Exception:
                self.controller.unknown = True
            return
        if not update.message or update.edited_message:
            return
        if not self.controller.journal.receive(update.update_id):
            return
        attachment_lock = False
        source_token = SOURCE_MESSAGE.set(getattr(update.message,'message_id',None))
        try:
            msg = update.message
            text = msg.text or msg.caption or ''
            if text.split(' ',1)[0].lower() == '/stop' and self.voice_task:
                self.voice_task.cancel()
                await self.say('已取消尚未提交的語音辨識。')
                if not self.controller.busy:
                    return
            if self.login and await self.login.handle(msg, text):
                return
            if self.login and self.login.active:
                await self.say('外部服務登入進行中，請完成登入或用 /login_cancel 取消後再開始工作。')
                return
            if msg.media_group_id:
                await self.say('首版不接受相簿，請逐個傳送檔案。')
                return
            if getattr(msg, 'voice', None):
                return await self.receive_voice(msg.voice)
            attachment = msg.document or (msg.photo[-1] if msg.photo else None)
            if attachment:
                if self.controller.busy or self.controller.commands.locked():
                    await self.say('工作進行中，附件尚未接收；請完成後再傳送。')
                    return
                await self.controller.commands.acquire()
                attachment_lock = True
                if not attachment.file_size or attachment.file_size > MAX_INPUT:
                    raise ValueError('附件需小於 19 MiB 且提供檔案大小。')
                self.inbox.mkdir(parents=True, exist_ok=True)
                suffix = '.jpg' if msg.photo else Path(attachment.file_name or '').suffix[:16]
                if not suffix.replace('.', '').isalnum():
                    suffix = '.bin'
                dest = self.inbox / (uuid.uuid4().hex + suffix)
                remote = await attachment.get_file()
                # Stream with a byte cap rather than trusting Telegram metadata.
                import httpx
                size = 0
                try:
                    async with httpx.AsyncClient(timeout=60, follow_redirects=False) as client:
                        require_live()
                        async with client.stream('GET', remote.file_path) as response:
                            response.raise_for_status()
                            with dest.open('xb') as out:
                                async for chunk in response.aiter_bytes():
                                    size += len(chunk)
                                    if size > MAX_INPUT:
                                        raise ValueError('附件超過大小限制。')
                                    out.write(chunk)
                    if size != attachment.file_size:
                        raise ValueError('附件大小不一致，未提交。')
                except Exception:
                    dest.unlink(missing_ok=True)
                    raise
                inputs = [{'type': 'text', 'text': text or '請檢視附件。'}]
                if msg.photo:
                    inputs.append({'type': 'localImage', 'path': str(dest)})
                else:
                    inputs[0]['text'] += '\n附件本機路徑：' + str(dest) + '\n原檔名：' + attachment.file_name
                await self.controller.start(inputs)
            elif text:
                await self.controller.handle(text)
            else:
                await self.say('請使用文字、語音、圖片或以「檔案」傳送附件。')
        except Exception:
            # Never echo network exception text: URLs can contain the Bot token.
            await self.say('這個操作沒有完成。請用 /status 查看狀態；若是指令用法不符，可用 /help 查看說明。')
        finally:
            SOURCE_MESSAGE.reset(source_token)
            if attachment_lock:
                self.controller.commands.release()

    async def receive_voice(self, attachment):
        self.require_member()
        if self.voice_task:
            return await self.say('上一則語音仍在辨識；請稍後再傳，或用 /stop 取消。')
        if not attachment.file_size or attachment.file_size > MAX_INPUT or attachment.duration > 300:
            return await self.say('語音請控制在 5 分鐘、19 MiB 以內。')
        if self.controller.pending or self.controller.commands.locked():
            return await self.say('目前操作尚未完成；這則語音未接收，請稍後重送。')
        expected = (self.controller.thread, self.controller.turn) if self.controller.busy else None
        initial_thread = self.controller.thread
        self.voice_task = asyncio.current_task()
        self.inbox.mkdir(parents=True, exist_ok=True)
        dest = self.inbox / (uuid.uuid4().hex + '.ogg')
        try:
            await self.say('正在辨識語音。')
            remote = await attachment.get_file()
            import httpx
            size = 0
            async with httpx.AsyncClient(timeout=60, follow_redirects=False) as client:
                require_live()
                async with client.stream('GET', remote.file_path) as response:
                    response.raise_for_status()
                    with dest.open('xb') as out:
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if size > MAX_INPUT:
                                raise ValueError('Voice too large')
                            out.write(chunk)
            if size != attachment.file_size:
                raise ValueError('Voice length mismatch')
            from gateway.telegram.voice import transcribe
            self.require_member()
            text = await transcribe(dest)
            await self.say('語音辨識：'+text)
            inputs = [{'type':'text','text':'以下是使用者語音辨識內容；人名、金額或日期有疑義時先確認：\n'+text}]
            if expected:
                await self.controller.steer(inputs, expected)
            elif self.controller.busy or self.controller.thread != initial_thread or self.controller.commands.locked():
                await self.say('辨識期間工作已變更，這段語音沒有自動插入或另開工作；請核對上方文字後重新傳送。')
            else:
                async with self.controller.commands:
                    await self.controller.start(inputs)
        except asyncio.CancelledError:
            return
        except Exception:
            await self.say('語音辨識未完成，沒有交給 Codex；請改傳文字或較短且清楚的語音。')
        finally:
            self.voice_task = None
            dest.unlink(missing_ok=True)

    async def say(self, text, *, reply_to=None):
        result = await self.send(str(self.owner), text, metadata={'notify': True, 'reply_to': reply_to})
        if not result.success:
            raise RuntimeError('Telegram delivery not confirmed')
        raw = getattr(result, 'raw_response', None) or {}
        return raw.get('message_ids') or [getattr(result, 'message_id', None),
                                          *(getattr(result, 'continuation_message_ids', None) or [])]

    async def buttons(self, text, rows):
        return await self._bot.send_message(chat_id=self.owner, text=text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=data)
                                               for label, data in row] for row in rows]))

    async def install_menu(self):
        await self._bot.set_my_commands([BotCommand(name, description) for name, description in COMMANDS],
            scope=BotCommandScopeChat(chat_id=self.owner), language_code='')
        self.menu_ready = True

    async def document(self, path, name=None):
        result = await self.send_document(str(self.owner), str(path), file_name=name or path.name)
        if not result.success:
            raise RuntimeError('File delivery not confirmed')
        return {'message_id':getattr(result,'message_id',None)}
