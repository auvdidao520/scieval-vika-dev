import tempfile
import unittest
from pathlib import Path
from framework import CRITERIA, Conflict, Store, snapshot, validate_report

class FrameworkTests(unittest.TestCase):
    def test_round_snapshot_idempotent_and_immutable(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'round1' / 'input.html'
            first = snapshot(path, b'hello')
            self.assertEqual(first, snapshot(path, b'hello'))
            with self.assertRaises(Conflict): snapshot(path, b'changed')
            self.assertEqual(path.read_bytes(), b'hello')

    def test_state_cas_outbox_and_round(self):
        with tempfile.TemporaryDirectory() as root:
            dbpath = str(Path(root) / 'jobs.db')
            store = Store(dbpath)
            store.create('j1')
            store.transition('j1', 0, 'preparing')
            with self.assertRaises(Conflict): store.transition('j1', 0, 'preparing')
            store.transition('j1', 1, 'evaluating')
            store.transition('j1', 2, 'revising')
            store.transition('j1', 3, 'evaluating')
            self.assertEqual(store.get('j1')['round'], 2)
            store.close()
            reopened = Store(dbpath)
            self.assertEqual(len(reopened.pending()), 4)
            self.assertEqual(reopened.get('j1')['revision'], 4)
            reopened.close()

    def test_report_missing_duplicate_and_unscored(self):
        criteria = [{'criterion_id': x, 'status': 'scored', 'score': 2, 'evidence': [], 'reasoning': 'x', 'recommendations': []} for x in CRITERIA]
        report = dict(schema_version='1', evaluation_id='e', job_id='j', round=1, input_uri='x', input_sha256='h', rubric_id='r', rubric_version='1', policy_version='1', evaluator_version='1', created_at='t', criteria=criteria, decision={'result':'pass','reason':'test'})
        validate_report(report, job_id='j', round_number=1, input_sha256='h')
        report['criteria'][0]['criterion_id'] = report['criteria'][1]['criterion_id']
        with self.assertRaises(ValueError): validate_report(report, job_id='j', round_number=1, input_sha256='h')
        report['criteria'][0]['criterion_id'] = next(iter(CRITERIA - {c['criterion_id'] for c in criteria}))
        report['criteria'][0].update(status='error', score=None)
        with self.assertRaises(ValueError): validate_report(report, job_id='j', round_number=1, input_sha256='h')

if __name__ == '__main__': unittest.main()
