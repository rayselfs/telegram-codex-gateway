"""Transport metadata only: no messages, prompts, or conversation history."""
import json
from pathlib import Path, PureWindowsPath
import sqlite3
import uuid


def safe_file(root, relative):
    p = PureWindowsPath(relative)
    if not relative or p.is_absolute() or p.drive or '..' in p.parts or ':' in relative:
        raise ValueError('Use a relative path inside workspace')
    root = Path(root).resolve(strict=True)
    target = (root / relative).resolve(strict=True)
    if not target.is_relative_to(root) or not target.is_file():
        raise ValueError('File is outside workspace or is not a file')
    return target


def snapshot_file(root, relative, outbox):
    target = safe_file(root, relative)
    outbox.mkdir(parents=True, exist_ok=True)
    dest = outbox / (uuid.uuid4().hex + target.suffix[:16])
    with target.open('rb') as source:
        # Check the opened handle so a concurrent junction swap cannot export another path.
        from gateway.platforms import current
        actual = current().opened_path(source)
        if not Path(actual).resolve().is_relative_to(Path(root).resolve()):
            raise ValueError('Opened file escaped workspace')
        try:
            with dest.open('xb') as output:
                total = 0
                while chunk := source.read(1024 * 1024):
                    total += len(chunk)
                    if total > 49 * 1024 * 1024:
                        raise ValueError('File exceeds upload limit')
                    output.write(chunk)
        except Exception:
            dest.unlink(missing_ok=True)
            raise
    return dest, target.name


class Journal:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, at REAL NOT NULL, kind TEXT NOT NULL, detail TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS outbound_chunks (batch TEXT, part INTEGER, ref TEXT, digest TEXT, state TEXT, receipt INTEGER, PRIMARY KEY(batch,part));
          CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS updates (id INTEGER PRIMARY KEY, state TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS jobs (thread TEXT PRIMARY KEY, turn TEXT, state TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS delivery (key TEXT PRIMARY KEY, state TEXT NOT NULL, receipts TEXT);
        ''')
        self.db.execute("UPDATE jobs SET state='unknown' WHERE state IN ('running','dispatching','compacting')")
        self.db.execute("UPDATE delivery SET state='unknown' WHERE state='pending'")
        self.db.execute("UPDATE outbound_chunks SET state='unknown' WHERE state='sending'")
        self.db.commit()

    def audit(self, kind, detail):
        import time
        with self.db:self.db.execute('INSERT INTO audit(at,kind,detail) VALUES(?,?,?)',
            (time.time(),kind,json.dumps(detail,ensure_ascii=False)))

    def chunk(self, batch, part, ref, digest, state, receipt=None):
        with self.db:self.db.execute('INSERT OR REPLACE INTO outbound_chunks VALUES(?,?,?,?,?,?)',
            (batch,part,ref,digest,state,receipt))

    def get(self, key, default=None):
        r = self.db.execute('SELECT value FROM kv WHERE key=?', (key,)).fetchone()
        return json.loads(r[0]) if r else default

    def set(self, key, value):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO kv VALUES (?,?)', (key, json.dumps(value)))

    def receive(self, uid, *, state='received'):
        with self.db:
            return self.db.execute("INSERT OR IGNORE INTO updates VALUES (?,?)", (uid, state)).rowcount == 1

    def job(self, thread, turn, state):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO jobs VALUES (?,?,?)', (thread, turn, state))

    def delivery(self, key, state, receipts=None):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO delivery VALUES (?,?,?)',
                            (key, state, json.dumps(receipts or [])))

    def delivered(self, key):
        return self.db.execute("SELECT 1 FROM delivery WHERE key=? AND state='delivered'", (key,)).fetchone() is not None

    def close(self):
        self.db.close()
