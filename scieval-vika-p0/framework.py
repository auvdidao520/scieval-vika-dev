"""P0 domain scaffold. No external execution or automatic publication."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

CRITERIA = frozenset(('I.A', 'I.B.1', 'I.B.2', 'I.B.3', 'I.C', 'II.A', 'II.B', 'II.C', 'III.A', 'III.B', 'III.C', 'III.D', 'III.E'))
TRANSITIONS = {
    'received': {'preparing', 'awaiting_review'},
    'preparing': {'evaluating', 'awaiting_review'},
    'evaluating': {'revising', 'awaiting_review'},
    'revising': {'evaluating', 'awaiting_review'},
    'awaiting_review': {'approved', 'rejected'},
    'approved': {'published'},
    'rejected': set(), 'published': set(),
}

class Conflict(ValueError):
    pass

class Evaluator(Protocol):
    def submit(self, request: dict, idempotency_key: str) -> str: ...
    def query(self, evaluation_id: str) -> dict: ...

@dataclass(frozen=True)
class Snapshot:
    uri: str
    sha256: str


def snapshot(path: Path, content: bytes) -> Snapshot:
    """Exclusive creation avoids overwriting a round; caller supplies a trusted path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(content).hexdigest()
    try:
        with path.open('xb') as stream:
            stream.write(content)
    except FileExistsError:
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise Conflict('round artifact already exists with different content')
    return Snapshot(str(path), digest)


def validate_report(report: dict, *, job_id: str, round_number: int, input_sha256: str) -> None:
    """Validate identity and structural completeness, not educational correctness."""
    required = ('schema_version', 'evaluation_id', 'job_id', 'round', 'input_uri', 'input_sha256', 'rubric_id', 'rubric_version', 'policy_version', 'evaluator_version', 'created_at', 'criteria', 'decision')
    if any(key not in report for key in required):
        raise ValueError('missing report fields')
    if (report['job_id'], report['round'], report['input_sha256']) != (job_id, round_number, input_sha256):
        raise ValueError('report identity mismatch')
    criteria = report['criteria']
    if not isinstance(criteria, list) or any(not isinstance(item, dict) for item in criteria):
        raise ValueError('criteria must be objects')
    ids = [item.get('criterion_id') for item in criteria]
    if len(ids) != 13 or set(ids) != CRITERIA:
        raise ValueError('expected exactly 13 unique criteria')
    for item in criteria:
        if any(key not in item for key in ('status', 'score', 'evidence', 'reasoning', 'recommendations')):
            raise ValueError('missing criterion fields')
        status, score = item['status'], item['score']
        if status not in ('scored', 'not_observable', 'error'):
            raise ValueError('invalid criterion status')
        if status == 'scored':
            if type(score) is not int or not 0 <= score <= 3:
                raise ValueError('invalid score')
        elif score is not None:
            raise ValueError('unscored criterion must have null score')
        if not isinstance(item['evidence'], list) or not isinstance(item['recommendations'], list):
            raise ValueError('evidence and recommendations must be lists')
    decision = report['decision']
    if not isinstance(decision, dict) or decision.get('result') not in ('pass', 'revise', 'human_review') or not decision.get('reason'):
        raise ValueError('invalid decision')
    if decision['result'] == 'pass' and any(item['status'] != 'scored' for item in criteria):
        raise ValueError('incomplete report cannot pass')


class Store:
    def __init__(self, path: str):
        self.db = sqlite3.connect(path)
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS jobs (job_id TEXT PRIMARY KEY, state TEXT NOT NULL, revision INTEGER NOT NULL, round INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS outbox (event_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, revision INTEGER NOT NULL, payload TEXT NOT NULL, delivered INTEGER NOT NULL DEFAULT 0, UNIQUE(job_id, revision));
        ''')

    def create(self, job_id: str) -> None:
        with self.db:
            self.db.execute('INSERT INTO jobs VALUES (?, ?, 0, 1)', (job_id, 'received'))

    def get(self, job_id: str) -> dict:
        row = self.db.execute('SELECT job_id, state, revision, round FROM jobs WHERE job_id=?', (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return dict(zip(('job_id', 'state', 'revision', 'round'), row))

    def transition(self, job_id: str, revision: int, target: str) -> dict:
        with self.db:
            job = self.get(job_id)
            if job['revision'] != revision:
                raise Conflict('stale revision')
            if target not in TRANSITIONS[job['state']]:
                raise ValueError('invalid transition')
            # Moving from revising to evaluating starts the next immutable round.
            next_round = job['round'] + int(job['state'] == 'revising' and target == 'evaluating')
            changed = self.db.execute('UPDATE jobs SET state=?, revision=revision+1, round=? WHERE job_id=? AND revision=?', (target, next_round, job_id, revision)).rowcount
            if changed != 1:
                raise Conflict('stale revision')
            event = {'event_id': str(uuid.uuid4()), 'job_id': job_id, 'revision': revision + 1, 'from': job['state'], 'to': target, 'round': next_round}
            self.db.execute('INSERT INTO outbox(event_id, job_id, revision, payload) VALUES (?, ?, ?, ?)', (event['event_id'], job_id, revision + 1, json.dumps(event)))
            return event

    def pending(self) -> list[dict]:
        return [json.loads(row[0]) for row in self.db.execute('SELECT payload FROM outbox WHERE delivered=0 ORDER BY rowid')]

    def close(self) -> None:
        self.db.close()
