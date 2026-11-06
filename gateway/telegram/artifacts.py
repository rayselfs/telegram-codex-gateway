"""Deliver explicitly linked final artifacts, never scan a user's workspace."""
import asyncio
import hashlib
from pathlib import Path
import re
from urllib.parse import unquote
from state import safe_file, snapshot_file

EXTENSIONS = {'.docx', '.pdf', '.pptx', '.xlsx', '.csv', '.txt', '.md', '.png', '.jpg', '.jpeg', '.webp', '.mp3', '.mp4', '.zip'}


def linked_files(text, workspace):
    root = Path(workspace).resolve()
    seen = set()
    for raw in re.findall(r'!?\[[^\]\n]*\]\((<[^>\n]+>|[^)\n]+)\)', text):
        value = unquote(raw.strip('<>'))
        if value.startswith('sandbox:'):
            value = value[8:]
        if re.match(r'^/[A-Za-z]:[/\\]', value):
            value = value[1:]
        if '://' in value:
            continue
        try:
            path = Path(value)
            relative = str(path.relative_to(root)) if path.is_absolute() else value
            if any(p.startswith('.') for p in Path(relative).parts):
                continue
            target = safe_file(root, relative)
            if target.suffix.lower() not in EXTENSIONS or target in seen:
                continue
            seen.add(target)
            yield relative
        except (ValueError, OSError):
            continue


async def deliver(controller, text, turn_key):
    for relative in list(linked_files(text, controller.workspace))[:10]:
        key = 'artifact:' + turn_key + ':' + hashlib.sha256(relative.casefold().encode()).hexdigest()
        # Sending is durable before the network call: ambiguous sends are never retried automatically.
        if controller.journal.db.execute('SELECT 1 FROM delivery WHERE key=?', (key,)).fetchone():
            continue
        copy = None
        try:
            copy, name = await asyncio.to_thread(snapshot_file, controller.workspace, relative,
                                                controller.tg.inbox.parent / 'outbox')
        except (ValueError, OSError):
            await controller.tg.say('成品無法附檔（可能超過 49 MiB 或檔案已變更）：' + relative + '\n可請我縮小檔案後再提供。')
            continue
        controller.journal.delivery(key, 'pending')
        try:
            receipt = await controller.tg.document(copy, name)
            controller.journal.delivery(key, 'delivered', [receipt])
        except Exception:
            controller.journal.delivery(key, 'unknown')
            await controller.tg.say('成品是否送達尚無法確認，未自動重送。需要時可用 /get ' + relative)
        finally:
            copy.unlink(missing_ok=True)
