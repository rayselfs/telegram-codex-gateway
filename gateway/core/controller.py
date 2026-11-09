import asyncio
import json
from pathlib import Path
import secrets
import time
from gateway.core.state import safe_file, snapshot_file
from gateway.telegram.ui import approval_text, approval_details
from gateway.core.personal_memory import PersonalMemory
from gateway.core.recovery import Recovery, PROMPT as RECOVERY_PROMPT

HELP = '''我是 Codex Gateway，透過 Codex 協助你完成工作。
你可以請我討論計劃、撰寫整理文字、處理工作區檔案、檢視圖片，或使用已配置的技能。
例如：「幫我整理這份文件的重點」、「先規劃這件事的步驟」。
外部服務以目前已連接的功能為準；完成後會說明結果與仍需確認的事項。

/new 新對話 · /sessions 列出對話 · /use ID 切換
/status 狀態 · /stop 中止 · /steer 內容 補充目前工作
/compact 壓縮 · /model [ID] · /effort [值]
/plan · /default · /skills
/cron 查看排程；/cron 每天九點提醒我…… 建立排程（先 /new 載入新功能）
/result 取回目前對話最近結果（可能重複交付）
/get 相對路徑 下載工作區檔案
/login 服務名稱 登入本人已註冊的 MCP（如 notion、canva、xmind）· /login_status [服務名稱] 查連線 · /login_cancel 取消登入
/trust 查看、信任或撤銷本人 MCP 服務
/memory 查看及管理個人記憶
可請我修復本人工具環境、製作或發布共用技能；更新後先 /new 載入這些能力。共用技能只有作者能透過發布工具更新。
可直接傳語音。工作進行中的文字與語音會插入目前工作；批准與提問請使用指定按鈕或 /answer。'''


class Controller:
    def __init__(self, rpc, transport, journal, workspace, *, personal_instructions=None):
        self.rpc, self.tg, self.journal = rpc, transport, journal
        self.workspace = Path(workspace).resolve()
        self.personal_instructions = personal_instructions
        self.thread = journal.get('thread')
        self.turn = None
        self.busy = False
        self.unknown = bool(journal.db.execute("SELECT 1 FROM jobs WHERE thread=? AND state='unknown'",
                                              (self.thread,)).fetchone())
        self.mode = journal.get('mode', 'default')
        self.model = journal.get('model')
        self.effort = journal.get('effort')
        self.pending = {}
        self.skill_choices = {}
        self.selected_skill = None
        self.shared_skills = None  # Bound by maintenance to the verified Telegram owner.
        self.permission_mode = 'workspace-write'
        self.self_help = None
        self.models = []
        self.outbox = asyncio.Queue(maxsize=32)
        self.progress_turn, self.progress_last, self.progress_count = None, 0, 0
        self.initializing = False
        self.closed = False
        self.commands = asyncio.Lock()
        self.schedule_ui = None
        self.execution_lock_path = None
        self.execution_lease = None
        self.stopping = False
        self.memory = PersonalMemory(journal)
        self.interactive = True
        self.stop_key = None
        self.active_model = None
        self.recovery = Recovery(self)
        transport.controller = self

    authorize = None

    capability_tools = None
    shared_policy = None
    persona_revision = None

    async def sync_persona(self, force=False):
        if self.shared_policy is None: return
        self.require_member()
        revision = self.shared_policy.active_persona()
        if self.thread and (force or self.journal.get('persona_version:'+self.thread) != revision['version']):
            await self.rpc.call('thread/inject_items', {'threadId':self.thread, 'items':[
                {'type':'message','role':'developer','content':[{'type':'input_text','text':revision['body']}]}]})
            self.journal.set('persona_version:'+self.thread, revision['version'])
        self.persona_revision = revision

    def require_member(self):
        if self.authorize is not None:
            self.authorize()

    def acquire_execution(self):
        if self.execution_lock_path and self.execution_lease is None:
            from gateway.platforms.lifecycle import SingleInstance
            self.execution_lease = SingleInstance(self.execution_lock_path)

    def release_execution(self):
        if self.execution_lease:
            self.execution_lease.close()
            self.execution_lease = None

    async def initialize(self, resume_thread=True):
        account = await self.rpc.call('account/read', {'refreshToken': False})
        if (account.get('account') or {}).get('type') != 'chatgpt':
            raise RuntimeError('Sign in to ChatGPT in the independent profile first')
        cursor = None
        while True:
            response = await self.rpc.call('model/list', {'cursor': cursor})
            self.models.extend(response['data'])
            cursor = response.get('nextCursor')
            if not cursor:
                break
        if not self.model:
            self.model = next((m['model'] for m in self.models if m.get('isDefault')), None)
        if not self.model or not any(m['model'] == self.model for m in self.models):
            raise RuntimeError('Configured model is not available')
        modes = await self.rpc.call('collaborationMode/list', {})
        if not {'plan', 'default'} <= {m['mode'] for m in modes['data']}:
            raise RuntimeError('Native Plan and Default are required')
        if self.thread and resume_thread:
            await self.resume(self.thread,recovering=True)
        if resume_thread:
            self.recovery.restore()

    async def checked_thread(self, tid):
        data = (await self.rpc.call('thread/read', {'threadId': tid}))['thread']
        if Path(data['cwd']).resolve() != self.workspace:
            raise ValueError('Thread workspace mismatch')
        return data

    async def resume(self, tid, *, recovering=False):
        from gateway.core.codex_wire import CodexAppServerError
        unstarted=(recovering and self.journal.get('unstarted_thread')==tid
                   and not self.journal.db.execute('SELECT 1 FROM jobs WHERE thread=?',(tid,)).fetchone())
        missing=False
        try:
            data = await self.checked_thread(tid)
        except CodexAppServerError as error:
            if not (unstarted and error.code==-32600 and error.message=='thread not loaded: '+tid):raise
            missing=True
            data={'preview':'','status':{'type':'notLoaded'}}
        if (data.get('status') or {}).get('type') == 'active':
            raise ValueError('Thread has an active turn; cannot take over')
        if (unstarted and data.get('preview')==''
                and (missing or (data.get('path') and not Path(data['path']).is_file()))):
            # Native Codex has no resumable rollout until the first input. Keep its metadata,
            # and let the next authorized input create a thread instead of exhausting recovery.
            self.thread,self.turn=None,None
            self.unknown=False
            self.journal.set('thread',None)
            self.journal.set('unstarted_thread',None)
            return
        await self.load_shared_pins(self.journal.get('skill_pins:'+tid, []))
        resumed = await self.rpc.call('thread/resume', {'threadId': tid, 'cwd': str(self.workspace),
            'approvalPolicy': 'on-request', 'sandbox': self.permission_mode})
        self.check_permissions(resumed)
        self.thread, self.turn = tid, None
        self.unknown = bool(self.journal.db.execute("SELECT 1 FROM jobs WHERE thread=? AND state='unknown'",
                                                   (tid,)).fetchone())
        self.journal.set('thread', tid)
        await self.sync_persona(force=True)

    async def new(self):
        from gateway.core.personal_memory import TOOL as MEMORY_TOOL
        pins = self.shared_skills.store.pin() if self.shared_skills and self.shared_skills.enabled else []
        await self.load_shared_pins(pins)
        params = {'cwd': str(self.workspace), 'model': self.model,
                  'approvalPolicy': 'on-request', 'sandbox': self.permission_mode,
                  'dynamicTools': [MEMORY_TOOL] if self.interactive else []}
        if self.schedule_ui:
            from gateway.scheduling.schedules import TOOL
            params['dynamicTools'].append(TOOL)
        if self.shared_skills and self.shared_skills.enabled and self.interactive:
            from gateway.core.autonomy_tools import SKILL_TOOL
            params['dynamicTools'].append(SKILL_TOOL)
        if self.self_help and self.interactive:
            from gateway.core.self_help import TOOL as SELF_HELP_TOOL
            params['dynamicTools'].append(SELF_HELP_TOOL)
        if self.shared_policy is not None:
            self.persona_revision = self.shared_policy.active_persona()
            params['developerInstructions'] = self.persona_revision['body']
        if self.capability_tools is not None:
            from gateway.core.capabilities import TOOL as CAPABILITY_TOOL
            params['dynamicTools'].append(CAPABILITY_TOOL)
        data = await self.rpc.call('thread/start', params)
        self.check_permissions(data)
        self.thread = data['thread']['id']
        self.turn = None
        self.journal.set('thread', self.thread)
        self.journal.set('skill_pins:'+self.thread, pins)
        self.journal.set('unstarted_thread',self.thread)
        if self.persona_revision is not None:
            self.journal.set('persona_version:'+self.thread, self.persona_revision['version'])
        self.unknown = False
        if self.schedule_ui:
            self.journal.set('schedule:' + self.thread, True)

    async def load_shared_pins(self, pins):
        if not self.shared_skills or not self.shared_skills.enabled:
            return
        from gateway.core.managed_skills import extra_roots
        roots = extra_roots()
        for pin in pins:
            # Resolve IDs through the registry and hashes; never trust a journal path.
            path = self.shared_skills.store._verify(pin['skill_id'],pin['version'])
            roots.append(str(path.parent.parent))
        await self.rpc.call('skills/extraRoots/set',{'extraRoots':roots})

    def check_permissions(self,response):
        expected={'workspace-write':'workspaceWrite','danger-full-access':'dangerFullAccess'}[self.permission_mode]
        if response.get('sandbox', {}).get('type') != expected:
            raise RuntimeError('Codex did not grant the configured workspace permissions')

    async def start(self, inputs, recovery_model=None):
        self.require_member()
        if getattr(self,'background_faults',None):
            return await self.tg.say('本人的背景服務異常，新的工作尚未啟動；請由維護端核對。其他人的工作不受影響。')
        if self.initializing:return await self.tg.say('本人 worker 正在恢復，這則工作尚未提交；請稍後重送。')
        if self.busy or self.pending:
            await self.tg.say('工作進行中，未排入新工作；請用 /steer 或稍後重送。')
            return
        if self.unknown or self.rpc.failure:
            await self.tg.say('上次執行狀態不明；先 /result 查詢，再以 /new 明確開始新工作。')
            return
        try:
            self.acquire_execution()
        except RuntimeError:
            return await self.tg.say('你的排程工作正在執行，這則訊息尚未排入；請稍後再送。可用 /cron 查看排程。')
        try:
            await self.sync_persona()
        except Exception:
            self.release_execution()
            await self.tg.say('共用指引待同步；尚未啟動本回合，請稍後重試。')
            return
        self.busy = True
        if recovery_model is None:
            from gateway.telegram.transport import SOURCE_MESSAGE
            self.journal.set('active_reply', SOURCE_MESSAGE.get())
        self.stopping = False
        self.stop_key = secrets.token_hex(8)
        if recovery_model is None:
            self.recovery.clear()
        self.active_model = recovery_model or self.model
        try:
            if not self.thread:
                await self.new()
            self.journal.job(self.thread, None, 'dispatching')
            self.journal.set('unstarted_thread',None)
            if self.selected_skill:
                inputs = [*inputs, self.selected_skill]
            params = {'threadId': self.thread, 'input': inputs, 'cwd': str(self.workspace),
                      'model': self.active_model, 'approvalPolicy': 'on-request',
                      'collaborationMode': {'mode': self.mode, 'settings': {
                          'model': self.active_model, 'reasoning_effort': self.effort,
                          'developer_instructions': self.instructions() + ('\n'+RECOVERY_PROMPT if recovery_model else '')}}}
            if self.effort:
                params['effort'] = self.effort
            self.turn = None
            response = await self.rpc.call('turn/start', params)
            self.selected_skill = None
            # A terminal event can arrive before this RPC response is consumed.
            if self.busy:
                self.turn = response['turn']['id']
                self.bind_reply(self.turn)
                self.journal.job(self.thread, self.turn, 'running')
                if self.stopping:
                    await self.rpc.call('turn/interrupt', {'threadId':self.thread,'turnId':self.turn})
        except Exception:
            self.busy = False
            self.unknown = True
            if self.thread:
                self.journal.job(self.thread, self.turn, 'unknown')
            raise

    def instructions(self):
        from gateway.core.tool_runtime import load_instructions
        mode='前景互動' if self.interactive else '排程（不提供瀏覽器與記憶修改工具）'
        return (load_instructions(self.personal_instructions) + '\n目前執行模式：' + mode
                + '\n本人目前明確保存的偏好：\n' + self.memory.context())

    async def steer(self, inputs, expected=None):
        self.require_member()
        if self.recovery.waiting:
            # Preserve the recovery budget; new instructions are not a queued job.
            return await self.tg.say('目前正在等待自動接續，這則補充尚未插入。可等接續開始後再補充，或 /stop 取消後傳送新指示。')
        if self.stopping:
            return await self.tg.say('正在停止目前工作；這則補充尚未執行，請等停止完成後重送。')
        thread, turn = expected or (self.thread, self.turn)
        if not self.busy or not turn or (thread, turn) != (self.thread, self.turn):
            return await self.tg.say('原工作已結束或尚未開始，這則補充沒有另開工作；請重新傳送。')
        if self.pending:
            return await self.tg.say('目前正在等你的確認或回答；請先使用按鈕或 /answer，也可以 /stop。這則補充尚未插入。')
        try:
            await self.rpc.call('turn/steer', {'threadId': thread, 'expectedTurnId': turn, 'input': inputs})
        except Exception:
            return await self.tg.say('無法確認這則補充是否已插入；沒有另開或重跑工作，請先查看目前結果。')
        await self.tg.say('已補充到目前工作。')

    async def handle(self, text):
        self.require_member()
        if self.initializing and (text.split() or [''])[0] not in ('/status','/stop'):
            return await self.tg.say('本人 worker 正在恢復，這則操作尚未提交；請稍後重送。')
        if not text.startswith('/') and self.busy:
            return await self.steer([{'type':'text','text':text}])
        command = text.split(' ', 1)[0].split('@')[0].lower()
        if command in {'/stop', '/steer', '/status', '/answer', '/trust'}:
            return await self._handle(text)
        if self.commands.locked():
            return await self.tg.say('上一個操作尚未完成，這則尚未接收。')
        async with self.commands:
            return await self._handle(text)

    async def _handle(self, text):
        if not text.startswith('/'):
            return await self.start([{'type': 'text', 'text': text}])
        cmd, _, arg = text.partition(' ')
        cmd, arg = cmd.split('@')[0].lower(), arg.strip()
        if cmd == '/trust':
            from gateway.mcp.mcp_consent import configured
            servers=configured(self.personal_instructions)
            saved=self.journal.get('mcp_trust',{})
            parts=arg.split()
            if parts==['audit']:
                rows=self.journal.db.execute('SELECT at,kind,detail FROM audit ORDER BY id DESC LIMIT 15').fetchall()
                from gateway.scheduling.schedules import local_time
                return await self.tg.say('本人授權紀錄：\n'+'\n'.join(local_time(r[0])+' '+r[1]+' '+r[2] for r in rows))
            if not parts:
                return await self.tg.say('本人 MCP 信任設定：\n'+'\n'.join(
                    name+'：'+('已信任' if saved.get(name)==fp else '逐次詢問') for name,fp in sorted(servers.items()))+
                    '\n/trust 服務名稱：信任該服務全部操作（含修改、刪除）\n/trust revoke 服務名稱：撤銷信任\n登入與資料表單仍需本人處理；信任不代表授權未要求的工作。')
            revoke=len(parts)==2 and parts[0]=='revoke'
            name=parts[-1]
            if (not revoke and len(parts)!=1) or name not in servers and not (revoke and name in saved):
                return await self.tg.say('請使用 /trust 列出的服務名稱；撤銷用 /trust revoke 服務名稱。')
            if revoke:saved.pop(name,None)
            else:saved[name]=servers[name]
            self.journal.set('mcp_trust',saved)
            self.journal.audit('trust_revoked' if revoke else 'trust_granted',{'server':name,'via':'command'})
            if not revoke:self.approve_trusted_pending()
            return await self.tg.say(('已撤銷信任：' if revoke else '已信任全部操作（含修改、刪除）：')+name+'。僅對本人有效；已送出的操作不受撤銷影響。')
        if cmd == '/secrets':
            if arg:
                return await self.tg.say('請勿在聊天貼憑證。請透過部署管理者提供的本機方式管理憑證。/secrets 只列出本人憑證名稱。')
            from gateway.mcp.vault import Vault
            rows=Vault(self.tg.owner).listing()
            return await self.tg.say('本人的憑證（不顯示內容）：\n'+('\n'.join(r['name']+(' — '+r['purpose'] if r['purpose'] else '') for r in rows) or '目前沒有新增憑證。')+'\n外部帳號登入由各工具管理。')
        if cmd == '/memory':
            if self.busy or self.pending:
                return await self.tg.say('請先完成或停止目前工作，再修改或查看記憶。')
            return await self.memory.command(self, arg)
        if cmd == '/cron' and self.schedule_ui:
            if not arg:
                return await self.schedule_ui.listing()
            if arg.startswith(('time ','name ')):
                action,_,value=arg.partition(' ')
                value=value.removeprefix('windows-media-transform ').strip()
                row=await self.schedule_ui.media.read()
                if not row:raise ValueError('No owned media task')
                spec=json.loads(row['spec'])
                if action=='name':spec['name']=value
                else:spec['schedule']['time']=value
                return await self.schedule_ui.tool({'action':'update','task':'windows-media-transform','spec':spec})
            if arg == 'runs':
                from gateway.scheduling.schedules import local_time
                rows = self.schedule_ui.store.runs()
                labels = {'queued':'等待執行','running':'執行中','completed':'完成','failed':'未完成','blocked':'待本人處理','unknown':'結果不明','missed':'逾期跳過','cancelled':'已取消','interrupted':'已停止'}
                return await self.tg.say('\n\n'.join(local_time(r['due'])+' · '+labels.get(r['state'],'待核對')+'\n'+(r['result'] or '尚無結果')[:1500]+ ('\n通知是否送達不明，未自動重送。' if r['notification']=='unknown' else '') for r in rows[:5]) or '尚無執行紀錄。')
            if self.thread and not self.journal.get('schedule:' + self.thread):
                return await self.tg.say('這是更新前的對話，請先 /new，再提出排程需求。原對話仍保留。')
            return await self.start([{'type':'text','text':'請使用 gateway_schedule 管理本人的排程：'+arg}])
        if cmd in ('/start', '/help'):
            return await self.tg.say('使用者：' + getattr(self.tg, 'display_name', '使用者') + '\n' + HELP)
        if cmd == '/status':
            held=self.journal.db.execute("SELECT count(*) FROM updates WHERE state='held-maintenance'").fetchone()[0]
            state = '連線中斷' if self.rpc.failure else ('需要確認上次結果' if self.unknown else
                ('等待你的確認或回答' if self.pending else ('工作進行中' if self.busy else '可以開始工作')))
            if getattr(self,'background_faults',None):state='背景服務異常，待維護（'+', '.join(self.background_faults)+'）'
            message = ('目前狀態：' + state + '\n模式：' + ('先討論計劃' if self.mode=='plan' else '一般工作')
                + '\n模型：' + str(self.active_model if self.busy and self.active_model else self.model) + '\n思考程度：' + (self.effort or '預設')
                + ('\n正在等待自動恢復；新工作預設仍為 '+str(self.model) if self.recovery.waiting else '')
                + ('\nTelegram 連線暫時不穩定。' if self.tg.health() != 'ready' else ''))
            if held:
                message+='\n維護前的 '+str(held)+' 則訊息已保留、未執行；仍需處理的工作請重新傳送。'
            if self.busy and self.stop_key:
                return await self.tg.buttons(message, [[('停止目前工作','stop:'+self.stop_key)]])
            return await self.tg.say(message)
        if cmd == '/stop':
            if self.recovery.waiting:
                self.recovery.clear()
                self.busy=False
                self.stopping=True
                self.release_execution()
                return await self.tg.say('已取消自動接續。已送至外部服務的任務不會因此取消。')
            self.recovery.clear()
            if self.busy and not self.turn:
                self.stopping=True
                return await self.tg.say('正在取消啟動中的工作；收到工作識別後會立即中止，不再自動接續。')
            if not self.turn or not self.busy:
                return await self.tg.say('沒有已確認的執行中 turn。')
            self.stopping = True
            try:
                await self.rpc.call('turn/interrupt', {'threadId': self.thread, 'turnId': self.turn})
            except Exception:
                return await self.tg.say('停止尚未確認，可能工作剛完成或仍在啟動；請用 /status 核對，未自動重試或另開工作。')
            return await self.tg.say('已送出停止請求，正在等待 Codex 確認。已送出的表單或列印不會因此撤回。')
        if cmd == '/steer':
            if not arg or not self.busy or not self.turn:
                raise ValueError('No active turn or empty steering input')
            return await self.steer([{'type':'text','text':arg}])
        if cmd == '/answer':
            return await self.answer(arg)
        if cmd == '/get':
            path, name = await asyncio.to_thread(snapshot_file, self.workspace, arg, self.tg.inbox.parent / 'outbox')
            return await self.tg.document(path, name)
        if cmd == '/result':
            if not self.thread:
                raise ValueError('No current thread')
            if self.busy:
                return await self.tg.say('工作仍在進行，完成後再取回結果；目前可用 /status。')
            await self.checked_thread(self.thread)
            return await self.deliver_result(self.thread, None, manual=True)
        if self.busy or self.pending:
            return await self.tg.say('請先完成或 /stop 目前工作，再變更設定或對話。')
        if cmd == '/new':
            await self.new()
            return await self.tg.say('新對話：' + self.thread)
        if cmd == '/use':
            await self.resume(arg)
            return await self.tg.say('已切換：' + self.thread)
        if cmd == '/sessions':
            response = await self.rpc.call('thread/list', {'cwd': str(self.workspace), 'limit': 30, 'cursor': arg or None})
            rows = [t for t in response['data'] if Path(t['cwd']).resolve() == self.workspace]
            listing = '\n'.join(t['id'] + ' ' + (t.get('name') or '') for t in rows) or '尚無對話。'
            if response.get('nextCursor'):
                listing += '\n下一頁：/sessions ' + response['nextCursor']
            return await self.tg.say(listing)
        if cmd == '/compact':
            if not self.thread or self.unknown:
                raise ValueError('No known thread to compact')
            self.acquire_execution()
            self.busy = True
            self.journal.job(self.thread, None, 'compacting')
            try:
                await self.rpc.call('thread/compact/start', {'threadId': self.thread})
                self.journal.set('persona_version:'+self.thread, None)
            except Exception:
                self.busy, self.unknown = False, True
                self.journal.job(self.thread, self.turn, 'unknown')
                raise
            return await self.tg.say('已請求原生壓縮，等待完成。')
        if cmd in ('/plan', '/default'):
            self.mode = cmd[1:]
            self.journal.set('mode', self.mode)
            return await self.tg.say('下一回合使用原生 ' + self.mode + ' 模式。')
        if cmd == '/model':
            if not arg:
                return await self.tg.say('\n'.join(m['model'] for m in self.models))
            if not any(m['model'] == arg for m in self.models):
                raise ValueError('Unknown model')
            self.model, self.effort = arg, None
            self.journal.set('model', arg)
            self.journal.set('effort', None)
            return await self.tg.say('下一回合模型：' + arg)
        if cmd == '/effort':
            model = next(m for m in self.models if m['model'] == self.model)
            allowed = [e['reasoningEffort'] for e in model.get('supportedReasoningEfforts', [])]
            if not arg:
                return await self.tg.say('可選：' + ', '.join(allowed))
            if arg not in allowed:
                raise ValueError('Unsupported effort')
            self.effort = arg
            self.journal.set('effort', arg)
            return await self.tg.say('下一回合 effort：' + arg)
        if cmd == '/skills':
            response = await self.rpc.call('skills/list', {'cwds': [str(self.workspace)], 'forceReload': True})
            self.skill_choices.clear()
            rows, paths = [], set()
            for entry in response['data']:
                for skill in entry.get('skills', []):
                    if skill['path'] in paths or skill.get('enabled') is False:
                        continue
                    paths.add(skill['path'])
                    import hashlib
                    key = hashlib.sha256(skill['path'].encode()).hexdigest()[:24]
                    self.skill_choices[key] = {'input':{'type':'skill','name':skill['name'],'path':skill['path']},
                                               'expires':time.monotonic()+600}
                    rows.append([(skill['name']+' · '+skill['path'], 'sk:'+key)])
            if not rows:
                return await self.tg.say('沒有可用技能。')
            for offset in range(0,len(rows),40):
                await self.tg.buttons('選擇技能，再輸入你要做的事：',rows[offset:offset+40])
            return
        return await self.tg.say('未知指令。' + HELP)

    async def events(self):
        while not self.closed:
            if self.rpc.failure:
                self.unknown, self.busy = True, False
                if self.thread:
                    self.journal.job(self.thread, self.turn, 'unknown')
                self.pending.clear()
                return
            for _ in range(100):
                event = self.rpc.take_notification()
                if event is None:
                    break
                p, method = event.get('params', {}), event.get('method')
                if method == 'serverRequest/resolved':
                    rid = p.get('requestId')
                    self.pending = {k: v for k, v in self.pending.items() if v['request']['id'] != rid}
                    continue
                if p.get('threadId') != self.thread:
                    continue
                if method == 'turn/started':
                    self.turn = p['turn']['id']
                    self.bind_reply(self.turn)
                    self.busy = True
                elif method == 'item/started':
                    self.queue_tool_progress(p)
                elif method == 'item/completed':
                    self.queue_progress(p)
                elif method == 'turn/completed':
                    turn = p['turn']
                    if self.recovery.state and self.recovery.state['failed_turn']==turn['id']:
                        continue
                    self.turn, self.busy, self.unknown = turn['id'], False, False
                    self.journal.job(self.thread, self.turn, turn['status'])
                    self.pending.clear()
                    if self.recovery.schedule(turn):
                        continue
                    self.stopping = False
                    self.release_execution()
                    try:
                        self.outbox.put_nowait(('result', self.thread, self.turn, turn['status'], turn.get('error')))
                    except asyncio.QueueFull:
                        self.journal.delivery(self.thread + ':' + self.turn, 'unknown')
                        self.unknown = True
            for _ in range(100):
                request = self.rpc.take_server_request()
                if request is None:
                    break
                await self.server_request(request)
            for key, pending in list(self.pending.items()):
                if time.monotonic() > pending['expires']:
                    self.rpc.respond_error(pending['request']['id'], -32000, 'User response expired')
                    del self.pending[key]
            if not self.unknown:
                await self.recovery.tick()
            await asyncio.sleep(.03)

    def bind_reply(self, turn):
        if self.interactive:
            self.journal.set('reply:'+self.thread+':'+turn,self.journal.get('active_reply'))

    def queue_tool_progress(self, params):
        item=params.get('item',{})
        labels={'mcpToolCall':('⚙️','正在使用工具處理工作。'),
                'commandExecution':('⚙️','正在執行工作步驟。'),
                'fileChange':('⚙️','正在更新工作檔案。'),
                'webSearch':('🔎','正在搜尋資料。')}
        if item.get('type') not in labels:return
        icon,text=labels[item['type']]
        self.queue_progress({**params,'item':{'type':'agentMessage','phase':'commentary',
            'id':item.get('id'),'text':text}},icon=icon)

    def queue_progress(self, params, icon="💭"):
        item = params.get('item', {})
        if (not self.interactive or not self.busy or params.get('threadId') != self.thread or
                params.get('turnId') != self.turn or item.get('type') != 'agentMessage' or
                item.get('phase') != 'commentary' or not isinstance(item.get('id'), str) or
                not item['id'] or not isinstance(item.get('text'), str) or not item['text'].strip()):
            return
        turn = (self.thread, self.turn)
        if self.progress_turn != turn:
            self.progress_turn, self.progress_last, self.progress_count = turn, 0, 0
        now = time.monotonic()
        if (self.progress_count >= 6 or self.outbox.qsize() >= 8 or
                (self.progress_count and now - self.progress_last < 8)):
            return
        key = 'progress:' + ':'.join((*turn, item['id']))
        if self.journal.db.execute('SELECT 1 FROM delivery WHERE key=?', (key,)).fetchone():
            return
        # Reserve before enqueue; ambiguous sends and restart recovery never replay progress.
        self.journal.delivery(key, 'pending')
        self.outbox.put_nowait(('progress', key, *turn, icon+' '+item['text'].strip()[:1500]))
        self.progress_last, self.progress_count = now, self.progress_count + 1

    async def send_progress(self, key, thread, turn, text):
        if not self.busy or (thread, turn) != (self.thread, self.turn):
            self.journal.delivery(key, 'skipped')
            return
        try:
            receipts = await asyncio.wait_for(self.tg.say(text), timeout=8)
            self.journal.delivery(key, 'delivered', receipts)
        except Exception:
            self.journal.delivery(key, 'unknown')

    async def sender(self):
        while not self.closed:
            job = await self.outbox.get()
            while not self.closed:
                try:
                    self.require_member(); break
                except PermissionError: await asyncio.sleep(1)
            if self.closed: return
            try:
                if job[0] == 'result':
                    await self.deliver_result(job[1], job[2], status=job[3], error=job[4])
                elif job[0] == 'progress':
                    await self.send_progress(*job[1:])
                elif job[0] == 'prompt':
                    await self.show_request(job[1])
                elif job[0] == 'notice':
                    await self.tg.say(job[1])
            except Exception:
                if job[0] == 'result':
                    self.journal.delivery(job[1] + ':' + job[2], 'unknown')
                elif job[1] in self.pending:
                    p = self.pending.pop(job[1])
                    self.rpc.respond_error(p['request']['id'], -32000, 'Prompt delivery failed')
            finally:
                self.outbox.task_done()

    async def deliver_result(self, thread, turn, manual=False, status=None, error=None):
        if not turn:
            response = await self.rpc.call('thread/turns/list', {'threadId': thread, 'limit': 1, 'itemsView': 'notLoaded'})
            if not response['data']:
                return await self.tg.say('尚無結果。')
            latest = response['data'][0]
            turn, status = latest['id'], latest['status']
            error = latest.get('error')
        key = thread + ':' + turn
        reply = self.journal.get('reply:'+key) if self.interactive else None
        async def answer(text):
            return await self.tg.say(text,reply_to=reply) if reply else await self.tg.say(text)
        if not manual and self.journal.delivered(key):
            return
        self.journal.delivery(key, 'pending')
        receipts, cursor, had_answer = [], None, False
        final_texts = []
        if manual:
            receipts.extend(await self.tg.say('手動取回原生紀錄，可能與先前收到的內容重複。'))
        while True:
            response = await self.rpc.call('thread/items/list', {'threadId': thread, 'turnId': turn,
                'limit': 100, 'cursor': cursor})
            for entry in response['data']:
                item = entry.get('item', entry)
                if item.get('type') == 'agentMessage' and item.get('text'):
                    if item.get('phase') == 'commentary' and (not manual or status == 'failed'):
                        continue
                    receipts.extend(await answer(item['text']))
                    had_answer = True
                    self.journal.delivery(key, 'pending', receipts)
                    if item.get('phase') != 'commentary':
                        final_texts.append(item['text'])
            cursor = response.get('nextCursor')
            if not cursor:
                break
        if status == 'completed' and not manual and self.interactive:
            from gateway.telegram.artifacts import deliver
            await deliver(self, '\n'.join(final_texts), key)
        if status == 'failed':
            from gateway.telegram.ui import failure_text
            receipts.extend(await answer(failure_text(error)))
        elif status == 'interrupted':
            receipts.extend(await answer('工作已停止。'))
        elif status not in {'completed'}:
            receipts.extend(await answer('目前無法確認工作是否完成，沒有自動重跑。'))
        elif not had_answer:
            receipts.extend(await answer('這次沒有文字回答，可用 /status 查看目前狀態。'))
        self.journal.delivery(key, 'delivered' if status in {'completed','failed','interrupted'} else 'unknown', receipts)

    async def server_request(self, request):
        self.require_member()
        p, method = request.get('params', {}), request['method']
        if method == 'item/tool/call' and p.get('tool') == 'gateway_capability':
            if not self.capability_tools or p.get('threadId') != self.thread or p.get('turnId') != self.turn or not self.busy:
                self.rpc.respond_error(request['id'],-32601,'Unavailable or stale capability call'); return
            try:
                result=await self.capability_tools.apply(p['arguments'],'native:'+str(self.thread)+':'+str(self.turn)+':'+str(request['id']))
                success=True
            except Exception as error:
                result={'state':'blocked','reason':type(error).__name__};success=False
            self.rpc.respond(request['id'],{'success':success,'contentItems':[{'type':'inputText','text':json.dumps(result,ensure_ascii=False)}]})
            return

        if method == 'item/tool/call' and p.get('tool') == 'gateway_self_help':
            if (not self.self_help or p.get('threadId') != self.thread or
                    p.get('turnId') != self.turn or not self.busy):
                self.rpc.respond_error(request['id'],-32601,'Unavailable or stale self-help call')
                return
            try:
                result=await self.self_help.apply(p['arguments']);success=True
            except Exception as error:
                result=type(error).__name__+': self-help did not complete';success=False
            self.rpc.respond(request['id'],{'success':success,'contentItems':[
                {'type':'inputText','text':json.dumps(result,ensure_ascii=False)}]})
            return
        if method == 'item/tool/call' and p.get('tool') == 'gateway_shared_skill':
            if (not self.shared_skills or p.get('threadId') != self.thread or
                    p.get('turnId') != self.turn or not self.busy):
                self.rpc.respond_error(request['id'], -32601, 'Unavailable or stale skill call')
                return
            try:
                result = await self.shared_skills.apply(p['arguments'])
                success = True
            except Exception as error:
                result, success = type(error).__name__+': skill operation rejected', False
            self.rpc.respond(request['id'], {'success':success,'contentItems':[
                {'type':'inputText','text':json.dumps(result,ensure_ascii=False)}]})
            return
        if method == 'item/tool/call' and p.get('tool') == 'gateway_memory':
            if p.get('threadId') != self.thread or p.get('turnId') != self.turn or not self.busy:
                self.rpc.respond_error(request['id'], -32601, 'Stale memory call')
                return
            try:
                result, success = self.memory.apply(p['arguments']), True
            except (ValueError, KeyError, TypeError) as error:
                result, success = str(error), False
            self.rpc.respond(request['id'], {'success':success, 'contentItems':[{'type':'inputText','text':json.dumps(result,ensure_ascii=False)}]})
            return
        if method == 'item/tool/call' and p.get('tool') == 'gateway_schedule' and self.schedule_ui:
            if p.get('threadId') != self.thread or p.get('turnId') != self.turn:
                self.rpc.respond_error(request['id'], -32601, 'Stale schedule call')
                return
            try:
                result = await self.schedule_ui.tool(p['arguments'])
                success = True
            except (ValueError, KeyError, TypeError) as error:
                result, success = str(error), False
            except Exception:
                result, success = '排程工具或確認訊息未完成；請用 /cron 檢查，不要宣稱已啟用。', False
            self.rpc.respond(request['id'], {'success':success,
                'contentItems':[{'type':'inputText','text':json.dumps(result,ensure_ascii=False)}]})
            return
        supported = {'item/commandExecution/requestApproval', 'item/fileChange/requestApproval',
                     'item/permissions/requestApproval', 'item/tool/requestUserInput', 'mcpServer/elicitation/request'}
        if self.recovery.waiting:
            self.rpc.respond_error(request['id'], -32601, 'Turn already ended; recovery pending')
            return
        if method == 'mcpServer/elicitation/request' and p.get('turnId') is None and p.get('threadId') == self.thread and self.busy and self.turn:
            # MCP may omit turnId; bind consent to this active turn so later buttons expire.
            p = dict(p, turnId=self.turn)
            request = dict(request, params=p)
        if method not in supported or p.get('threadId') != self.thread or p.get('turnId') != self.turn:
            self.rpc.respond_error(request['id'], -32601, 'Unsupported or stale server request')
            return
        if method == 'mcpServer/elicitation/request':
            from gateway.mcp.mcp_consent import choices
            try:choices(p)
            except ValueError:
                self.rpc.respond(request['id'],{'action':'decline'})
                await self.tg.say('外部工具要求目前不支援的表單或登入流程，尚未執行；這不代表你的帳號只有讀取權。請提供服務名稱供維護檢查，不要貼帳密。')
                return
        if method == 'mcpServer/elicitation/request' and self.auto_consent(request):
            return
        if len(self.pending) >= 16:
            self.rpc.respond_error(request['id'], -32000, 'Too many pending requests')
            return
        self.journal.audit('approval_requested',{'method':method,'server':p.get('serverName'),'thread':self.thread,'turn':self.turn})
        key = secrets.token_hex(8)
        self.pending[key] = {'request': request, 'expires': time.monotonic() + 600,
                             'choices': [], 'answers': {}}
        try:
            self.outbox.put_nowait(('prompt', key))
        except asyncio.QueueFull:
            self.pending.pop(key)
            self.rpc.respond_error(request['id'], -32000, 'Delivery backlog')

    def approve_trusted_pending(self):
        for key,pending in list(self.pending.items()):
            if time.monotonic()<pending['expires'] and self.auto_consent(pending['request']):
                self.pending.pop(key,None)

    def auto_consent(self,request):
        p=request.get('params',{})
        if (request.get('method')!='mcpServer/elicitation/request' or not self.busy or not self.turn or
            p.get('threadId')!=self.thread or p.get('turnId') not in (None,self.turn)):
            return False
        from gateway.mcp.mcp_consent import choices,trusted
        try:decision=choices(p)[0][1]
        except ValueError:return False
        if not trusted(self,p):return False
        self.journal.audit('consent_auto',{'server':p['serverName'],'thread':self.thread,'turn':self.turn})
        self.rpc.respond(request['id'],decision)
        return True

    async def show_request(self, key):
        if key not in self.pending:
            return
        pending = self.pending[key]
        request = pending['request']
        p, method = request['params'], request['method']
        if method == 'item/tool/requestUserInput':
            questions = p['questions']
            if not 1 <= len(questions) <= 3:
                raise ValueError('Invalid question count')
            for i, q in enumerate(questions):
                await self.tg.say(q['question'] + '\n' + '\n'.join(
                    o['label'] + ': ' + o.get('description', '') for o in q.get('options') or [])
                    + f'\n回覆：/answer {key} {i + 1} 你的回答')
            return
        details = approval_details(p)
        if len(details) > 20000:
            raise ValueError('Approval too large to review')
        if method == 'mcpServer/elicitation/request':
            from gateway.mcp.mcp_consent import choices as consent_choices
            pending['choices']=consent_choices(p)
            from gateway.mcp.mcp_consent import configured
            fingerprint=configured(self.personal_instructions).get(p['serverName'])
            if fingerprint:
                pending['trust_fingerprint']=fingerprint
                pending['choices'].append(('信任此服務全部操作（含修改、刪除）',pending['choices'][0][1]))
                pending['trust_index']=len(pending['choices'])-1
            rows=[[(label,f'cg:{key}:{i}')] for i,(label,_) in enumerate(pending['choices'])]
            return await self.tg.buttons('外部工具操作確認：'+p['serverName']+'\n'+str(p.get('message',''))[:2500]+'\n僅核對你剛才要求的操作；此確認 10 分鐘內有效。',rows)
        if method == 'item/permissions/requestApproval':
            choices = [('允許本回合指定權限', {'permissions': p['permissions'], 'scope': 'turn'}),
                       ('拒絕權限', {'permissions': {}, 'scope': 'turn'})]
        else:
            offered = p.get('availableDecisions')
            if offered is None:
                offered = ['accept', 'acceptForSession', 'decline', 'cancel']
            if not isinstance(offered, list):
                raise ValueError('Invalid approval choices')
            labels = {'accept': '允許一次', 'acceptForSession': '允許本 session',
                      'decline': '拒絕', 'cancel': '取消工作'}
            # Persistent policy amendments are deliberately not offered by this UI.
            choices = [(labels[d], {'decision': d}) for d in offered if isinstance(d, str) and d in labels]
        if not choices:
            raise ValueError('No supported approval choices')
        pending['choices'] = choices
        rows = [[(label, f'cg:{key}:{i}')] for i, (label, _) in enumerate(choices)]
        rows.append([('查看細節',f'cg:{key}:details')])
        await self.tg.buttons(approval_text(method,p,self.workspace), rows)

    def valid_pending(self, key):
        p = self.pending.get(key)
        if not p or time.monotonic() > p['expires']:
            raise ValueError('Expired request')
        params = p['request']['params']
        if params['threadId'] != self.thread or params['turnId'] != self.turn or not self.busy:
            raise ValueError('Request is no longer active')
        return p

    async def callback(self, query):
        self.require_member()
        try:
            if query.data.startswith('stop:'):
                if not self.busy or query.data[5:] != self.stop_key:
                    raise ValueError('Stale stop button')
                await query.answer()
                return await self.handle('/stop')
            if query.data.startswith('mem:'):
                if self.busy or self.commands.locked():
                    raise ValueError('Work busy')
                await query.answer()
                if query.data == 'mem:native':
                    return await self.handle('/memory auto')
                if query.data.startswith('mem:forget:'):
                    return await self.handle('/memory forget ' + query.data.split(':')[2])
                raise ValueError('Invalid memory action')
            if query.data.startswith('sm:') and self.schedule_ui:
                return await self.schedule_ui.callback(query)
            if query.data.startswith('sc:') and self.schedule_ui:
                _, key, decision = query.data.split(':')
                if decision not in ('yes','no'):
                    raise ValueError('Invalid schedule decision')
                result = await self.schedule_ui.confirm(key, decision == 'yes')
                await query.answer('已處理')
                await self.tg.say(result)
                return
            if query.data.startswith('sk:'):
                key = query.data[3:]
                skill = self.skill_choices.get(key)
                if self.busy or self.pending or not skill or time.monotonic()>skill['expires']:
                    raise ValueError('Skill selection expired or work busy')
                self.selected_skill = skill['input']
                self.skill_choices.clear()
                await query.answer('已選擇技能')
                await self.tg.say('已選擇「'+self.selected_skill['name']+'」，請輸入你要做的事。')
                return
            prefix, key, index = query.data.split(':')
            if prefix != 'cg':
                raise ValueError('Unknown callback')
            p = self.valid_pending(key)
            if index == 'details':
                await query.answer()
                await self.tg.say(approval_details(p['request']['params']))
                return
            i = int(index)
            if i < 0 or i >= len(p['choices']):
                raise ValueError('Invalid decision')
            if i==p.get('trust_index'):
                from gateway.mcp.mcp_consent import configured
                name=p['request']['params']['serverName']
                fingerprint=configured(self.personal_instructions).get(name)
                if not fingerprint or fingerprint!=p['trust_fingerprint']:raise ValueError('Service changed')
                saved=self.journal.get('mcp_trust',{});saved[name]=fingerprint
                self.journal.set('mcp_trust',saved)
                self.journal.audit('trust_granted',{'server':name,'via':'button'})
            self.journal.audit('approval_reply',{'method':p['request']['method'],'server':p['request']['params'].get('serverName'),'choice':i,'thread':self.thread,'turn':self.turn})
            self.pending.pop(key)
            self.rpc.respond(p['request']['id'], p['choices'][i][1])
            if i==p.get('trust_index'):self.approve_trusted_pending()
            await query.answer('已回覆 Codex')
        except (ValueError, KeyError, TypeError):
            await query.answer('此按鈕已失效', show_alert=True)

    async def answer(self, arg):
        key, number, text = arg.split(' ', 2)
        p = self.valid_pending(key)
        if p['request']['method'] != 'item/tool/requestUserInput' or not text.strip():
            raise ValueError('Not an active question')
        questions = p['request']['params']['questions']
        idx = int(number) - 1
        if not 0 <= idx < len(questions):
            raise ValueError('Invalid question number')
        q = questions[idx]
        options = q.get('options') or []
        if options and not q.get('isOther') and text not in [o['label'] for o in options]:
            raise ValueError('Choose one of the provided labels')
        p['answers'][q['id']] = {'answers': [text]}
        if len(p['answers']) == len(questions):
            self.pending.pop(key)
            self.rpc.respond(p['request']['id'], {'answers': p['answers']})
            await self.tg.say('已回覆 Codex 提問。')
        else:
            await self.tg.say('已記錄，請回答其餘題目。')
