import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from gateway.core.members import Members

class MigrationTests(unittest.TestCase):
    def test_twice_preserves_all_owner_data_and_rollback_uses_latest_authorization(self):
        from gateway.core.migration import migrate, export_legacy_routes
        from gateway.mcp.presets import Presets
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);rows=[];files=[]
            for owner in (101,102):
                home=root/str(owner)/'profile';workspace=root/str(owner)/'workspace'
                (home/'codex-home').mkdir(parents=True);workspace.mkdir()
                rows.append(dict(owner_id=owner,name=str(owner),home=str(home),workspace=str(workspace)))
                config=home/'codex-home/config.toml'
                config.write_text('[mcp_servers.existing]\ncommand="unchanged-entry"\nargs=["--owner", "'+str(owner)+'"]\n',encoding='utf-8')
                (home/'codex-home/auth.json').write_text('{"synthetic":"owner-'+str(owner)+'"}')
                db=sqlite3.connect(home/'transport.sqlite')
                db.execute('CREATE TABLE kv(key TEXT PRIMARY KEY,value TEXT)')
                for key in ('thread','mode','model','effort','unknown','recovery','personal_memories','skill_pins','mcp_trust'):
                    db.execute('INSERT INTO kv VALUES(?,?)',(key,json.dumps({'original':key,'owner':owner})))
                db.commit();db.close()
                db=sqlite3.connect(home/'schedules.sqlite')
                db.executescript("CREATE TABLE tasks(id TEXT,next_due INTEGER);INSERT INTO tasks VALUES('original-task',123);CREATE TABLE receipts(id TEXT,state TEXT);INSERT INTO receipts VALUES('original-receipt','unknown');")
                db.close()
                (workspace/'same-name.txt').write_text(str(owner))
                files.extend(p for p in (root/str(owner)).rglob('*') if p.is_file())
            before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
            members=Members(root/'members.sqlite',root)
            try:
                migrate(members,rows,101);migrate(members,rows,101)
                self.assertEqual(before,{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files})
                self.assertEqual([r['owner_id'] for r in members.active_profiles()],[101,102])
                policy=Presets(members)
                self.assertTrue(policy.allowed(102,'existing',{'command':'unchanged-entry','args':['--owner','102']}))
                self.assertFalse(policy.allowed(102,'existing',{'command':'new-entry','args':['--owner','102']}))
                members.suspend(101,102,'latest decision')
                migrate(members,rows,101)
                with self.assertRaises(PermissionError):members.require_active(102)
                result=export_legacy_routes(members,root/'rollback-users.json',{101,102})
                self.assertEqual([r['owner_id'] for r in result],[101])
                self.assertEqual(before,{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files})
            finally:members.close()
