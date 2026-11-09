"""Consent-only MCP forms. Credentials and arbitrary data forms never enter chat."""
def choices(params):
    if not isinstance(params.get('serverName'),str) or not params['serverName']:
        raise ValueError('Unknown MCP consent source')
    if params.get('mode') not in ('form','openai/form','openaiForm'):
        raise ValueError('This service requires its dedicated external login flow')
    schema=params.get('requestedSchema') or {}
    if schema.get('type')!='object':raise ValueError('Unsupported MCP form')
    properties=schema.get('properties',{})
    if not isinstance(properties,dict) or len(properties)>1:raise ValueError('Only consent forms are supported')
    content={}
    for name,field in properties.items():
        if not isinstance(field,dict) or field.get('type')!='boolean':raise ValueError('Data and credential forms must use an external flow')
        content[name]=True
    if any(name not in content for name in schema.get('required',[])):raise ValueError('Unknown required fields')
    return [('允許這次操作',{'action':'accept','content':content}),('拒絕這次操作',{'action':'decline'})]


def configured(personal_instructions):
    """Bind trust to this owner's configured service definition, without exposing it."""
    import hashlib,json,tomllib
    from pathlib import Path
    if not personal_instructions:return {}
    path=Path(personal_instructions).parent/'config.toml'
    try:servers=tomllib.loads(path.read_text(encoding='utf-8-sig')).get('mcp_servers',{})
    except (OSError,ValueError):return {}
    return {name:hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()
            for name,value in servers.items() if value.get('enabled',True)}

def trusted(controller,params):
    name=params.get('serverName')
    fingerprint=configured(controller.personal_instructions).get(name)
    saved=controller.journal.get('mcp_trust',{})
    if not controller.interactive:
        import sqlite3,json
        from pathlib import Path
        path=Path(controller.personal_instructions).parent.parent/'transport.sqlite'
        try:
            with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
                row=db.execute("SELECT value FROM kv WHERE key='mcp_trust'").fetchone()
                saved=json.loads(row[0]) if row else {}
        except (OSError,ValueError,sqlite3.Error):return False
    return bool(fingerprint and saved.get(name)==fingerprint)
