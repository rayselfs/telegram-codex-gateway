"""Local recorded token counters, never billing or account-wide consumption."""
import asyncio
import json
from pathlib import Path

FIELDS=('input_tokens','cached_input_tokens','output_tokens','reasoning_output_tokens','total_tokens')

# Standard USD per million tokens, verified 2026-11-07: developers.openai.com/api/docs/pricing
PRICES={'gpt-6.1-sol':(2,.1,10),'gpt-6-astra':(10,1,50)}

def estimate(delta,model,request_input):
    rates=PRICES.get(model)
    if rates is None:return None
    uncached=delta['input_tokens']-delta['cached_input_tokens']-delta['cache_write_input_tokens']
    if uncached<0:return None
    input_rate,cache_rate,output_rate=rates
    if request_input>272000:input_rate*=2;cache_rate*=2;output_rate*=1.5
    # shortcut: cache-write retention is absent in native logs; use the standard 30-minute rate until recorded.
    return (uncached*input_rate+delta['cached_input_tokens']*cache_rate+delta['cache_write_input_tokens']*input_rate*1.25+delta['output_tokens']*output_rate)/1000000

def summarize(folder):
    folder=Path(folder)
    result=dict(tokens={k:0 for k in FIELDS},sessions=0,incomplete_files=0,files_without_usage=0,missing=not folder.is_dir(),estimated_usd=None)
    if result['missing']:return result
    sessions={};costs={};budget=256*1024*1024
    for path in sorted(folder.rglob('*.jsonl')):
        if path.is_symlink() or not path.resolve().is_relative_to(folder.resolve()):
            result['incomplete_files']+=1;continue
        identity=None;latest=None;bad=False;model=None;cost=0.;priced=True
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
                        elif row.get('type')=='turn_context':model=payload.get('model')
                        elif row.get('type')=='event_msg' and payload.get('type')=='token_count':
                            info=payload.get('info') or {};counts=info.get('total_token_usage')
                            if counts is None:continue
                            if any(type(counts.get(k)) is not int or counts[k]<0 for k in FIELDS):bad=True;continue
                            snapshot={k:counts[k] for k in FIELDS}
                            write=counts.get('cache_write_input_tokens',0)
                            if type(write) is not int or write<0:bad=True;continue
                            snapshot['cache_write_input_tokens']=write
                            if latest and any(snapshot[k]<latest[k] for k in FIELDS):bad=True
                            delta={k:max(0,v-(latest or {}).get(k,0)) for k,v in snapshot.items()}
                            if delta['total_tokens']:
                                request_input=(info.get('last_token_usage') or {}).get('input_tokens',delta['input_tokens'])
                                if type(request_input) is not int or request_input<0:priced=False
                                else:
                                    amount=estimate(delta,model,request_input)
                                    if amount is None:priced=False
                                    else:cost+=amount
                            latest={k:max((latest or {}).get(k,0),v) for k,v in snapshot.items()}
                    except (ValueError,AttributeError,TypeError):bad=True
        except (OSError,UnicodeError):bad=True
        if bad:result['incomplete_files']+=1
        if identity and latest:
            previous=sessions.get(identity,{k:0 for k in FIELDS})
            if identity not in costs or latest['total_tokens']>previous['total_tokens']:
                costs[identity]=cost if priced and not bad else None
            sessions[identity]={k:max(previous[k],latest[k]) for k in FIELDS}
        else:result['files_without_usage']+=1
    result['sessions']=len(sessions)
    result['tokens']={k:sum(v[k] for v in sessions.values()) for k in FIELDS}
    if sessions and not result['incomplete_files'] and all(v is not None for v in costs.values()):
        result['estimated_usd']=sum(costs.values())
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
        lines=[]
        for row,result in results:
            name=str(row['display_name']).replace('\n',' ')[:128]
            if lines:lines.append('')
            lines.append(name)
            if result['missing'] or not result['sessions']:
                lines.append('尚無使用紀錄')
            else:
                lines.append(f"已使用 {result['tokens']['total_tokens']:,} tokens")
                cost=result['estimated_usd']
                lines.append('API 標準價估算：'+(f'約 US${cost:,.2f}' if cost is not None else '暫無法估算'))
        return '\n'.join(lines)
