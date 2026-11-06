from bootstrap import CODE_ROOT
from gateway.platforms import current
process_options = current().process_options
"""Own one Gateway process; restart exits only, never replay a task or kill on health."""
import json
import subprocess
import time
from bootstrap import ROOT,PYTHON,child_environment
from lifecycle import SingleInstance,ChildJob
from health_file import write_json

def allow_restart(state,now):
    attempts=[t for t in state.get('restarts',[]) if 0<=now-t<3600]
    if len(attempts)>=3:
        state.update(restarts=attempts,phase='exhausted');return False
    attempts.append(now);state.update(restarts=attempts,phase='restarting');return True

def main():
    home=ROOT/'profile';lease=SingleInstance(home/'guard.lock')
    state_path=home/'guard-state.json'
    try:
        state=json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {}
        if state.get('phase')=='exhausted' and not allow_restart(state,time.time()):return
        # Refuse to compete with a Gateway launched outside this guard.
        probe=SingleInstance(home/'gateway.lock');probe.close()
        while not (home/'stop.request').exists() and not (home/'maintenance.request').exists():
            proc=subprocess.Popen([str(PYTHON),'-B',str(CODE_ROOT/'main.py'),'serve'],
                cwd=CODE_ROOT,env=child_environment(),**process_options())
            job=None
            try:
                job=ChildJob(proc)
                state.update(phase='running',pid=proc.pid,started=time.time());write_json(state_path,state)
                proc.wait()
            finally:
                if job:job.close()
                elif proc.poll() is None:proc.terminate();proc.wait(timeout=10)
            if (home/'stop.request').exists() or (home/'maintenance.request').exists():
                state.update(phase='stopped');write_json(state_path,state);return
            state['exit_code']=proc.returncode
            if not allow_restart(state,time.time()):write_json(state_path,state);return
            write_json(state_path,state);time.sleep(15)
    finally:lease.close()

if __name__=='__main__':main()
