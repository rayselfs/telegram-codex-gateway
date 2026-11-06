"""Fixed-text registration commands. No model is involved in authorization."""
from pathlib import Path
from gateway.core.init import Bootstrap

class Registration:
    def __init__(self,members,send,login,check,activate,bot_name,*,shared_policy=None):
        self.shared_policy=shared_policy
        self.attached={r['owner_id'] for r in members.active_profiles()}
        self.members,self.send,self.login,self.check,self.activate=members,send,login,check,activate
        self.bot_name=bot_name

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
                    await self.login(row)
                elif command=='/registration':
                    row=self.members._member(actor)
                    await self.send(actor,'註冊狀態：'+row['state'])
                else:
                    await self.send(actor,'帳號尚未啟用，請使用有效邀請並等待管理員核准。')
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
                    for member in self.members.active_profiles():
                        await self.send(member['owner_id'],'共用指引提案 '+proposal['id']+'\n雜湊 '+proposal['hash']+'\n'+proposal['body'])
                elif parts[1]=='view' and len(parts)==3:
                    proposal=policy.view(actor,parts[2])
                    await self.send(actor,proposal['hash']+'\n'+proposal['body']+'\n'+proposal['diff'])
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
