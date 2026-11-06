"""User-approved shared Windows identity policy (2026-11-03).

Profile/owner separation is application routing, not an OS security boundary.
Native commands use the current non-elevated Windows user's rights; no UAC broker.
"""
from pathlib import Path

PERMISSION_MODE='workspace-write'
from bootstrap import ROOT
SHARED_STORE=ROOT/'shared/skills'


def configure(controller,owner_id,*,root=SHARED_STORE):
    from autonomy_tools import SkillTools
    from shared_skills import SkillStore
    if type(owner_id) is not int or owner_id<=0:raise ValueError('Verified Telegram owner required')
    store=SkillStore(root)
    controller.permission_mode=PERMISSION_MODE
    controller.shared_skills=SkillTools(store,owner_id,enabled=True)
    return store
