"""Versioned shared guidance; approval and publication share one transaction."""
import difflib
import hashlib
import json
import secrets
import time

POLICIES={'admin_only','all_active_members'}

def content_hash(body,policy):
    return hashlib.sha256(json.dumps([body,policy],ensure_ascii=False).encode()).hexdigest()

class SharedPolicy:
    def __init__(self,members):
        self.members=members;self.db=members.db
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS shared_versions (
            version INTEGER PRIMARY KEY, body TEXT NOT NULL, policy TEXT NOT NULL,
            hash TEXT NOT NULL, proposal TEXT UNIQUE, published REAL NOT NULL);
          CREATE TABLE IF NOT EXISTS shared_proposals (
            id TEXT PRIMARY KEY, actor INTEGER NOT NULL, base INTEGER NOT NULL,
            body TEXT NOT NULL, policy TEXT NOT NULL, hash TEXT NOT NULL,
            electorate TEXT NOT NULL, epoch INTEGER NOT NULL, expires REAL NOT NULL, state TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS shared_votes (
            proposal TEXT NOT NULL, owner INTEGER NOT NULL, decision INTEGER NOT NULL,
            PRIMARY KEY(proposal,owner));
        ''')

    def import_initial(self,body,policy='admin_only'):
        if policy not in POLICIES or not isinstance(body,str):raise ValueError('Invalid initial shared policy')
        with self.members.transaction():
            row=self.db.execute('SELECT * FROM shared_versions ORDER BY version DESC LIMIT 1').fetchone()
            if row:
                if row['version']!=1 or row['body']!=body or row['policy']!=policy:
                    raise ValueError('Initial shared policy already imported')
                return
            self.db.execute('INSERT INTO shared_versions VALUES(1,?,?,?,NULL,?)',
                (body,policy,content_hash(body,policy),time.time()))

    def active_persona(self):
        row=self.db.execute('SELECT * FROM shared_versions ORDER BY version DESC LIMIT 1').fetchone()
        if row is None:raise RuntimeError('Shared policy has not been initialized')
        return dict(row)

    def propose(self,actor_id,base_version,body,*,policy=None,now=None):
        if not isinstance(body,str) or len(body.encode('utf-8'))>65536:raise ValueError('Shared guidance exceeds limit')
        with self.members.transaction():
            self.members.require_active(actor_id)
            current=self.active_persona()
            if base_version!=current['version']:raise ValueError('Base revision is stale')
            policy=policy or current['policy']
            if policy not in POLICIES:raise ValueError('Unknown approval policy')
            electorate=[r['owner_id'] for r in self.members.active_profiles()]
            proposal=secrets.token_hex(16);digest=content_hash(body,policy)
            self.db.execute('INSERT INTO shared_proposals VALUES(?,?,?,?,?,?,?,?,?,?)',
                (proposal,actor_id,base_version,body,policy,digest,json.dumps(electorate),self.members.epoch,
                 (time.time() if now is None else now)+604800,'pending'))
            self.members._audit(actor_id,actor_id,'shared_proposal',proposal)
            return self.view(actor_id,proposal)

    def view(self,actor_id,proposal_id):
        self.members.require_active(actor_id)
        row=self.db.execute('SELECT * FROM shared_proposals WHERE id=?',(proposal_id,)).fetchone()
        if row is None:raise ValueError('Unknown proposal')
        result=dict(row)
        base=self.db.execute('SELECT body FROM shared_versions WHERE version=?',(row['base'],)).fetchone()
        result['diff']=''.join(difflib.unified_diff(base['body'].splitlines(True),row['body'].splitlines(True),fromfile='current',tofile='proposed'))
        result['votes']=[dict(v) for v in self.db.execute('SELECT owner,decision FROM shared_votes WHERE proposal=?',(proposal_id,))]
        result['membership_changes']=[dict(r) for r in self.db.execute("SELECT actor,subject,action,reason FROM member_audit WHERE action IN ('suspend','restore','activate') ORDER BY id DESC LIMIT 20")]
        return result

    def pending(self,actor_id):
        self.members.require_active(actor_id)
        return [self.view(actor_id,r[0]) for r in self.db.execute("SELECT id FROM shared_proposals WHERE state='pending' ORDER BY expires")]

    def vote(self,actor_id,proposal_id,content_hash,decision,*,now=None):
        if type(decision) is not bool:raise ValueError('Explicit approve/reject required')
        with self.members.transaction():
            actor=self.members.require_active(actor_id)
            proposal=self.db.execute('SELECT * FROM shared_proposals WHERE id=?',(proposal_id,)).fetchone()
            current=self.active_persona()
            if not proposal or proposal['state']!='pending' or proposal['hash']!=content_hash:
                raise ValueError('Proposal is changed or no longer pending')
            if proposal['expires'] <= (time.time() if now is None else now) or proposal['epoch']!=self.members.epoch or proposal['base']!=current['version']:
                raise ValueError('Proposal expired or membership/base changed; submit again')
            electorate=json.loads(proposal['electorate'])
            if actor_id not in electorate or (current['policy']=='admin_only' and actor['role']!='admin'):
                raise PermissionError('Actor is not an eligible voter')
            if self.db.execute('SELECT 1 FROM shared_votes WHERE proposal=? AND owner=?',(proposal_id,actor_id)).fetchone():
                raise ValueError('Vote already recorded')
            self.db.execute('INSERT INTO shared_votes VALUES(?,?,?)',(proposal_id,actor_id,int(decision)))
            if not decision:
                self.db.execute("UPDATE shared_proposals SET state='rejected' WHERE id=?",(proposal_id,))
                return current
            count=self.db.execute('SELECT count(*) FROM shared_votes WHERE proposal=? AND decision=1',(proposal_id,)).fetchone()[0]
            needed=1 if current['policy']=='admin_only' else len(electorate)
            if count==needed:
                self.db.execute('INSERT INTO shared_versions VALUES(?,?,?,?,?,?)',
                    (current['version']+1,proposal['body'],proposal['policy'],proposal['hash'],proposal_id,time.time()))
                self.db.execute("UPDATE shared_proposals SET state='published' WHERE id=?",(proposal_id,))
            self.members._audit(actor_id,actor_id,'shared_vote',proposal_id)
            return self.active_persona()
