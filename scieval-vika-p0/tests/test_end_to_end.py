"""Local simulated flow; no real integrations or automatic publication."""
import hashlib
import tempfile
import unittest
from pathlib import Path
from framework import CRITERIA, Store, snapshot, validate_report

from simulated_evaluator import SimulatedEvaluator

class EndToEndTests(unittest.TestCase):
    def test_two_rounds_recovery_and_manual_review(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = str(root / 'jobs.sqlite')
            store = Store(db)
            evaluator = SimulatedEvaluator(Path(__file__).resolve().parents[1] / 'fixtures/synthetic_scores.json')
            store.create('job1')
            store.transition('job1', 0, 'preparing')
            first = snapshot(root / 'job1' / '1' / 'input.html', b'<p>draft</p>')
            store.transition('job1', 1, 'evaluating')
            request = {'job_id':'job1', 'round':1, 'uri':first.uri, 'sha256':first.sha256}
            key = 'job1:1:' + first.sha256 + ':test'
            evaluation_id = evaluator.submit(request, key)
            self.assertEqual(evaluation_id, evaluator.submit(request, key))
            # Simulate a process restart after submission. The fake service remains in memory.
            store.close()
            store = Store(db)
            self.assertEqual(store.get('job1')['state'], 'evaluating')
            self.assertEqual(len(store.pending()), 2)
            report = evaluator.query(evaluation_id)
            validate_report(report, job_id='job1', round_number=1, input_sha256=first.sha256)
            self.assertEqual(report['decision']['result'], 'revise')
            store.transition('job1', 2, 'revising')
            second = snapshot(root / 'job1' / '2' / 'input.html', b'<p>revised</p>')
            self.assertNotEqual(first.sha256, second.sha256)
            store.transition('job1', 3, 'evaluating')
            request2 = {'job_id':'job1', 'round':2, 'uri':second.uri, 'sha256':second.sha256}
            eid2 = evaluator.submit(request2, 'job1:2:' + second.sha256 + ':test')
            report2 = evaluator.query(eid2)
            validate_report(report2, job_id='job1', round_number=2, input_sha256=second.sha256)
            self.assertEqual(report2['decision']['result'], 'human_review')
            store.transition('job1', 4, 'awaiting_review')
            self.assertEqual(store.get('job1')['state'], 'awaiting_review')
            self.assertEqual(store.get('job1')['round'], 2)
            self.assertEqual(hashlib.sha256(Path(first.uri).read_bytes()).hexdigest(), first.sha256)
            self.assertEqual(len(store.pending()), 5)
            store.close()

if __name__ == '__main__': unittest.main()
