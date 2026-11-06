"""Deliver durable results separately; never rerun a task to retry a notification."""
import asyncio
import json
from pathlib import Path
from schedules import local_time
from transport import DELIVERY_REF


async def deliver(store, tg):
    while not tg.controller.closed:
        if getattr(tg, 'members', None) is not None:
            try: tg.require_member()
            except PermissionError:
                await asyncio.sleep(1); continue
        if tg.health()=='ready':
            rows=store.db.execute("SELECT * FROM runs WHERE state NOT IN ('queued','running') AND notification='ready' ORDER BY created LIMIT 5").fetchall()
            for row in rows:
                with store.db:
                    changed=store.db.execute("UPDATE runs SET notification='sending' WHERE id=? AND notification='ready'",(row['id'],)).rowcount
                if not changed:continue
                spec=json.loads(row['spec'])
                states={'completed':'完成','failed':'未完成','blocked':'待處理','unknown':'結果不明','missed':'逾期跳過','cancelled':'已取消','interrupted':'已停止'}
                token=DELIVERY_REF.set('schedule:'+row['id'])
                try:
                    text=row['result'] or '無文字結果'
                    if row['state']!='completed':
                        text=f'排程：{spec["name"]}\n預定：{local_time(row["due"])}\n結果：{states.get(row["state"],row["state"])}\n\n'+text
                    await tg.say(text)
                    attachments=store.db.execute("SELECT * FROM attachments WHERE run=? AND state='ready'",(row['id'],)).fetchall()
                    for attachment in attachments:
                        with store.db:
                            claimed=store.db.execute("UPDATE attachments SET state='sending' WHERE key=? AND state='ready'",(attachment['key'],)).rowcount
                        if not claimed:continue
                        path=Path(attachment['file']).resolve(strict=True)
                        if not path.is_relative_to(tg.controller.workspace.resolve()) or path.suffix.lower()!='.docx':
                            raise ValueError('Attachment escaped owner workspace')
                        await tg.say(attachment['label'])
                        receipt=await tg.document(path,path.name)
                        with store.db:
                            store.db.execute("UPDATE attachments SET state='delivered',receipt=? WHERE key=?",(json.dumps(receipt),attachment['key']))
                    state='delivered'
                except Exception:
                    state='unknown'
                    with store.db:
                        store.db.execute("UPDATE attachments SET state='unknown' WHERE run=? AND state='sending'",(row['id'],))
                finally:DELIVERY_REF.reset(token)
                with store.db:store.db.execute('UPDATE runs SET notification=? WHERE id=?',(state,row['id']))
        await asyncio.sleep(3)
