"""Contain owner service failures without replaying work or exposing exception values."""
import json
import time
from pathlib import Path

def record_error(home,service,error):
    frames=[];tb=error.__traceback__
    while tb:
        code=tb.tb_frame.f_code
        frames.append({'file':Path(code.co_filename).name,'function':code.co_name,'line':tb.tb_lineno})
        tb=tb.tb_next
    record={'time':time.time(),'service':service,'type':type(error).__name__,'frames':frames[-12:]}
    try:
        with (Path(home)/'service-errors.jsonl').open('a',encoding='utf-8') as out:
            out.write(json.dumps(record,ensure_ascii=True)+'\n')
    except OSError:pass  # Diagnostic storage failure must not bring down another owner.
    return record

def fail(item,name,error):
    faults=item.setdefault('service_faults',{})
    if name not in faults:
        faults[name]=record_error(item['profile']['home'],name,error)

async def supervise(item,name,operation,*,terminal=True):
    if name in item.get('service_faults',{}):return
    try:
        await operation()
        if terminal and not item['controller'].closed:
            raise RuntimeError('Owner service ended unexpectedly')
    except Exception as error:
        fail(item,name,error)

async def check_recovery(item):
    task=item.get('recovery_task')
    if task is not None:
        item['recovery_task']=None
        try:await task
        except Exception as error:fail(item,'recovery',error)
    return not item.get('service_faults')
