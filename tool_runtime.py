"""Read shared policy and only the bound owner's personal guidance per turn."""
from pathlib import Path
from bootstrap import ROOT

SHARED_AGENTS=ROOT/'AGENTS.md'


def load_instructions(personal_path=None):
    shared=SHARED_AGENTS.read_text(encoding='utf-8-sig').strip()
    if not shared:raise ValueError('Telegram shared AGENTS.md is empty')
    result='# Telegram 共用指引：'+str(SHARED_AGENTS)+'\n'+shared
    if personal_path is not None:
        personal_path=Path(personal_path)
        result+='\n\n# 本人管理的個人指引：'+str(personal_path)+'\n'
        if personal_path.exists():result+=personal_path.read_text(encoding='utf-8-sig')
    return result
