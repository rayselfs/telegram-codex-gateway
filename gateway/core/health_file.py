"""Small atomic JSON files for observers; no partial health snapshots."""
import json
import os
from pathlib import Path

def write_json(path,value):
    path=Path(path);temp=path.with_name(path.name+'.'+str(os.getpid())+'.tmp')
    try:
        temp.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')
        os.replace(temp,path)
    finally:temp.unlink(missing_ok=True)
