"""Fixed-text registration commands. No model is involved in authorization."""
from pathlib import Path
import asyncio
from gateway.core.init import Bootstrap

class Registration:
    def __init__(self,members,send,login,check,activate,bot_name,*,shared_policy=None):
        self.extensions={}
        self.login_tasks={}
        self.shared_policy=shared_policy
        self.attached={r['owner_id'] for r in members.active_profiles()}
        self.members,self.send,self.login,self.check,self.activate=members,send,login,check,activate
        self.bot_name=bot_name

    async def close(self):
        tasks=list(self.login_tasks.values())
        for task in tasks:task.cancel()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)

    def begin_login(self,row):
        owner=row['owner_id']
        if owner in self.login_tasks and not self.login_tasks[owner].done():return
        async def run():
            try:await self.login(row)
            except asyncio.CancelledError:raise
            except Exception:
                await self.send(owner,'登入未完成；原資料保留，可重試 /login codex。')
            finally:self.login_tasks.pop(owner,None)
        self.login_tasks[owner]=asyncio.create_task(run())

    async def handle(self,update):
        user,chat=update.effective_user,update.effective_chat
        if not user or user.is_bot or not chat or chat.type!='private' or user.id!=chat.id:
            return False
        actor=user.id
        message=getattr(update,'message',None)
        text=(getattr(message,'text',None) or '').strip()
        parts=text.split(); command=parts[0].split('@')[0].lower() if parts else ''
        try:
            if command=='/start' and len(parts)==2:
                if parts[1].startswith('bootstrap_'):
                    claim=Bootstrap(self.members).claim_bootstrap(parts[1][10:],actor,private=True)
                    await self.send(actor,'配對待主機確認。Telegram ID：'+str(actor)+'；認領 ID：'+claim)
                    return True
                if parts[1].startswith('invite_'):
                    self.members.claim(parts[1][7:],actor,getattr(user,'full_name',str(actor)))
                    await self.send(actor,'已認領邀請，待管理員核准。');return True
            if command=='/invite':
                token=self.members.invite(actor)
                await self.send(actor,'https://t.me/'+self.bot_name+'?start=invite_'+token)
                return True
            if command=='/members':
                self.members.require_admin(actor)
                if len(parts)==1:
                    rows=self.members.list_members(actor)
                    await self.send(actor,'\n'.join(f"{r['owner_id']} {r['display_name']} {r['role']} {r['state']}" for r in rows))
                    return True
                if len(parts)<3 or not parts[2].isascii() or not parts[2].isdigit():
                    raise ValueError('Numeric member ID required')
                action,owner=parts[1],int(parts[2])
                if action=='approve':
                    self.members.approve(actor,owner);self.members.provision(actor,owner)
                elif action=='activate':
                    row=self.members._member(owner)
                    if row['state']!='active':
                        candidate=self.members.require_onboarding(owner)
                        ready=await self.check(candidate)
                        self.members.activate(actor,owner,lambda _:ready)
                    if owner not in self.attached:
                        await self.activate(self.members.require_active(owner))
                        self.attached.add(owner)
                elif action in ('suspend','restore'):
                    reason=' '.join(parts[3:])
                    getattr(self.members,action)(actor,owner,reason)
                elif action=='retry':
                    self.members.provision(actor,owner)
                else: raise ValueError('Unknown member command')
                await self.send(actor,'成員狀態已更新。');return True
            try:
                row=self.members.require_active(actor)
            except PermissionError:
                if command=='/login' and parts[1:]==['codex']:
                    row=self.members.require_onboarding(actor)
                    self.begin_login(row)
                elif command=='/registration':
                    row=self.members._member(actor)
                    await self.send(actor,'註冊狀態：'+row['state'])
                else:
                    await self.send(actor,'帳號尚未啟用，請使用有效邀請並等待管理員核准。')
                return True
            if command in self.extensions:
                message_id=getattr(message,'message_id',None)
                if type(message_id) is not int: raise ValueError('Telegram message identity required')
                result=await self.extensions[command](actor,text.partition(' ')[2],'telegram:'+str(actor)+':'+str(message_id))
                import json
                await self.send(actor,json.dumps(result,ensure_ascii=False))
                return True
            if command=='/persona' and self.shared_policy is not None:
                policy=self.shared_policy
                if len(parts)==1:
                    current=policy.active_persona()
                    await self.send(actor,'版本 '+str(current['version'])+' / '+current['policy']+'\n'+current['body'])
                    for proposal in policy.pending(actor):
                        await self.send(actor,'待核准 '+proposal['id']+' / '+proposal['hash'])
                elif parts[1]=='propose' and len(parts)>=4:
                    proposal=policy.propose(actor,int(parts[2]),text.split(None,3)[3])
                    await policy.notify(actor,proposal['id'],self.send)
                elif parts[1]=='policy' and len(parts)==4:
                    current=policy.active_persona()
                    proposal=policy.propose(actor,int(parts[2]),current['body'],policy=parts[3])
                    await policy.notify(actor,proposal['id'],self.send)
                elif parts[1]=='rollback' and len(parts)==4:
                    proposal=policy.propose_rollback(actor,int(parts[2]),int(parts[3]))
                    await policy.notify(actor,proposal['id'],self.send)
                elif parts[1]=='view' and len(parts)==3:
                    proposal=policy.view(actor,parts[2])
                    import json
                    await self.send(actor,json.dumps(proposal,ensure_ascii=False))
                elif parts[1]=='vote' and len(parts)==5 and parts[4] in ('approve','reject'):
                    current=policy.vote(actor,parts[2],parts[3],parts[4]=='approve')
                    await self.send(actor,'已記錄本人投票；現行版本 '+str(current['version']))
                else: raise ValueError('Invalid persona command')
                return True
            if command=='/resume_schedules':
                self.members.resume_own_schedules(actor)
                await self.send(actor,'已確認恢復本人的私人排程。');return True
            return False
        except (PermissionError,ValueError,RuntimeError):
            await self.send(actor,'操作未完成：請確認身分、註冊狀態、命令及本人登入。')
            return True
