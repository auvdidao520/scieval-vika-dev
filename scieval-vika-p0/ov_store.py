"""OpenViking-backed job state for a single-host, single-writer P0 deployment."""
from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path

from framework import Conflict, TRANSITIONS

JOB_ID = re.compile(r'^[A-Za-z0-9_-]{1,64}$')


class OvError(RuntimeError):
    pass


class OvStore:
    """Persist one JSON document per job below an OV resource URI.

    State transitions and their outbox records are written in the same document.
    A local flock serializes writers. OV content/write has no generic CAS
    contract, so this adapter must not be shared by multiple hosts or writers.
    """

    def __init__(self, root_uri: str, lock_dir: str, *, runner=None, ov_bin: str = 'ov', timeout: float = 30.0):
        if not root_uri.startswith('viking://') or '..' in root_uri.split('/'):
            raise ValueError('invalid OV root URI')
        self.root_uri = root_uri.rstrip('/')
        self.lock_dir = Path(lock_dir)
        self.lock_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.runner = runner or self._run
        self.ov_bin = ov_bin
        self.timeout = timeout

    def _run(self, args: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            [self.ov_bin, *args], capture_output=True, text=True,
            timeout=self.timeout, check=False,
        )

    def _uri(self, job_id: str) -> str:
        if not JOB_ID.fullmatch(job_id):
            raise ValueError('invalid job id')
        return f'{self.root_uri}/{job_id}/state.json'

    @contextmanager
    def _lock(self, job_id: str):
        if not JOB_ID.fullmatch(job_id):
            raise ValueError('invalid job id')
        path = self.lock_dir / f'{job_id}.lock'
        with path.open('a+b') as stream:
            os.chmod(path, 0o600)
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _read(self, job_id: str) -> dict:
        result = self.runner(['read', self._uri(job_id)])
        if result.returncode != 0:
            raise OvError(f'ov read failed: {result.stderr[-500:]}')
        try:
            state = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise OvError('OV state is not valid JSON') from error
        if state.get('job_id') != job_id or not isinstance(state.get('revision'), int):
            raise OvError('OV state identity is invalid')
        return state

    def _write(self, job_id: str, state: dict, *, create: bool = False) -> None:
        content = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n'
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode='w', encoding='utf-8', dir=self.lock_dir,
                prefix=f'.{job_id}-', suffix='.json', delete=False,
            ) as stream:
                temporary = Path(stream.name)
                stream.write(content)
            os.chmod(temporary, 0o600)
            result = self.runner([
                'write', self._uri(job_id), '--from-file', str(temporary),
                '--mode', 'create' if create else 'replace', '--wait',
            ])
            if result.returncode != 0:
                raise OvError(f'ov write failed: {result.stderr[-500:]}')
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def create(self, job_id: str) -> None:
        with self._lock(job_id):
            state = {
                'schema_version': 1,
                'job_id': job_id,
                'state': 'received',
                'revision': 0,
                'round': 1,
                'outbox': [],
            }
            self._write(job_id, state, create=True)

    def get(self, job_id: str) -> dict:
        state = self._read(job_id)
        # Preserve the original Store's small public shape for the orchestrator.
        return {key: state[key] for key in ('job_id', 'state', 'revision', 'round')}

    def transition(self, job_id: str, revision: int, target: str) -> dict:
        with self._lock(job_id):
            state = self._read(job_id)
            if state['revision'] != revision:
                raise Conflict('stale revision')
            if target not in TRANSITIONS[state['state']]:
                raise ValueError('invalid transition')
            previous = state['state']
            next_round = state['round'] + int(previous == 'revising' and target == 'evaluating')
            next_revision = revision + 1
            event = {
                'event_id': f'{job_id}:transition:{next_revision}',
                'job_id': job_id,
                'revision': next_revision,
                'from': previous,
                'to': target,
                'round': next_round,
            }
            state.update(state=target, revision=next_revision, round=next_round)
            state.setdefault('outbox', []).append({**event, 'delivered': False})
            self._write(job_id, state)
            persisted = self._read(job_id)
            if persisted['revision'] != next_revision or not any(
                item['event_id'] == event['event_id'] for item in persisted.get('outbox', [])
            ):
                raise OvError('OV transition read-back verification failed')
            return event

    def pending(self, job_id: str) -> list[dict]:
        state = self._read(job_id)
        return [{key: value for key, value in event.items() if key != 'delivered'}
                for event in state.get('outbox', []) if not event.get('delivered')]

    def mark_delivered(self, job_id: str, event_id: str) -> None:
        with self._lock(job_id):
            state = self._read(job_id)
            matches = [event for event in state.get('outbox', []) if event['event_id'] == event_id]
            if not matches:
                raise KeyError(event_id)
            if matches[0].get('delivered'):
                return
            matches[0]['delivered'] = True
            self._write(job_id, state)

    def close(self) -> None:
        return None
