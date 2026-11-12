"""Manual, approval-gated OV submission. The P1 database and process are trusted."""
import base64
import json
import re
import subprocess
import time
import uuid
from pathlib import Path

from framework import Conflict

_ROOT = re.compile(r'^viking://(?:resources|user/[^/]+/resources)(?:/[^/]+)+$')
_MAX_BYTES = 20 * 1024 * 1024


def _root(value):
    if (not isinstance(value, str) or not _ROOT.fullmatch(value)
            or re.search(r'[\s%\\?#\x00-\x1f\x7f]', value)
            or any(part in ('', '.', '..') for part in value[9:].split('/'))):
        raise ValueError('explicit canonical resource root required')
    if re.match(r'^viking://user/[^/]+/resources/(?:vika|emails|meetings)(?:/|$)', value):
        raise ValueError('reserved resource root')
    return value


class NodeOVTransport:
    """Use Vika's configured OV identity. Errors are deliberately sanitized."""
    def __init__(self, config_path, node='node', bridge=None, timeout=90):
        if not isinstance(config_path, str) or not Path(config_path).is_absolute():
            raise ValueError('absolute OV config path required')
        self.config_path, self.node, self.timeout = config_path, node, timeout
        self.bridge = bridge or str(Path(__file__).resolve().parent.parent / 'vika/packages/daemon/src/scieval/ov-publish-bridge.mjs')

    def submit(self, *, target, content, name):
        request = json.dumps({'configPath': self.config_path, 'target': target,
                              'content': base64.b64encode(content).decode('ascii'), 'name': name})
        try:
            result = subprocess.run([self.node, self.bridge], input=request, text=True,
                                    capture_output=True, timeout=self.timeout, check=True)
            data = json.loads(result.stdout)
            if not isinstance(data, dict) or not isinstance(data.get('task_id'), str) or not data['task_id']:
                raise ValueError('missing task id')
            return data['task_id']
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            raise RuntimeError('OV submission outcome unknown; reconcile before retry') from exc


class OVPublisher:
    def __init__(self, foundations, root, transport):
        self.f, self.root, self.transport = foundations, _root(root), transport
        self.f.db.execute('''CREATE TABLE IF NOT EXISTS ov_submissions (
            job_id TEXT PRIMARY KEY, target TEXT NOT NULL, artifact_sha256 TEXT NOT NULL,
            state TEXT NOT NULL, task_id TEXT, created REAL NOT NULL)''')
        self.f.db.commit()

    def submit(self, job_id, revision, credential):
        f = self.f
        with f.transaction():
            f._authorize(credential, 'publish', job_id)
            job = f.store.get(job_id)
            if f.db.execute('SELECT 1 FROM ov_submissions WHERE job_id=?', (job_id,)).fetchone():
                raise Conflict('submission already attempted; reconcile before retry')
            if job['revision'] != revision:
                raise Conflict('stale revision')
            if job['state'] != 'approved':
                raise ValueError('approval required')
            review = f.db.execute('SELECT artifact_name,artifact_version,artifact_sha256,report_sha256 FROM reviews WHERE job_id=? AND decision=?', (job_id, 'approved')).fetchone()
            if review is None:
                raise ValueError('approval record missing')
            artifact = f.read_resource(job_id, review[0], review[1])
            if artifact['sha256'] != review[2] or f.read_resource(job_id, review[0])['version'] != review[1]:
                raise Conflict('approved artifact changed')
            if f.read_resource(job_id, 'report.json')['sha256'] != review[3]:
                raise Conflict('approved report changed')
            if len(artifact['content']) > _MAX_BYTES:
                raise ValueError('artifact exceeds upload limit')
            target = f'{self.root}/submission-{uuid.uuid4().hex}'
            f.db.execute('INSERT INTO ov_submissions VALUES (?,?,?,?,?,?)',
                         (job_id, target, artifact['sha256'], 'attempting', None, time.time()))
        # The committed attempt blocks all retries, including after process death.
        suffix = Path(review[0]).suffix.lower()
        name = 'artifact' + (suffix if re.fullmatch(r'\.[a-z0-9]{1,10}', suffix) else '.bin')
        task_id = self.transport.submit(target=target, content=artifact['content'], name=name)
        if not isinstance(task_id, str) or not task_id.strip():
            raise RuntimeError('OV submission outcome unknown; reconcile before retry')
        with f.transaction():
            f.db.execute("UPDATE ov_submissions SET state='accepted',task_id=? WHERE job_id=? AND state='attempting'", (task_id, job_id))
        return {'target': target, 'task_id': task_id, 'sha256': artifact['sha256'], 'state': 'accepted'}
