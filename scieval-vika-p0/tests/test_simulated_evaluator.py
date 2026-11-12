import tempfile
import unittest
from pathlib import Path
from framework import Conflict, snapshot, validate_report
from simulated_evaluator import SimulatedEvaluator

class SimulatorTests(unittest.TestCase):
    def test_synthetic_two_round_reports_and_conflict(self):
        sim = SimulatedEvaluator(Path(__file__).resolve().parents[1] / 'fixtures/synthetic_scores.json')
        with tempfile.TemporaryDirectory() as tmp:
            for round_number in (1, 2):
                artifact = snapshot(Path(tmp) / str(round_number) / 'lesson.html', f'<p>{round_number}</p>'.encode())
                request = {'job_id':'j', 'round':round_number, 'uri':artifact.uri, 'sha256':artifact.sha256}
                key = f'j:{round_number}:{artifact.sha256}:synthetic'
                eid = sim.submit(request, key)
                self.assertEqual(eid, sim.submit(request, key))
                report = sim.query(eid)
                validate_report(report, job_id='j', round_number=round_number, input_sha256=artifact.sha256)
                self.assertTrue(report['synthetic'])
                self.assertEqual(report['decision']['result'], 'revise' if round_number == 1 else 'human_review')
                changed = dict(request, job_id='another')
                with self.assertRaises(Conflict): sim.submit(changed, key)
                with self.assertRaises(ValueError): sim.submit(dict(request, sha256='wrong'), 'new-key')

if __name__ == '__main__': unittest.main()
