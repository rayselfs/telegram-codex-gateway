"""Read shared policy and only the bound owner's personal guidance per turn."""
from pathlib import Path
from gateway.core.bootstrap import ROOT

SHARED_AGENTS=ROOT/'AGENTS.md'


def load_instructions(personal_path=None):
    shared=SHARED_AGENTS.read_text(encoding='utf-8-sig').strip() if SHARED_AGENTS.exists() else ''
    result=('# Telegram 共用指引：'+str(SHARED_AGENTS)+'\n'+shared) if shared else ''
    if personal_path is not None:
        personal_path=Path(personal_path)
        result+='\n\n# 本人管理的個人指引：'+str(personal_path)+'\n'
        if personal_path.exists():result+=personal_path.read_text(encoding='utf-8-sig')
    return result
