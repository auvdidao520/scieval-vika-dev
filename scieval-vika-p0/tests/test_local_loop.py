import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from framework import Conflict
from local_loop import LocalLoop
from simulated_evaluator import SimulatedEvaluator

FIXTURE = Path(__file__).resolve().parents[1] / 'fixtures' / 'synthetic_scores.json'

class LoopTests(unittest.TestCase):
    def test_resumed_revision_must_differ_from_previous_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            draft = root / 'draft.html'
            draft.write_text('same content')
            loop = LocalLoop(root / 'state', SimulatedEvaluator(FIXTURE))
            self.addCleanup(loop.close)
            loop.create('job')
            loop.advance('job', draft)
            staged = loop.directory('job', 2) / 'input.html'
            staged.parent.mkdir(parents=True)
            staged.write_bytes(draft.read_bytes())
            with self.assertRaisesRegex(ValueError, 'revision must differ'):
                loop.advance('job')
            self.assertEqual(loop.store.get('job')['state'], 'revising')

    def test_restart_after_report_before_transition_and_two_rounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / 'first.html'
            first.write_text('<p>draft</p>')
            second = root / 'second.html'
            second.write_text('<p>revised</p>')
            loop = LocalLoop(root / 'state', SimulatedEvaluator(FIXTURE))
            loop.create('job1')
            original = loop.store.transition
            def crash(job, revision, target):
                if target == 'revising':
                    raise RuntimeError('simulated crash')
                return original(job, revision, target)
            with patch.object(loop.store, 'transition', side_effect=crash):
                with self.assertRaises(RuntimeError):
                    loop.advance('job1', first)
            loop.close()
            loop = LocalLoop(root / 'state', SimulatedEvaluator(FIXTURE))
            self.assertEqual(loop.advance('job1')['state'], 'revising')
            with self.assertRaises(ValueError):
                loop.advance('job1', first)
            self.assertEqual(loop.advance('job1', second)['state'], 'awaiting_review')
            self.assertEqual(loop.advance('job1')['round'], 2)
            report = json.loads((root / 'state/jobs/job1/rounds/2/report.json').read_text())
            self.assertTrue(report['synthetic'])
            self.assertEqual(len(loop.store.pending()), 5)
            loop.close()

    def test_tampered_input_and_invalid_identifier_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            draft = root / 'draft.html'
            draft.write_text('one')
            loop = LocalLoop(root / 'state', SimulatedEvaluator(FIXTURE))
            with self.assertRaises(ValueError):
                loop.create('../escape')
            loop.create('job')
            loop.advance('job', draft)
            artifact = root / 'state/jobs/job/rounds/1/input.html'
            artifact.write_text('altered')
            # Existing archived request detects changed input on any resumed evaluation.
            loop.store.db.execute("UPDATE jobs SET state='evaluating' WHERE job_id='job'")
            loop.store.db.commit()
            with self.assertRaises(Conflict):
                loop.advance('job')
            loop.close()

if __name__ == '__main__':
    unittest.main()
