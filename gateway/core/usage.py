"""Local recorded token counters, never billing or account-wide consumption."""
import asyncio
import json
from pathlib import Path

FIELDS=('input_tokens','cached_input_tokens','output_tokens','reasoning_output_tokens','total_tokens')

def summarize(folder):
    folder=Path(folder)
    result=dict(tokens={k:0 for k in FIELDS},sessions=0,incomplete_files=0,files_without_usage=0,missing=not folder.is_dir())
    if result['missing']:return result
    sessions={};budget=256*1024*1024
    for path in sorted(folder.rglob('*.jsonl')):
        if path.is_symlink() or not path.resolve().is_relative_to(folder.resolve()):
            result['incomplete_files']+=1;continue
        identity=None;latest=None;bad=False
        try:
            size=path.stat().st_size
            if size>budget:result['incomplete_files']+=1;continue
            budget-=size
            with path.open('r',encoding='utf-8') as source:
                while True:
                    line=source.readline(8*1024*1024+1)
                    if not line:break
                    if len(line)>8*1024*1024:bad=True;break
                    try:
                        row=json.loads(line);payload=row.get('payload',{})
                        if row.get('type')=='session_meta':
                            value=payload.get('id')
                            if not isinstance(value,str) or not value or len(value)>128 or (identity and identity!=value):bad=True;break
                            identity=value
                        elif row.get('type')=='event_msg' and payload.get('type')=='token_count':
                            info=payload.get('info') or {};counts=info.get('total_token_usage')
                            if counts is None:continue
                            if any(type(counts.get(k)) is not int or counts[k]<0 for k in FIELDS):bad=True;continue
                            snapshot={k:counts[k] for k in FIELDS}
                            if latest and any(snapshot[k]<latest[k] for k in FIELDS):bad=True
                            latest={k:max((latest or {}).get(k,0),snapshot[k]) for k in FIELDS}
                    except (ValueError,AttributeError,TypeError):bad=True
        except (OSError,UnicodeError):bad=True
        if bad:result['incomplete_files']+=1
        if identity and latest:
            previous=sessions.get(identity,{k:0 for k in FIELDS})
            sessions[identity]={k:max(previous[k],latest[k]) for k in FIELDS}
        else:result['files_without_usage']+=1
    result['sessions']=len(sessions)
    result['tokens']={k:sum(v[k] for v in sessions.values()) for k in FIELDS}
    return result

class Usage:
    def __init__(self,members):self.members=members

    async def command(self,actor,text,request):
        self.members.require_active(actor)
        arg=text.strip().lower()
        if arg not in ('','self','all'):raise ValueError('Use /usage or /usage all')
        if arg=='all':
            self.members.require_admin(actor)
            rows=[dict(r) for r in self.members.db.execute('SELECT owner_id,display_name,home FROM members ORDER BY owner_id')]
        else:rows=[self.members.require_active(actor)]
        targets=[]
        for row in rows:
            home=Path(row['home']).resolve()
            if not home.is_relative_to(self.members.root.resolve()):raise ValueError('Member home outside deployment')
            folder=home/'codex-home/sessions'
            if not folder.resolve().is_relative_to(home):raise ValueError('Redirected session directory')
            targets.append((row,folder))
        # Parsing local history never sends prompts to a model or reads account credentials.
        results=await asyncio.to_thread(lambda:[(row,summarize(folder)) for row,folder in targets])
        self.members.require_active(actor)
        if arg=='all':self.members.require_admin(actor)
        lines=['本機保留紀錄的 token 累計（非帳單）']
        for row,result in results:
            name=str(row['display_name']).replace('\n',' ')[:128]
            lines.append('\n'+name)
            if result['missing'] or not result['sessions']:
                lines.append('無可用用量紀錄，不能視為零使用量。')
            else:
                v=result['tokens']
                lines.append(f"對話 {result['sessions']}；總 token {v['total_tokens']:,}")
                lines.append(f"輸入 {v['input_tokens']:,}（快取 {v['cached_input_tokens']:,}）；輸出 {v['output_tokens']:,}（推理 {v['reasoning_output_tokens']:,}）")
            if result['incomplete_files'] or result['files_without_usage']:
                lines.append(f"資料缺口：不完整檔 {result['incomplete_files']}；無用量檔 {result['files_without_usage']}。")
        lines.append('\n快取／推理為細項，不再加到總數。按對話累計高水位去重；分支可能含繼承用量。只涵蓋本機保留紀錄，不代表帳號全部使用、即時額度或費用。')
        return '\n'.join(lines)
