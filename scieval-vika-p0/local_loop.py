"""Restartable local demonstration using explicitly supplied draft revisions."""
import argparse
import hashlib
import json
import re
from pathlib import Path

from framework import Conflict, Store, snapshot, validate_report
from simulated_evaluator import SimulatedEvaluator


class LocalLoop:
    def __init__(self, root, evaluator):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.store = Store(str(self.root / 'jobs.sqlite'))
        self.evaluator = evaluator

    def close(self):
        self.store.close()

    def directory(self, job_id, round_number):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', job_id):
            raise ValueError('job_id must contain only letters, digits, _ or -')
        return self.root / 'jobs' / job_id / 'rounds' / str(round_number)

    def create(self, job_id):
        self.directory(job_id, 1)
        self.store.create(job_id)

    def advance(self, job_id, draft=None):
        """Advance until a revision or human review is needed; safe to resume after restart."""
        job = self.store.get(job_id)
        if job['state'] == 'received':
            self.store.transition(job_id, job['revision'], 'preparing')
            job = self.store.get(job_id)
        if job['state'] in ('preparing', 'revising'):
            next_round = job['round'] + (job['state'] == 'revising')
            target = self.directory(job_id, next_round) / 'input.html'
            if draft is not None:
                content = Path(draft).read_bytes()
                if job['state'] == 'revising':
                    previous = (self.directory(job_id, job['round']) / 'input.html').read_bytes()
                    if content == previous:
                        raise ValueError('revision must differ from previous input')
                snapshot(target, content)
            elif not target.is_file():
                return job
            if job['state'] == 'revising':
                previous = (self.directory(job_id, job['round']) / 'input.html').read_bytes()
                if target.read_bytes() == previous:
                    raise ValueError('revision must differ from previous input')
            self.store.transition(job_id, job['revision'], 'evaluating')
            job = self.store.get(job_id)
        if job['state'] != 'evaluating':
            return job
        directory = self.directory(job_id, job['round'])
        input_path = directory / 'input.html'
        data = input_path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        request = {'job_id': job_id, 'round': job['round'], 'uri': str(input_path), 'sha256': digest}
        request_bytes = json.dumps(request, sort_keys=True, ensure_ascii=False).encode('utf-8')
        snapshot(directory / 'request.json', request_bytes)
        key = f'{job_id}:{job["round"]}:{digest}:synthetic'
        report_path = directory / 'report.json'
        if report_path.exists():
            report = json.loads(report_path.read_text(encoding='utf-8'))
        else:
            eid = self.evaluator.submit(request, key)
            report = self.evaluator.query(eid)
            self._validate(report, request, eid)
            snapshot(report_path, json.dumps(report, sort_keys=True, ensure_ascii=False, indent=2).encode('utf-8'))
        self._validate(report, request)
        result = report['decision']['result']
        self.store.transition(job_id, job['revision'], 'revising' if result == 'revise' else 'awaiting_review')
        return self.store.get(job_id)

    @staticmethod
    def _validate(report, request, eid=None):
        validate_report(report, job_id=request['job_id'], round_number=request['round'], input_sha256=request['sha256'])
        if report['input_uri'] != request['uri'] or (eid is not None and report['evaluation_id'] != eid):
            raise ValueError('report evaluation or input URI mismatch')
        if report.get('synthetic') is not True:
            raise ValueError('local demonstration accepts synthetic reports only')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, help='local state and artifact directory')
    sub = parser.add_subparsers(dest='command', required=True)
    create = sub.add_parser('create')
    create.add_argument('job_id')
    advance = sub.add_parser('advance')
    advance.add_argument('job_id')
    advance.add_argument('--draft', help='HTML file for the next round')
    status = sub.add_parser('status')
    status.add_argument('job_id')
    args = parser.parse_args()
    loop = LocalLoop(args.root, SimulatedEvaluator(Path(__file__).parent / 'fixtures' / 'synthetic_scores.json'))
    try:
        if args.command == 'create':
            loop.create(args.job_id)
            result = loop.store.get(args.job_id)
        elif args.command == 'advance':
            result = loop.advance(args.job_id, args.draft)
        else:
            result = loop.store.get(args.job_id)
        print(json.dumps(result, ensure_ascii=False))
    finally:
        loop.close()

if __name__ == '__main__':
    main()
