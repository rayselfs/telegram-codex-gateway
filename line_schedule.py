"""Fixed LINE schedule adapter. One durable send reservation per confirmed occurrence."""
import json
from pathlib import Path
import queue
import subprocess
import threading
import time
from lifecycle import SingleInstance, ChildJob
from schedules import deadline as run_deadline

LOCK=Path('D:/CodexTelegram/profile/line-browser.lock')
SCRIPT=Path('D:/CodexTelegram/bridge/line-scheduled-send.mjs')

def halt(store,run,state,message):
    with store.db:
        store.db.execute('CREATE TABLE IF NOT EXISTS line_deliveries(run TEXT PRIMARY KEY,state TEXT NOT NULL,receipt TEXT)')
        store.db.execute('UPDATE tasks SET enabled=0,next=NULL WHERE id=?',(run['task'],))
        store.db.execute("UPDATE runs SET state='cancelled',result='LINE 上次結果不明，暫停後續執行' WHERE task=? AND state='queued'",(run['task'],))
        store.db.execute("UPDATE line_deliveries SET state='unknown' WHERE run=? AND state='sending'",(run['id'],))
    return state,message+' 此排程已暫停，請核對後再決定是否恢復。'

def uncertain(store,run):
    return halt(store,run,'unknown','LINE 送出結果不明，不重送；請核對指定群組。')

def execute(profile,run,store):
    spec=json.loads(run['spec'])
    if (type(profile.get('owner_id')) is not int or profile['owner_id']<=0) or spec['kind']!='line_message':
        raise ValueError('Invalid LINE owner/task')
    store.db.execute('CREATE TABLE IF NOT EXISTS line_deliveries(run TEXT PRIMARY KEY,state TEXT NOT NULL,receipt TEXT)')
    store.db.commit()
    old=store.db.execute('SELECT state,receipt FROM line_deliveries WHERE run=?',(run['id'],)).fetchone()
    if old:
        if old['state']=='sent':return 'completed',old['receipt']
        return uncertain(store,run)
    if time.time()>run_deadline(run):return 'missed','LINE 已超過 設定的有效期限，未送出。'
    try:lease=SingleInstance(LOCK)
    except RuntimeError:return 'queued','LINE 瀏覽器占用，等待下一次排程檢查；逾期不補送。'
    proc=None;job=None;reserved=False
    try:
        proc=subprocess.Popen(['C:/nvm4w/nodejs/node.exe',str(SCRIPT)],stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,encoding='utf-8')
        job=ChildJob(proc)
        events=queue.Queue()
        def read():
            for line in proc.stdout:
                try:events.put(json.loads(line))
                except ValueError:events.put({'stage':'invalid'})
            events.put({'stage':'closed'})
        threading.Thread(target=read,daemon=True).start()
        def write(value):proc.stdin.write(json.dumps(value,ensure_ascii=False)+'\n');proc.stdin.flush()
        write({'config':str(profile['home']/'line-browser-config.json'),
               'recipient':spec['recipient'],'text':spec['content']})
        deadline=time.monotonic()+120
        while True:
            event=events.get(timeout=max(.01,deadline-time.monotonic()))
            stage=event.get('stage')
            if stage=='ready' and not reserved:
                if event.get('recipient')!=spec['recipient']:
                    return halt(store,run,'blocked','LINE 對象核對不符，未送出。')
                if time.time()>run_deadline(run):return 'missed','準備完成時已逾期，未送出。'
                # Commit before permitting the single UI send; crash afterwards is ambiguous.
                with store.db:
                    store.db.execute('INSERT INTO line_deliveries VALUES(?,?,?)',
                        (run['id'],'sending',json.dumps(event,ensure_ascii=False)))
                reserved=True
                write({'send':True})
            elif stage=='sent' and reserved:
                mid=event.get('messageId')
                if not isinstance(mid,str) or not mid.isascii() or not mid.isdigit():
                    raise ValueError('No confirmed LINE message ID')
                result='LINE 已送至「'+spec['recipient']+'」。訊息 ID：'+mid
                with store.db:store.db.execute("UPDATE line_deliveries SET state='sent',receipt=? WHERE run=?",(result,run['id']))
                return 'completed',result
            elif stage=='busy' and not reserved:
                return 'queued','LINE 瀏覽器占用，等待下一次排程檢查；逾期不補送。'
            elif stage=='blocked' and not reserved:
                return halt(store,run,'blocked','LINE 登入、唯一群組或輸入欄位未確認；未送出，請本人核對。')
            else:
                raise RuntimeError('LINE child ended without confirmation')
    except Exception:
        return uncertain(store,run) if reserved else halt(store,run,'blocked','LINE 準備未完成，未送出；請核對登入與瀏覽器。')
    finally:
        if proc:
            if proc.stdin:proc.stdin.close()
            try:proc.wait(timeout=8)
            except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=5)
            if proc.stdout:proc.stdout.close()
        if job:job.close()
        lease.close()
