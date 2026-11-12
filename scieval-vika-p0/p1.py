"""Single-job local POC; all adapters are local and evaluations are synthetic."""
import hashlib
import json
from pathlib import Path

from foundations import Foundations
from framework import Conflict, snapshot
from local_loop import LocalLoop
from simulated_evaluator import SimulatedEvaluator


class P1:
    def __init__(self, root, authorizer=None):
        self.root = Path(root).resolve()
        self.loop = LocalLoop(self.root, SimulatedEvaluator(Path(__file__).parent / 'fixtures/synthetic_scores.json'))
        self.f = Foundations(str(self.root / 'jobs.sqlite'), authorizer=authorizer)

    def close(self):
        self.f.close()
        self.loop.close()

    def create(self, job_id):
        self.loop.create(job_id)

    def submit(self, job_id, round_number, content):
        job = self.loop.store.get(job_id)
        if type(round_number) is not int or round_number not in (1, 2):
            raise ValueError('only two synthetic rounds are available')
        expected = job['round'] + int(job['state'] == 'revising')
        task_id = f'{job_id}:round:{round_number}'
        # A previously queued request can be repeated even after state changes.
        prior = self.f.db.execute('SELECT task_id FROM dispatch WHERE task_id=?', (task_id,)).fetchone()
        if not prior and (job['state'] not in ('received', 'preparing', 'revising', 'evaluating') or expected != round_number):
            raise ValueError('round is not ready')
        path = self.loop.directory(job_id, round_number) / 'submitted.html'
        snapshot(path, content)
        self.f.enqueue(task_id, 'evaluate', {'job_id': job_id, 'round': round_number, 'sha256': hashlib.sha256(content).hexdigest()})

    def run_one(self):
        return self.f.run_one({'evaluate': self._evaluate})

    def _evaluate(self, payload):
        job_id, number = payload['job_id'], payload['round']
        directory = self.loop.directory(job_id, number)
        data = (directory / 'submitted.html').read_bytes()
        if hashlib.sha256(data).hexdigest() != payload['sha256']:
            raise Conflict('submitted content changed')
        job = self.loop.store.get(job_id)
        # A lease may expire after advance completed. Reconcile the saved result.
        finished = job['round'] == number and job['state'] in ('revising', 'awaiting_review')
        if not finished:
            expected = job['round'] + int(job['state'] == 'revising')
            if expected != number or job['state'] not in ('received', 'preparing', 'revising', 'evaluating'):
                raise Conflict('job moved to a different round')
            job = self.loop.advance(job_id, directory / 'submitted.html')
        report = (directory / 'report.json').read_bytes()
        request = json.loads((directory / 'request.json').read_text())
        self.loop._validate(json.loads(report), request)
        if hashlib.sha256((directory / 'input.html').read_bytes()).hexdigest() != payload['sha256']:
            raise Conflict('round input does not match submitted content')
        self._save_once(job_id, f'round-{number}-input.html', data)
        self._save_once(job_id, f'round-{number}-report.json', report)
        if job['state'] == 'awaiting_review':
            self._save_once(job_id, 'report.json', report)
        return job

    def _save_once(self, job_id, name, data):
        try:
            old = self.f.read_resource(job_id, name)
        except KeyError:
            self.f.write_resource(job_id, name, data)
        else:
            if old['content'] != data:
                raise Conflict('resource already exists with different content')

    def review(self, job_id, credential, decision):
        # All database reads that determine approval are repeated while holding
        # the same write reservation as the decision and outbox commit.
        job = self.f.store.get(job_id)
        number = job['round']
        name = f'round-{number}-input.html'
        artifact = self.f.read_resource(job_id, name)
        report = self.f.read_resource(job_id, 'report.json')
        parsed = json.loads(report['content'])
        if not isinstance(parsed, dict) or not isinstance(parsed.get('policy_version'), str) or not parsed['policy_version']:
            raise ValueError('report missing policy_version')

        def validate(locked_job, locked_artifact, locked_report):
            directory = self.loop.directory(job_id, locked_job['round'])
            request = json.loads((directory / 'request.json').read_text(encoding='utf-8'))
            self.loop._validate(json.loads(locked_report['content']), request)
            archived = self.f.read_resource(job_id, f"round-{locked_job['round']}-report.json")
            if archived['content'] != locked_report['content']:
                raise Conflict('review report differs from archived round report')
            if locked_artifact['sha256'] != request['sha256']:
                raise Conflict('review artifact differs from evaluated input')

        self.f.review(job_id, job['revision'], name, artifact['version'], report['sha256'],
                      parsed['policy_version'], credential, decision, validate)

    def publish_local(self, job_id, credential):
        job = self.f.store.get(job_id)
        return self.f.publish_local(job_id, job['revision'], credential)
