"""Explicit member relay; deployment contacts and receipts stay outside source."""
import asyncio
import hashlib
import json
import re
import sqlite3
import unicodedata
from gateway.telegram.registration import Registration

class RelayRegistration(Registration):
    async def handle(self,update):
        user,chat=update.effective_user,update.effective_chat
        message=getattr(update,'message',None)
        text=(getattr(message,'text',None) or '').strip()
        route_path=self.members.root/'profile/member-relay.json'
        if not user or user.is_bot or not chat or chat.type!='private' or user.id!=chat.id or not route_path.is_file():
            return await super().handle(update)
        # Only an explicit, original user message authorizes delivery; no model-selected recipient.
        match=re.fullmatch(r'(?:/tell(?:@'+re.escape(self.bot_name)+r')?\s+|(?:請|幫我)?(?:告訴|轉告|通知|傳訊息給|發訊息給)\s*)([^：:\n]{1,128}?)[：:]([\s\S]*)',text,re.IGNORECASE)
        if not match:
            match=re.fullmatch(r'(?:請|幫我)?(?:傳訊息給|發訊息給)[^\S\r\n]*([^：:\r\n]{1,128})\r?\n([\s\S]*)',text)
        if not match:return await super().handle(update)
        target,body=match[1].strip(),match[2]
        try:
            if getattr(message,'forward_origin',None) or getattr(message,'forward_date',None):
                raise ValueError('Forwarded text is not an instruction')
            result=await self.relay(user.id,getattr(message,'message_id',None),target,body,route_path)
        except (ValueError,PermissionError):
            result='未傳送：請確認本人與收件人的代傳資料及權限；姓名須能唯一辨識，請用完整姓名。格式：幫我告訴 姓名：內容（上限 2000 字元）。'
        await self.send(user.id,result)
        return True

    async def relay(self,actor,message_id,target,body,route_path):
        config=json.loads(route_path.read_text(encoding='utf-8-sig'))
        if set(config)!={'contacts','allowed_pairs'}:raise ValueError('Invalid relay directory')
        contacts=config['contacts']
        if not isinstance(contacts,dict) or not isinstance(config['allowed_pairs'],list):raise ValueError('Invalid directory')
        self.members.require_active(actor)
        if str(actor) not in contacts:raise PermissionError('Sender profile required')
        normalize=lambda value:unicodedata.normalize('NFKC',value).strip().casefold()
        matches=[]
        for identity,profile in contacts.items():
            if not identity.isascii() or not identity.isdigit() or str(int(identity))!=identity or int(identity)<=0:raise ValueError('Invalid identity')
            if not isinstance(profile,dict) or set(profile)!={'name','aliases'} or not isinstance(profile['aliases'],list):raise ValueError('Invalid contact')
            labels=[profile['name'],*profile['aliases']]
            if any(not isinstance(v,str) or not v.strip() or len(v)>128 or any(ord(c)<32 for c in v) for v in labels):raise ValueError('Invalid contact name')
            if normalize(target) in {normalize(v) for v in labels}:matches.append(int(identity))
        if len(matches)!=1:raise ValueError('Unknown or ambiguous recipient')
        recipient=matches[0]
        if recipient==actor or [actor,recipient] not in config['allowed_pairs']:raise PermissionError('Route not permitted')
        self.members.require_active(recipient)
        route=dict(recipient=recipient,sender_label=contacts[str(actor)]['name'],recipient_label=contacts[str(recipient)]['name'])
        body=body.strip()
        if type(message_id) is not int or message_id<=0 or not body or len(body.encode('utf-16-le'))//2>2000:raise ValueError('Invalid message')
        digest=hashlib.sha256((str(route['recipient'])+'\n'+body).encode('utf-8')).hexdigest()
        path=route_path.with_name('member-relay.sqlite')
        db=sqlite3.connect(path)
        try:
            db.execute('CREATE TABLE IF NOT EXISTS relay(actor INTEGER,message INTEGER,digest TEXT,state TEXT,receipts TEXT,PRIMARY KEY(actor,message))')
            db.execute('BEGIN IMMEDIATE')
            previous=db.execute('SELECT digest,state,receipts FROM relay WHERE actor=? AND message=?',(actor,message_id)).fetchone()
            if previous:
                db.rollback()
                if previous[0]!=digest:raise ValueError('Same request changed')
                return self.result(previous[1],route['recipient_label'])
            db.execute('INSERT INTO relay VALUES(?,?,?,?,?)',(actor,message_id,digest,'sending','[]'));db.commit()
            try:
                receipts=await self.send(route['recipient'],route['sender_label']+' 委託轉達：\n\n'+body)
                if not isinstance(receipts,list) or len(receipts)!=1 or type(receipts[0]) is not int:raise RuntimeError('Unconfirmed receipt')
            except BaseException as error:
                db.execute("UPDATE relay SET state='unknown' WHERE actor=? AND message=?",(actor,message_id));db.commit()
                if isinstance(error,(asyncio.CancelledError,KeyboardInterrupt,SystemExit)):raise
                return self.result('unknown',route['recipient_label'])
            db.execute("UPDATE relay SET state='delivered',receipts=? WHERE actor=? AND message=?",(json.dumps(receipts),actor,message_id));db.commit()
            return self.result('delivered',route['recipient_label'])
        finally:db.close()

    @staticmethod
    def result(state,name):
        return ('已傳送給 '+name+'（不代表已讀）。' if state=='delivered' else '送達狀態不確定，為避免重複，不會自動重送；請先向對方確認。')
