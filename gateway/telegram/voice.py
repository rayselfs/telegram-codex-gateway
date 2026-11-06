from bootstrap import CODE_ROOT
from gateway.platforms import current
process_options = current().process_options
import asyncio
import json
import subprocess
from pathlib import Path
from bootstrap import ROOT, child_environment


async def transcribe(path):
    env = child_environment()
    env.update(PYTHONUTF8='1', HF_HUB_OFFLINE='1', HF_HOME=str(ROOT/'runtime/voice-cache'),
               TEMP=str(path.parent), TMP=str(path.parent))
    proc = await asyncio.create_subprocess_exec(str(ROOT/'runtime/voice-venv/Scripts/python.exe'),
        '-B', str(CODE_ROOT/'voice_transcribe.py'), str(path), env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        **process_options())
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=180)
        result = json.loads(out.decode('utf-8'))
        if proc.returncode or not result.get('text'):
            raise ValueError('Speech recognition failed')
        return result['text']
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
