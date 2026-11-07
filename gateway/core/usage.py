"""Local recorded token counters, never billing or account-wide consumption."""
import asyncio
from contextlib import closing
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
import sqlite3
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

MAX_LINE = 8 * 1024 * 1024
SCAN_BUDGET = 256 * 1024 * 1024


def consume(state, row, zone):
    payload = row.get('payload', {})
    match row.get('type'):
        case 'session_meta':
            identity = payload.get('id')
            if (not isinstance(identity, str) or not identity or len(identity) > 128
                    or state.get('identity', identity) != identity):
                raise ValueError('Invalid session identity')
            state['identity'] = identity
        case 'turn_context':
            state['model'] = payload.get('model')
        case 'event_msg' if payload.get('type') == 'token_count':
            info = payload.get('info') or {}
            counts = info.get('total_token_usage')
            if counts is None:
                return
            snapshot = {key: counts.get(key) for key in FIELDS}
            snapshot['cache_write_input_tokens'] = counts.get('cache_write_input_tokens', 0)
            if any(type(value) is not int or value < 0 for value in snapshot.values()):
                raise ValueError('Invalid token counters')
            previous = state.get('latest', {})
            if any(value < previous.get(key, 0) for key, value in snapshot.items()):
                state['bad'] = True
            delta = {key: max(0, value - previous.get(key, 0)) for key, value in snapshot.items()}
            state['latest'] = {key: previous.get(key, 0) + delta[key] for key in snapshot}
            if not delta['total_tokens']:
                return

            month = ''
            try:
                timestamp = datetime.fromisoformat(row.get('timestamp', ''))
                if timestamp.tzinfo is not None:
                    month = timestamp.astimezone(zone).strftime('%Y-%m')
            except (ValueError, TypeError, OverflowError):
                pass
            request_input = (info.get('last_token_usage') or {}).get('input_tokens', delta['input_tokens'])
            model = state.get('model')
            cost = None
            if type(request_input) is int and request_input >= 0 and isinstance(model, str):
                cost = estimate(delta, model, request_input)
            bucket = state['months'].setdefault(month, [0, 0.0])
            bucket[0] += delta['total_tokens']
            bucket[1] = bucket[1] + cost if bucket[1] is not None and cost is not None else None


def anchor(source, offset):
    size = min(64, offset)
    source.seek(offset - size)
    return sha256(source.read(size)).hexdigest()


def scan_file(path, stamp, state, zone, budget):
    with path.open('rb') as source:
        stat = os.fstat(source.fileno())
        if (stat.st_dev, stat.st_ino) != tuple(stamp[:2]) or stat.st_size < stamp[2]:
            raise OSError('Session changed while opening')
        size = stamp[2]
        previous = state.get('stamp')
        offset = state.get('offset', 0)
        if (not previous or previous[:2] != stamp[:2] or stamp[2] < previous[2]
                or (stamp[2] == previous[2] and previous != stamp)
                or (offset and anchor(source, offset) != state['anchor'])):
            offset = 0
            state = {'months': {}}
        if state.get('blocked'):
            state['stamp'] = stamp
            return state, budget, False

        source.seek(offset)
        pending = False
        while offset < size:
            if not budget:
                pending = True
                break
            line = source.readline(min(size - offset, MAX_LINE + 1, budget))
            budget -= len(line)
            if len(line) > MAX_LINE:
                state.update(bad=True, blocked=True)
                break
            if not line.endswith(b'\n'):
                pending = not budget and offset + len(line) < size
                break  # A writer can still be finishing this record.
            offset += len(line)
            try:
                consume(state, json.loads(line.decode('utf-8')), zone)
            except (ValueError, AttributeError, TypeError):
                state['bad'] = True

        state.update(stamp=stamp, offset=offset, anchor=anchor(source, offset), pending=pending)
        return state, budget, pending


def indexed_files(folder, zone):
    # Only counters, model, dates and read cursors are persisted. Source logs remain authoritative.
    cache = folder.with_name(folder.name + '.usage-v1.sqlite3')
    if any(path.is_symlink() or (path.exists() and path.stat().st_nlink != 1)
           for path in (cache, cache.with_name(cache.name + '-journal'))):
        raise ValueError('Redirected usage index')
    states = {}
    incomplete = 0
    pending = False
    budget = SCAN_BUDGET
    with closing(sqlite3.connect(cache, timeout=15)) as db, db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, state TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS calendar (zone TEXT NOT NULL)')
        if db.execute('SELECT zone FROM calendar').fetchone() != (str(zone),):
            db.execute('DELETE FROM files')
            db.execute('DELETE FROM calendar')
            db.execute('INSERT INTO calendar VALUES (?)', (str(zone),))
        stored = {name: json.loads(value) for name, value in db.execute('SELECT path,state FROM files')}
        root = folder.resolve()
        for path in sorted(folder.rglob('*.jsonl')):
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                incomplete += 1
                continue
            key = path.relative_to(folder).as_posix()
            old = stored.pop(key, {})
            try:
                stat = path.stat()
                stamp = [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]
                if old.get('stamp') == stamp and not old.get('pending'):
                    states[key] = old
                    continue
                if not budget:
                    pending = True
                    continue
                state, budget, more = scan_file(path, stamp, old, zone, budget)
            except FileNotFoundError:
                db.execute('DELETE FROM files WHERE path=?', (key,))
                continue
            except OSError:
                incomplete += 1
                continue
            states[key] = state
            pending |= more
            db.execute('INSERT OR REPLACE INTO files VALUES (?,?)', (key, json.dumps(state)))
        db.executemany('DELETE FROM files WHERE path=?', ((key,) for key in stored))
    return states.values(), incomplete, pending


def summarize(folder, zone=timezone.utc, now=None):
    folder = Path(folder)
    month = (now or datetime.now(zone)).astimezone(zone)
    result = dict(tokens=dict.fromkeys(FIELDS, 0), sessions=0, incomplete_files=0,
                  files_without_usage=0, missing=not folder.is_dir(), estimated_usd=None,
                  month=month.month, monthly=dict(total_tokens=None, estimated_usd=None), pending=False)
    if result['missing']:
        return result

    states, incomplete, result['pending'] = indexed_files(folder, zone)
    sessions = {}
    for state in states:
        incomplete += bool(state.get('bad'))
        identity, latest = state.get('identity'), state.get('latest')
        if not identity or not latest:
            result['files_without_usage'] += 1
            continue
        previous = sessions.get(identity)
        # Keep one coherent history for each ID so overlapping copies cannot double count a month.
        if previous is None or latest['total_tokens'] > previous['latest']['total_tokens']:
            sessions[identity] = state
    result['incomplete_files'] = incomplete
    result['sessions'] = len(sessions)
    result['tokens'] = {key: sum(state['latest'][key] for state in sessions.values()) for key in FIELDS}
    if not sessions or result['pending'] or incomplete:
        return result

    month_key = month.strftime('%Y-%m')
    buckets = [bucket for state in sessions.values() for bucket in state['months'].values()]
    if all(cost is not None for _, cost in buckets):
        result['estimated_usd'] = sum(cost for _, cost in buckets)
    if all('' not in state['months'] for state in sessions.values()):
        current = [state['months'].get(month_key, (0, 0.0)) for state in sessions.values()]
        result['monthly'] = dict(
            total_tokens=sum(count for count, _ in current),
            estimated_usd=sum(cost for _, cost in current) if all(cost is not None for _, cost in current) else None)
    return result


def format_period(label, total, cost):
    if total is None:
        return f'{label}　紀錄不足'
    amount = f'約 US${cost:,.2f}' if cost is not None else '暫無法估算'
    return f'{label}　{total:,} tokens · {amount}'


class Usage:
    def __init__(self, members, zone=timezone.utc):
        self.members = members
        self.zone = zone

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
        results=await asyncio.to_thread(lambda:[(row,summarize(folder,self.zone)) for row,folder in targets])
        self.members.require_active(actor)
        if arg=='all':self.members.require_admin(actor)
        lines=[]
        for row,result in results:
            name=str(row['display_name']).replace('\n',' ')[:128]
            if lines:lines.append('')
            lines.append(name)
            if result['pending']:
                lines.append('使用量整理中，請稍後再查')
            elif result['missing'] or not result['sessions']:
                lines.append('尚無使用紀錄')
            else:
                lines.append(format_period('累計', result['tokens']['total_tokens'], result['estimated_usd']))
                monthly = result['monthly']
                lines.append(format_period(f"本月（{result['month']} 月）",
                                           monthly['total_tokens'], monthly['estimated_usd']))
        return '\n'.join(lines)
