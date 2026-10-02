from __future__ import annotations
import json
import sqlite3
import time
import uuid
from pathlib import Path

ACTIVE = ('queued', 'running', 'waiting_approval')
TERMINAL = ('completed', 'failed', 'cancelled', 'interrupted')

class Store:
    """Small synchronous SQLite operations, called only on the event-loop thread.

    WAL and transactions protect persisted state. Run exactly one scheduler process.
    A process lock in app.py prevents competing control planes using the same DB.
    """
    def __init__(self, path: Path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        PRAGMA journal_mode=WAL;
        PRAGMA busy_timeout=5000;
        CREATE TABLE IF NOT EXISTS jobs(
          id TEXT PRIMARY KEY, kind TEXT NOT NULL, lane TEXT NOT NULL,
          payload TEXT NOT NULL, status TEXT NOT NULL, progress REAL NOT NULL DEFAULT 0,
          result TEXT, output TEXT NOT NULL DEFAULT '{}', error TEXT,
          created REAL NOT NULL, updated REAL NOT NULL, priority INTEGER NOT NULL DEFAULT 0,
          run_after REAL NOT NULL DEFAULT 0, request_key TEXT UNIQUE);
        CREATE INDEX IF NOT EXISTS queue_idx ON jobs(lane,status,priority,created);
        CREATE TABLE IF NOT EXISTS events(
          seq INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, type TEXT NOT NULL,
          data TEXT NOT NULL, ts REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS event_job_idx ON events(job_id,seq);
        CREATE TABLE IF NOT EXISTS objects(
          category TEXT NOT NULL, id TEXT NOT NULL, value TEXT NOT NULL,
          PRIMARY KEY(category,id));
        ''')
        self.db.commit()
    @staticmethod
    def decode(row):
        if row is None: return None
        out = dict(row)
        for field in ('payload', 'result', 'output', 'data'):
            if field in out and out[field] is not None: out[field] = json.loads(out[field])
        return out
    def create(self, kind, lane, payload, *, priority=0, run_after=0, request_key=None):
        if request_key:
            row = self.db.execute('SELECT * FROM jobs WHERE request_key=?', (request_key,)).fetchone()
            if row:
                old = self.decode(row)
                if old['kind'] != kind or old['payload'] != payload:
                    raise ValueError('Idempotency key already used for a different request')
                return old
        now, ident = time.time(), uuid.uuid4().hex
        with self.db:
            self.db.execute('INSERT INTO jobs(id,kind,lane,payload,status,created,updated,priority,run_after,request_key) VALUES(?,?,?,?,?,?,?,?,?,?)',
                (ident, kind, lane, json.dumps(payload), 'queued', now, now, priority, run_after, request_key))
        return self.get(ident)
    def get(self, ident):
        return self.decode(self.db.execute('SELECT * FROM jobs WHERE id=?', (ident,)).fetchone())
    def jobs(self, limit=100, chat_id=None):
        if chat_id:
            rows = self.db.execute("SELECT * FROM jobs WHERE kind='chat' AND json_extract(payload,'$.chat_id')=? ORDER BY created DESC LIMIT ?", (chat_id, limit)).fetchall()
        else:
            rows = self.db.execute('SELECT * FROM jobs ORDER BY created DESC LIMIT ?', (limit,)).fetchall()
        return [self.decode(row) for row in rows]
    def active(self):
        return [self.decode(r) for r in self.db.execute("SELECT * FROM jobs WHERE status IN ('queued','running','waiting_approval') ORDER BY created")]
    def change(self, ident, **values):
        allowed = {'status','progress','result','output','error','priority','run_after'}
        if not values or not set(values) <= allowed: raise ValueError('Invalid job fields')
        values['updated'] = time.time()
        for key in ('result', 'output'):
            if key in values: values[key] = json.dumps(values[key])
        sql = ','.join(f'{k}=?' for k in values)
        with self.db:
            self.db.execute(f'UPDATE jobs SET {sql} WHERE id=?', (*values.values(), ident))
        return self.get(ident)
    def claim(self, lane):
        with self.db:
            row = self.db.execute("SELECT id FROM jobs WHERE lane=? AND status='queued' AND run_after<=? ORDER BY priority DESC,created LIMIT 1", (lane,time.time())).fetchone()
            if not row: return None
            self.db.execute("UPDATE jobs SET status='running',updated=? WHERE id=? AND status='queued'", (time.time(), row['id']))
        return self.get(row['id'])
    def recover(self):
        rows = self.db.execute("SELECT id FROM jobs WHERE status IN ('running','waiting_approval')").fetchall()
        for row in rows:
            self.change(row['id'], status='interrupted', error='Server stopped during execution. Review side effects before retrying.')
        return [r['id'] for r in rows]
    def event(self, ident, kind, data):
        now = time.time()
        with self.db:
            cur = self.db.execute('INSERT INTO events(job_id,type,data,ts) VALUES(?,?,?,?)', (ident,kind,json.dumps(data),now))
        return {'seq':cur.lastrowid, 'job_id':ident, 'type':kind, 'data':data, 'ts':now}
    def events(self, after=0, limit=500, job_id=None):
        sql, args = 'SELECT * FROM events WHERE seq>?', [after]
        if job_id: sql += ' AND job_id=?'; args.append(job_id)
        sql += ' ORDER BY seq LIMIT ?'; args.append(min(limit,1000))
        return [self.decode(r) for r in self.db.execute(sql,args)]
    def cursor(self):
        return self.db.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0]
    def put(self, category, ident, value):
        with self.db:
            self.db.execute('INSERT INTO objects VALUES(?,?,?) ON CONFLICT(category,id) DO UPDATE SET value=excluded.value', (category,ident,json.dumps(value)))
        return value
    def object(self, category, ident, default=None):
        row = self.db.execute('SELECT value FROM objects WHERE category=? AND id=?', (category,ident)).fetchone()
        return json.loads(row[0]) if row else default
    def objects(self, category):
        return [json.loads(r[0]) for r in self.db.execute('SELECT value FROM objects WHERE category=? ORDER BY id', (category,))]
    def delete(self, category, ident):
        with self.db: self.db.execute('DELETE FROM objects WHERE category=? AND id=?', (category,ident))
    def close(self): self.db.close()
