"""Single-host adapters for resources, dispatch, review, and local publication.

These are local components, not OpenViking or a remote Runtime integration.
"""
import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager

from framework import Conflict, Store
from identity import AuthenticatedReviewerProvider


def digest(data):
    return hashlib.sha256(data).hexdigest()


class Foundations:
    def __init__(self, path, authorizer: AuthenticatedReviewerProvider | None = None):
        self.authorizer = authorizer
        self.store = Store(path)
        self.db = self.store.db
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS resources (
          job_id TEXT NOT NULL, name TEXT NOT NULL, version INTEGER NOT NULL,
          content BLOB NOT NULL, sha256 TEXT NOT NULL,
          PRIMARY KEY(job_id,name,version));
        CREATE TABLE IF NOT EXISTS reviews (
          job_id TEXT PRIMARY KEY, artifact_name TEXT NOT NULL, artifact_version INTEGER NOT NULL,
          artifact_sha256 TEXT NOT NULL, report_sha256 TEXT NOT NULL, policy_version TEXT NOT NULL,
          reviewer TEXT NOT NULL, decision TEXT NOT NULL, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS publications (
          job_id TEXT PRIMARY KEY, artifact_sha256 TEXT NOT NULL, content BLOB NOT NULL, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS dispatch (
          task_id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
          state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, lease_token TEXT, lease_until REAL,
          result TEXT, error TEXT);
        ''')

    @contextmanager
    def transaction(self):
        # Acquire the write reservation before any reads used for a decision.
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def _authorize(self, credential, action, job_id):
        # The adapter is a trusted integration boundary, not caller-supplied identity.
        if self.authorizer is None:
            raise PermissionError('authorization adapter required')
        principal = self.authorizer.authorize(credential, action, job_id)
        if not isinstance(principal, str) or not principal.strip():
            raise PermissionError('authorization denied')
        return principal

    def close(self):
        self.store.close()

    def write_resource(self, job_id, name, content, expected_version=0):
        if not name or not isinstance(content, bytes):
            raise ValueError('resource name and bytes required')
        with self.transaction():
            self.store.get(job_id)
            current = self.db.execute('SELECT MAX(version) FROM resources WHERE job_id=? AND name=?', (job_id, name)).fetchone()[0] or 0
            if current != expected_version:
                raise Conflict('stale resource version')
            version = current + 1
            self.db.execute('INSERT INTO resources VALUES (?,?,?,?,?)', (job_id, name, version, content, digest(content)))
        return version

    def read_resource(self, job_id, name, version=None):
        if version is None:
            row = self.db.execute('SELECT version,content,sha256 FROM resources WHERE job_id=? AND name=? ORDER BY version DESC LIMIT 1', (job_id,name)).fetchone()
        else:
            row = self.db.execute('SELECT version,content,sha256 FROM resources WHERE job_id=? AND name=? AND version=?', (job_id,name,version)).fetchone()
        if row is None:
            raise KeyError((job_id, name, version))
        if digest(row[1]) != row[2]:
            raise ValueError('resource digest mismatch')
        return {'version': row[0], 'content': row[1], 'sha256': row[2]}

    def kanban(self):
        return [self.store.get(row[0]) for row in self.db.execute('SELECT job_id FROM jobs ORDER BY job_id').fetchall()]

    def review(self, job_id, revision, artifact_name, artifact_version, report_sha256, policy_version, credential, decision, validate=None):
        if decision not in ('approved', 'rejected') or not policy_version or len(report_sha256) != 64 or any(c not in '0123456789abcdef' for c in report_sha256):
            raise ValueError('review identity, decision, report digest and policy required')
        with self.transaction():
            reviewer = self._authorize(credential, 'review', job_id)
            job = self.store.get(job_id)
            if job['revision'] != revision:
                raise Conflict('stale revision')
            if job['state'] != 'awaiting_review':
                raise ValueError('job is not awaiting review')
            artifact = self.read_resource(job_id, artifact_name, artifact_version)
            if self.read_resource(job_id, artifact_name)['version'] != artifact_version:
                raise Conflict('artifact version changed')
            report = self.read_resource(job_id, 'report.json')
            if report['sha256'] != report_sha256:
                raise Conflict('report digest mismatch')
            if validate is not None:
                validate(job, artifact, report)
            self.db.execute('INSERT INTO reviews VALUES (?,?,?,?,?,?,?,?,?)', (job_id, artifact_name, artifact_version, artifact['sha256'], report_sha256, policy_version, reviewer, decision, time.time()))
            self.store._transition(job_id, revision, decision)

    def publish_local(self, job_id, revision, credential):
        with self.transaction():
            self._authorize(credential, 'publish', job_id)
            job = self.store.get(job_id)
            existing = self.db.execute('SELECT artifact_sha256 FROM publications WHERE job_id=?', (job_id,)).fetchone()
            if existing and job['state'] == 'published':
                return existing[0]
            if job['revision'] != revision:
                raise Conflict('stale revision')
            if job['state'] != 'approved':
                raise ValueError('approval required')
            review = self.db.execute('SELECT artifact_name,artifact_version,artifact_sha256,report_sha256 FROM reviews WHERE job_id=? AND decision=?', (job_id,'approved')).fetchone()
            if review is None:
                raise ValueError('approval record missing')
            artifact = self.read_resource(job_id, review[0], review[1])
            latest = self.read_resource(job_id, review[0])
            if artifact['sha256'] != review[2] or latest['version'] != review[1]:
                raise Conflict('approved artifact changed')
            report = self.read_resource(job_id, 'report.json')
            if report['sha256'] != review[3]:
                raise Conflict('approved report changed')
            self.db.execute('INSERT INTO publications VALUES (?,?,?,?)', (job_id, artifact['sha256'], artifact['content'], time.time()))
            self.store._transition(job_id, revision, 'published')
            return artifact['sha256']

    def enqueue(self, task_id, kind, payload):
        body = json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False)
        h = digest(body.encode())
        with self.db:
            row = self.db.execute('SELECT kind,payload_sha256 FROM dispatch WHERE task_id=?', (task_id,)).fetchone()
            if row:
                if row != (kind,h):
                    raise Conflict('task id reused with different request')
                return
            self.db.execute('INSERT INTO dispatch(task_id,kind,payload,payload_sha256,state) VALUES (?,?,?,?,?)', (task_id,kind,body,h,'queued'))

    def retry_failed(self, task_id):
        """Explicitly requeue a failed task without changing its stable payload."""
        with self.db:
            row = self.db.execute('SELECT state FROM dispatch WHERE task_id=?', (task_id,)).fetchone()
            if row is None:
                raise KeyError(task_id)
            if row[0] != 'failed':
                raise Conflict('only failed tasks can be retried')
            self.db.execute("UPDATE dispatch SET state='queued',result=NULL,error=NULL,lease_token=NULL,lease_until=NULL WHERE task_id=? AND state='failed'", (task_id,))

    def run_one(self, handlers, lease_seconds=60):
        if lease_seconds <= 0:
            raise ValueError('lease must be positive')
        now = time.time()
        token = str(uuid.uuid4())
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row = self.db.execute("SELECT task_id,kind,payload FROM dispatch WHERE state='queued' OR (state='running' AND lease_until<=?) ORDER BY rowid LIMIT 1", (now,)).fetchone()
            if row is None:
                self.db.commit()
                return None
            self.db.execute("UPDATE dispatch SET state='running',attempts=attempts+1,lease_token=?,lease_until=? WHERE task_id=?", (token, now+lease_seconds,row[0]))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        try:
            result = json.dumps(handlers[row[1]](json.loads(row[2])), allow_nan=False)
            state, error = 'succeeded', None
        except Exception as exc:
            result, state, error = None, 'failed', str(exc)
        with self.db:
            changed = self.db.execute('UPDATE dispatch SET state=?,result=?,error=?,lease_token=NULL,lease_until=NULL WHERE task_id=? AND state=? AND lease_token=?', (state,result,error,row[0],'running',token)).rowcount
            if not changed:
                raise Conflict('task lease lost; result not committed')
        return row[0], state
