"""Explicit owner routing. Existing User A paths stay in place to preserve native sessions."""
import json
from pathlib import Path
from bootstrap import ROOT


def load_profiles(path):
    rows = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    owners, roots = set(), []
    if not isinstance(rows, list) or not rows:
        raise ValueError('User profiles are required')
    for row in rows:
        uid = row['owner_id']
        if type(uid) is not int or uid <= 0 or uid in owners:
            raise ValueError('Duplicate or invalid owner')
        owners.add(uid)
        if not isinstance(row['name'], str) or not row['name'].strip():
            raise ValueError('User name required')
        for key in ('home', 'workspace'):
            p = Path(row[key]).resolve(strict=True)
            if not p.is_dir() or not p.is_relative_to(ROOT.resolve()) or p == ROOT.resolve():
                raise ValueError('Profile must remain inside installation')
            if any(p == old or p.is_relative_to(old) or old.is_relative_to(p) for old in roots):
                raise ValueError('User directories overlap')
            roots.append(p)
            row[key] = p
    return rows
