"""Import routing and exact MCP definitions without rewriting existing owner data."""
import json
import os
from pathlib import Path
import tempfile
import tomllib
from gateway.mcp.presets import Presets

def migrate(members,rows,admin_owner):
    members._paths(rows)
    servers={}
    # Validate all definitions before importing any identity; no token is exported.
    for row in rows:
        path=Path(row['home'])/'codex-home/config.toml'
        if path.is_symlink() or path.is_junction():raise ValueError('Redirected member configuration')
        servers[row['owner_id']]=tomllib.loads(path.read_text(encoding='utf-8-sig')).get('mcp_servers',{})
    members.import_existing(rows,admin_owner)
    policy=Presets(members)
    for row in rows:
        owner=row['owner_id']
        # A later suspension is authoritative, including on a migration retry.
        if members._member(owner)['state']=='active':policy.import_existing(owner,servers[owner])
    return members.active_profiles()

def export_legacy_routes(members,path,compatible_owners):
    """Run only while workers are stopped; never restore historical authorization."""
    path=Path(path)
    if not path.is_absolute() or not path.is_relative_to(members.root):
        raise ValueError('Compatibility export must stay in private deployment')
    for part in (path,*path.parents):
        if part.is_symlink() or part.is_junction():raise ValueError('Redirected compatibility path')
    with members.transaction():
        rows=[dict(r,home=str(r['home']),workspace=str(r['workspace'])) for r in members.active_profiles() if r['owner_id'] in compatible_owners]
        members._paths(rows)
        fd,temporary=tempfile.mkstemp(dir=path.parent,prefix='.routes-',suffix='.tmp')
        try:
            with os.fdopen(fd,'w',encoding='utf-8') as out:
                json.dump(rows,out,ensure_ascii=False,indent=2);out.flush();os.fsync(out.fileno())
            os.replace(temporary,path)
        finally:
            Path(temporary).unlink(missing_ok=True)
    return rows
