"""Owner-local account operations; never start an inference turn."""
import asyncio
from pathlib import Path
import re
import subprocess
from bootstrap import CODEX, child_environment
from lifecycle import ChildJob, SingleInstance
from gateway.platforms import current

def authorize(members, owner):
    row=members._member(owner)
    if row['state']=='active': return members.require_active(owner)
    return members.require_onboarding(owner)

async def check(row,members):
    from rpc import Client
    owner=row['owner_id']
    auth=lambda: authorize(members,owner)
    auth()
    rpc=Client(Path(row['home']),authorize=auth)
    job=ChildJob(rpc._proc)
    try:
        await asyncio.to_thread(rpc.initialize,'gateway_onboarding','Gateway account check','1',{'experimentalApi':True})
        result=await rpc.call('account/read',{'refreshToken':False})
        auth()
        return bool(result.get('account'))
    finally:
        await asyncio.to_thread(rpc.close); job.close()

def device_instructions(text):
    # Only return a provider URL and user code, never arbitrary CLI output.
    urls=re.findall(r'https://auth\.openai\.com/[A-Za-z0-9/_-]+',text)
    codes=re.findall(r'\b[A-Z0-9]{4}-[A-Z0-9]{4,6}\b',text)
    return '\n'.join(dict.fromkeys(urls+codes))

async def login(row,send,members):
    owner=row['owner_id'];authorize(members,owner)
    home=Path(row['home']);lease=SingleInstance(home/'onboarding.lock')
    process=None;job=None
    env=child_environment()
    env.update(CODEX_HOME=str(home/'codex-home'),CODEX_SQLITE_HOME=str(home/'codex-home'))
    try:
        process=subprocess.Popen([str(CODEX),'login','--device-auth'],env=env,cwd=home,
            stdout=subprocess.PIPE,stderr=subprocess.STDOUT,**current().process_options())
        job=ChildJob(process)
        async def read():
            total=0
            while line:=await asyncio.to_thread(process.stdout.readline):
                authorize(members,owner)
                total+=len(line)
                if total>32768: raise RuntimeError('Login output exceeded limit')
                text=device_instructions(line.decode('utf-8',errors='replace'))
                if text: await send(owner,'請本人在提供者頁面登入：\n'+text)
            return await asyncio.to_thread(process.wait)
        result=await asyncio.wait_for(read(),180)
        await send(owner,'登入程序已結束，請管理員執行啟用檢查。' if result==0 else '登入未完成，可重試 /login codex。')
    finally:
        if process and process.poll() is None: process.terminate()
        if job: job.close()
        if process: await asyncio.to_thread(process.wait)
        lease.close()
