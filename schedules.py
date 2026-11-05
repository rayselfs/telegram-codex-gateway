"""Owner-bound schedule definitions and durable occurrences; no model or Telegram credentials."""
import json
import re
import secrets
import sqlite3
import time
from datetime import datetime, timedelta, timezone

TAIPEI = timezone(timedelta(hours=8))


def next_time(schedule, after):
    kind = schedule.get('kind')
    if kind == 'interval':
        minutes = schedule.get('minutes')
        if type(minutes) is not int or not 5 <= minutes <= 525600:
            raise ValueError('間隔須為 5 至 525600 分鐘')
        if set(schedule) != {'kind', 'minutes'}:
            raise ValueError('間隔格式不符')
        return after + minutes * 60
    if kind == 'once':
        if set(schedule) != {'kind', 'at'}:
            raise ValueError('單次時間格式不符')
        dt = datetime.fromisoformat(schedule['at'])
        if dt.tzinfo is None or dt.utcoffset() != timedelta(hours=8):
            raise ValueError('單次時間需包含 +08:00')
        return dt.timestamp() if dt.timestamp() > after else None
    if kind not in ('daily', 'weekly') or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', str(schedule.get('time', ''))):
        raise ValueError('請使用每日或每週及 HH:MM 台灣時間')
    days = schedule.get('days', list(range(7)))
    if set(schedule) != ({'kind', 'time', 'days'} if kind == 'weekly' else {'kind', 'time'}):
        raise ValueError('排程格式不符')
    if not isinstance(days, list) or not days or any(type(d) is not int or d not in range(7) for d in days):
        raise ValueError('星期以 0=週一至 6=週日表示')
    dt = datetime.fromtimestamp(after, TAIPEI)
    hour, minute = map(int, schedule['time'].split(':'))
    for offset in range(8):
        candidate = (dt + timedelta(days=offset)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate.weekday() in days and candidate.timestamp() > after:
            return candidate.timestamp()


def deadline(run):
    spec=json.loads(run['spec'])
    return run['due']+60*spec.get('grace_minutes',15)


def validate(spec, now=None, allow_internal=False):
    if not isinstance(spec, dict):
        raise ValueError('排程格式不符')
    expected = {'name', 'kind', 'content', 'schedule'}
    if spec.get('kind') in ('line_message','line_dynamic'):expected.add('recipient')
    for optional in ('grace_minutes','preview'):
        if optional in spec:expected.add(optional)
    grace=spec.get('grace_minutes',15)
    if type(grace) is not int or not 1<=grace<=120:raise ValueError('有效期限需為 1–120 分鐘')
    if 'preview' in spec and (spec.get('kind')!='line_dynamic' or type(spec['preview']) is not bool):raise ValueError('preview 僅供動態 LINE 預覽')
    if set(spec) != expected:
        raise ValueError('需要 name、kind、content、schedule，不能指定他人身分或路徑')
    for key, size in [('name', 80), ('content', 8000)]:
        if not isinstance(spec[key], str) or not spec[key].strip() or len(spec[key]) > size:
            raise ValueError('名稱或內容為空或過長')
    if spec['kind'] in ('line_message','line_dynamic'):
        recipient=spec['recipient']
        if not isinstance(recipient,str) or not recipient.strip() or len(recipient)>100 or any(ord(c)<32 for c in recipient):
            raise ValueError('LINE 需要完整群組名稱，最多 100 字且不可換行')
        if spec['kind']=='line_message' and len(spec['content'])>5000:
            raise ValueError('LINE 文字最多 5000 字')
    if spec['kind'] not in ('reminder', 'codex', 'line_message','line_dynamic') and not (allow_internal and spec['kind'] in {'transcript_delivery','m365_billing','media_failure'}):
        raise ValueError('工作須為 reminder、codex 、line_message 或 line_dynamic')
    if not isinstance(spec['schedule'], dict):
        raise ValueError('時間格式不符')
    due = next_time(spec['schedule'], time.time() if now is None else now)
    if due is None:
        raise ValueError('時間已過，請指定未來時間')
    return due


def local_time(stamp):
    return datetime.fromtimestamp(stamp, TAIPEI).strftime('%Y/%m/%d %H:%M') if stamp else '無'


class Schedules:
    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, spec TEXT NOT NULL,
            enabled INTEGER NOT NULL, next REAL, revision INTEGER NOT NULL, model TEXT, effort TEXT);
          CREATE TABLE IF NOT EXISTS proposals(id TEXT PRIMARY KEY, action TEXT, task TEXT,
            spec TEXT, revision INTEGER, expires REAL, model TEXT, effort TEXT);
          CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, task TEXT, due REAL, spec TEXT,
            state TEXT, result TEXT, notification TEXT DEFAULT 'ready', created REAL,
            model TEXT, effort TEXT, UNIQUE(task,due));
          CREATE TABLE IF NOT EXISTS attachments(key TEXT PRIMARY KEY, run TEXT, file TEXT,
            label TEXT, state TEXT NOT NULL, receipt TEXT);
          CREATE TABLE IF NOT EXISTS source_events(key TEXT PRIMARY KEY, run TEXT);
          CREATE TABLE IF NOT EXISTS line_prepared(run TEXT PRIMARY KEY, payload TEXT NOT NULL);
        ''')

    def close(self):
        self.db.close()

    def tasks(self):
        return [dict(r) for r in self.db.execute('SELECT * FROM tasks ORDER BY rowid')]

    def runs(self):
        return [dict(r) for r in self.db.execute('SELECT id,task,due,state,result,notification FROM runs ORDER BY created DESC LIMIT 15')]

    def propose(self, action, task=None, spec=None, model=None, effort=None):
        if action not in {'create', 'update', 'pause', 'resume', 'delete', 'run'}:
            raise ValueError('不支援的排程操作')
        old = self.db.execute('SELECT * FROM tasks WHERE id=?', (task,)).fetchone()
        if action != 'create' and not old:
            raise ValueError('找不到本人的任務')
        if action in ('create', 'update'):
            validate(spec)
        else:
            spec = json.loads(old['spec'])
        if action == 'resume':
            validate(spec, allow_internal=True)
        if action == 'create' and len(self.tasks()) >= 100:
            raise ValueError('任務數已達上限')
        key = secrets.token_hex(8)
        with self.db:
            self.db.execute('DELETE FROM proposals WHERE expires<?', (time.time(),))
            self.db.execute('INSERT INTO proposals VALUES(?,?,?,?,?,?,?,?)',
                (key, action, task, json.dumps(spec, ensure_ascii=False), old['revision'] if old else 0,
                 time.time()+900, model, effort))
        return key, spec

    def confirm(self, key, accept=True):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            p = self.db.execute('SELECT * FROM proposals WHERE id=?', (key,)).fetchone()
            if not p or p['expires'] < time.time():
                raise ValueError('確認已過期或已使用')
            self.db.execute('DELETE FROM proposals WHERE id=?', (key,))
            if not accept:
                self.db.commit()
                return '已取消，排程未變更。'
            old = self.db.execute('SELECT * FROM tasks WHERE id=?', (p['task'],)).fetchone()
            if p['action'] != 'create' and (not old or old['revision'] != p['revision']):
                raise ValueError('任務已變更，請重新確認')
            spec = json.loads(p['spec']); now = time.time(); action = p['action']
            tid = p['task'] or secrets.token_hex(6)
            due = None
            if action in ('create', 'update', 'resume'):
                due = validate(spec, now, allow_internal=action=='resume')
            if action == 'create':
                self.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?)', (tid,p['spec'],1,due,1,p['model'],p['effort']))
            elif action in ('update', 'resume', 'pause'):
                self.db.execute('UPDATE tasks SET spec=?,enabled=?,next=?,revision=revision+1,model=?,effort=? WHERE id=?',
                    (p['spec'],int(action!='pause'),due,p['model'] or old['model'],p['effort'] or old['effort'],tid))
            elif action == 'delete':
                self.db.execute('DELETE FROM tasks WHERE id=?', (tid,))
            elif action == 'run':
                self._insert_run(tid, now, p['spec'], old['model'], old['effort'])
                due = now
            if action in ('update', 'pause', 'delete'):
                self.db.execute("UPDATE runs SET state='cancelled',result='排程變更，取消尚未開始的工作' WHERE task=? AND state='queued'", (tid,))
            self.db.commit()
            return f'已套用：{spec["name"]}\n下次：{local_time(due)}（台灣時間）\n已開始的工作不會因暫停或刪除而中斷。'
        except Exception:
            self.db.rollback()
            raise

    def _insert_run(self, tid, due, spec, model, effort):
        self.db.execute('INSERT OR IGNORE INTO runs(id,task,due,spec,state,created,model,effort) VALUES(?,?,?,?,?,?,?,?)',
            (secrets.token_hex(12),tid,due,spec,'queued',time.time(),model,effort))

    def enqueue(self, now=None):
        now = time.time() if now is None else now
        self.db.execute('BEGIN IMMEDIATE')
        try:
            for t in self.db.execute('SELECT * FROM tasks WHERE enabled=1 AND next<=?', (now,)).fetchall():
                self._insert_run(t['id'],t['next'],t['spec'],t['model'],t['effort'])
                schedule = json.loads(t['spec'])['schedule']
                due = next_time(schedule, now)
                if schedule['kind'] == 'interval':
                    step = schedule['minutes'] * 60
                    due = t['next'] + (int((now-t['next'])//step)+1)*step
                self.db.execute('UPDATE tasks SET next=?,enabled=? WHERE id=?', (due,int(due is not None),t['id']))
            # Never replay a backlog after downtime; first release has a fixed 15-minute grace.
            for pending in self.db.execute("SELECT id,due,spec FROM runs WHERE state='queued'").fetchall():
                if now>deadline(pending):
                    self.db.execute("UPDATE runs SET state='missed',result='超過本人設定的有效期限，未補跑' WHERE id=?",(pending['id'],))
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def recover(self):
        # Called only under the per-profile worker OS lock, never by readers.
        with self.db:
            if self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='line_deliveries'").fetchone():
                rows=self.db.execute("SELECT r.id,r.task,d.state,d.receipt FROM runs r JOIN line_deliveries d ON d.run=r.id WHERE r.state='running'").fetchall()
                for row in rows:
                    if row['state']=='sent':
                        self.db.execute("UPDATE runs SET state='completed',result=? WHERE id=?",(row['receipt'],row['id']))
                    else:
                        self.db.execute("UPDATE line_deliveries SET state='unknown' WHERE run=?",(row['id'],))
                        self.db.execute('UPDATE tasks SET enabled=0,next=NULL WHERE id=?',(row['task'],))
                        self.db.execute("UPDATE runs SET state='cancelled',result='LINE 結果不明，暫停後續執行' WHERE task=? AND state='queued'",(row['task'],))
            self.db.execute("UPDATE runs SET state='unknown',result='上次程序中斷；未自動重跑，請核對結果' WHERE state='running'")

    def claim(self):
        self.db.execute('BEGIN IMMEDIATE')
        row = self.db.execute("SELECT * FROM runs WHERE state='queued' AND due<=? ORDER BY due LIMIT 1", (time.time(),)).fetchone()
        if row:
            self.db.execute("UPDATE runs SET state='running' WHERE id=?", (row['id'],))
        self.db.commit()
        return dict(row) if row else None

    def finish(self, rid, state, result):
        with self.db:
            self.db.execute('UPDATE runs SET state=?,result=? WHERE id=?', (state,result[:24000],rid))
            if state=='completed' and result.strip()=='[SILENT]':
                self.db.execute("UPDATE runs SET notification='suppressed' WHERE id=?",(rid,))


TOOL = {'type':'function','name':'ezra_schedule','description':
    'Manage only the current Telegram user schedules. Use for reminders, cron, scheduled tasks. Identify tasks by name in user-facing replies; never print internal task/run IDs. '
    'list/runs read existing tasks. create/update require the full spec; pause/resume/delete/run require task ID. '
    'Mutations are proposals: the user must confirm the Telegram button, never claim active before confirmation. '
    'Timezone Asia/Taipei. Daily/weekly HH:MM; weekday 0 Monday through 6 Sunday. interval minutes >=5; '
    'once at ISO8601 +08:00. reminder sends literal content, codex performs the saved instructions. line_message sends literal content to the exact LINE group name in recipient using the shared User A/User B LINE account; no attachments or generated text. line_dynamic first runs content as read/compose instructions using the current owner tools, then sends generated text to the fixed recipient. Skip only when a successful complete query finds no matching data; errors stop without sending. Confirm source, selection rules, format, recipient and time; never invent missing data source or format. Always show recipient/content/time for confirmation. '
    'For windows-media-transform (kind media_transform), list first; update name and/or daily schedule time, preserving kind/content. pause/resume/delete supported; run/create are not. It executes separately without the Codex timeout. '
    'No auto-retry. grace_minutes sets expiry after due time, 1-120 minutes, default 15. line_dynamic preview=true generates a preview only and never sends LINE; confirm this mode explicitly. Ask for missing time/content. External writes or printing must be explicitly scoped by user.',
    'inputSchema':{'type':'object','properties':{
        'action':{'type':'string','enum':['list','runs','create','update','pause','resume','delete','run']},
        'task':{'type':['string','null']},
        'spec':{'type':['object','null'],'properties':{
            'name':{'type':'string'},'kind':{'type':'string','enum':['reminder','codex','line_message','line_dynamic','media_transform']},
            'grace_minutes':{'type':'integer','minimum':1,'maximum':120},'preview':{'type':'boolean'},
            'content':{'type':'string'},'recipient':{'type':'string'},'schedule':{'type':'object','properties':{
                'kind':{'type':'string','enum':['daily','weekly','interval','once']},
                'time':{'type':'string'},'days':{'type':'array','items':{'type':'integer'}},
                'minutes':{'type':'integer'},'at':{'type':'string'}},'additionalProperties':False}},
            'required':['name','kind','content','schedule'],'additionalProperties':False}},
        'required':['action'],'additionalProperties':False}}


class ScheduleUI:
    def __init__(self, store, controller):
        self.store, self.c = store, controller
        from media_schedule import MediaSchedule
        self.media = MediaSchedule(store)

    async def confirm(self,key,accept):
        if key.startswith('wm'):
            return await self.media.confirm(key,accept)
        return self.store.confirm(key,accept)

    async def tasks(self):
        rows=self.store.tasks()
        media=await self.media.read()
        if media:rows.append(media)
        return rows

    async def tool(self, args):
        if not isinstance(args,dict) or set(args)-{'action','task','spec'}:
            raise ValueError('排程參數不符')
        action = args.get('action')
        if action == 'list':
            return await self.tasks()
        if args.get('task') == 'windows-media-transform':
            if action=='runs':return await self.media.recent()
            key,clock=await self.media.propose(action,args.get('spec'))
            name=self.store.db.execute('SELECT new_name FROM media_proposals WHERE id=?',(key,)).fetchone()[0]
            labels={'update':'修改','pause':'暫停','resume':'恢復','delete':'刪除排程'}
            await self.c.tg.buttons('確認'+labels[action]+'：'+name+'\n每天 '+clock+'（台灣時間）\n只管理排程；保留影片、腳本與設定。已開始的轉檔不會中斷。\n確認有效 15 分鐘。',
                [[('確認','sc:'+key+':yes'),('取消','sc:'+key+':no')]])
            return {'state':'awaiting_user_confirmation','name':name}
        if action == 'runs':
            return self.store.runs()
        key,spec = self.store.propose(action,args.get('task'),args.get('spec'),self.c.model,self.c.effort)
        due = next_time(spec['schedule'],time.time())
        if action == 'run':due = time.time()
        labels={'create':'新增','update':'修改','pause':'暫停','resume':'恢復','delete':'刪除','run':'立即執行'}
        text = f'確認{labels[action]}排程：{spec["name"]}\n使用者：{self.c.tg.display_name}\n類型：{spec["kind"]}\n時間規則：{describe(spec["schedule"])}\n下次：{local_time(due)}（台灣時間）\n內容：{spec["content"]}\n逾期超過 {spec.get("grace_minutes",15)} 分鐘跳過；失敗不自動重跑。'
        if spec['kind'] in ('line_message','line_dynamic'):
            text+='\nLINE 共用帳號 → 群組：'+spec['recipient']+'\n占用時等待有效期限；送出結果不明不重送。'
        if spec.get('preview'):text+='\n模式：僅預覽，不發送 LINE。'
        await self.c.tg.say(text)
        await self.c.tg.buttons('確認後才套用；15 分鐘內有效。', [[('確認','sc:'+key+':yes'),('取消','sc:'+key+':no')]])
        return {'state':'awaiting_user_confirmation','name':spec['name']}

    async def listing(self):
        try:
            rows=await self.tasks()
        except Exception:
            rows=self.store.tasks()
            await self.c.tg.say('影片排程狀態暫時無法讀取，沒有變更它；其餘排程如下。')
        text='\n\n'.join(json.loads(r['spec'])['name']+'\n'+('啟用' if r['enabled'] else '停用')+' · 下次 '+local_time(r['next']) for r in rows)
        await self.c.tg.say(text or '尚無排程。可說「每天早上九點提醒我……」。')
        for r in rows:
            task = r['id']
            action, label = ('pause','暫停') if r['enabled'] else ('resume','恢復')
            await self.c.tg.buttons(json.loads(r['spec'])['name'], [
                [(label, f'sm:{action}:{task}'), ('修改', f'sm:update:{task}')],
                [('最近結果', f'sm:runs:{task}'), ('刪除', f'sm:delete:{task}')]])

    async def callback(self, query):
        _, action, task = query.data.split(':')
        row = (await self.media.read() if task=='windows-media-transform' else
               next((r for r in self.store.tasks() if r['id'] == task), None))
        if not row:
            raise ValueError('Task not owned or deleted')
        await query.answer()
        if action == 'update':
            if task=='windows-media-transform':
                return await self.c.tg.say('修改時間：\n/cron time HH:MM\n修改名稱：\n/cron name 新名稱\n時間使用台灣時間；確認後才套用。轉檔規則維持不變。')
            return await self.c.tg.say('請傳送：/cron 修改「'+json.loads(row['spec'])['name']+'」，再加上新的名稱、時間或內容。\n修改前會顯示內容讓你確認。')
        if action == 'runs':
            if task=='windows-media-transform':
                return await self.c.tg.say(await self.media.recent())
            rows = [r for r in self.store.runs() if r['task']==task][:3]
            labels={'completed':'完成','failed':'失敗','blocked':'待處理','unknown':'結果不明','queued':'等待','running':'執行中','missed':'逾期跳過','cancelled':'取消'}
            return await self.c.tg.say('\n\n'.join(local_time(r['due'])+' · '+labels.get(r['state'],r['state'])+'\n'+(r['result'] or '尚無結果')[:1000] for r in rows) or '此任務尚無執行紀錄。')
        if action not in ('pause','resume','delete'):
            raise ValueError('Invalid action')
        return await self.tool({'action':action,'task':task})


def describe(s):
    if s['kind']=='daily':return '每天 '+s['time']
    if s['kind']=='weekly':return '每週'+ '、'.join('一二三四五六日'[d] for d in s['days'])+' '+s['time']
    if s['kind']=='interval':return f'每 {s["minutes"]} 分鐘'
    return s['at']
