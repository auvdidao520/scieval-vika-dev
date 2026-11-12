import copy
import unittest

from contracts import validate_revision_context
from framework import CRITERIA


class RevisionContextTests(unittest.TestCase):
    def setUp(self):
        self.binding = dict(schema_version=1, job_id='J1', task_id='t17', source_event_id='event:1')
        self.plan = dict(plan_id='p2', based_on_report_id='r1', target_criteria=['II.B'],
                         actions=[dict(criterion_id='II.B', change='change', expected_evidence='evidence', priority='P1')], preserve=[])
        self.digest = 'a' * 64
        self.report = dict(schema_version=1, evaluation_id='r1', job_id='J1', round=1,
                           input_uri='snapshot', input_sha256=self.digest, rubric_id='rubric',
                           rubric_version='1', policy_version='1', evaluator_version='1', created_at='now',
                           criteria=[dict(criterion_id=i, status='scored', score=1, evidence=[], reasoning='', recommendations=[]) for i in CRITERIA],
                           decision=dict(result='revise', reason='improve'))

    def validate(self, **changes):
        values = dict(binding=self.binding, plan=self.plan, report=self.report, next_round=2,
                      expected_job_id='J1', expected_input_sha256=self.digest)
        values.update(changes)
        validate_revision_context(**values)

    def test_valid_revision(self):
        self.validate()

    def test_rejects_wrong_trusted_context_and_binding(self):
        for changes in (dict(expected_job_id='J2'), dict(binding={**self.binding, 'job_id': 'J2'}),
                        dict(next_round=True), dict(next_round=1), dict(expected_input_sha256='x'*64),
                        dict(expected_input_sha256='b'*64)):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.validate(**changes)

    def test_rejects_mismatched_or_incomplete_report(self):
        for mutation in (lambda r: r.update(job_id='J2'), lambda r: r.update(round=2),
                         lambda r: r.update(evaluation_id='wrong'), lambda r: r.update(evaluation_id=''),
                         lambda r: r['criteria'].pop()):
            report = copy.deepcopy(self.report)
            mutation(report)
            with self.subTest(report=report), self.assertRaises(ValueError):
                self.validate(report=report)

    def test_rejects_plan_citing_another_report(self):
        with self.assertRaisesRegex(ValueError, 'plan report identity mismatch'):
            self.validate(plan={**self.plan, 'based_on_report_id': 'other'})
