"""Durable, append-only local recovery index for trusted accepted inputs.

Register before creating the job state. A missing state is reported by the
scheduler and retried, never silently excluded. This index is not an event
ledger: only a trusted ingress that has authenticated and durably accepted the
origin may register it. All writers must share this SQLite database on a local
filesystem; it is not a multi-host coordination service.
"""
import sqlite3
import threading

from framework import Conflict
from ov_store import JOB_ID


class RecoveryRegistry:
    def __init__(self, path):
        self._lock = threading.RLock()
        self.db = sqlite3.connect(path, timeout=30, check_same_thread=False)
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS recovery_jobs ('
                        'job_id TEXT PRIMARY KEY, origin_seq INTEGER NOT NULL CHECK(origin_seq > 0))')
        self.db.commit()

    def register(self, job_id, origin_seq):
        if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id):
            raise ValueError('invalid job id')
        if type(origin_seq) is not int or not 0 < origin_seq <= 2**63 - 1:
            raise ValueError('invalid accepted origin sequence')
        with self._lock, self.db:
            self.db.execute('INSERT OR IGNORE INTO recovery_jobs VALUES (?, ?)', (job_id, origin_seq))
            row = self.db.execute('SELECT origin_seq FROM recovery_jobs WHERE job_id=?', (job_id,)).fetchone()
            if row[0] != origin_seq:
                raise Conflict('job origin sequence cannot change')

    def list_jobs(self):
        with self._lock:
            return self.db.execute('SELECT job_id, origin_seq FROM recovery_jobs ORDER BY job_id').fetchall()

    def close(self):
        with self._lock:
            self.db.close()
