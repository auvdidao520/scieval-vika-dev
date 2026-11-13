import tempfile
import unittest
from pathlib import Path
from framework import Conflict
from ov_store import OvStore
from simulated_evaluator import SimulatedEvaluator
from test_ov_store import FakeOv
from worker_loop import (apply_report, bind_task, prepare_round, report_route, submit_round,
                         reconcile_report, pending_report_deliveries, acknowledge_report_delivery)

class WorkerLoopTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = OvStore('viking://~/resources/scieval/jobs', str(self.root / 'locks'), runner=FakeOv())
        self.category = {'category':'experiment_inquiry', 'category_version':'teaching-category-draft-1', 'subject':'science', 'grade':'初二', 'confidence':.9, 'teacher_requirements':['保留活动'], 'needs_teacher_confirmation':False}
        self.plan = {'plan_id':'p1', 'based_on_report_id':None, 'target_criteria':['II.B'], 'actions':[{'criterion_id':'II.B', 'change':'收集观点', 'expected_evidence':'观点记录', 'priority':'P0'}], 'preserve':['保留活动']}
        self.store.create_workflow('job1', self.category, self.plan, 'synthetic')
        self.evaluator = SimulatedEvaluator(Path(__file__).resolve().parents[1] / 'fixtures/synthetic_scores.json')

    def test_two_rounds_and_idempotent_feedback(self):
        s = bind_task(self.store, 'job1', 0, 't1')
        s = prepare_round(self.store, 'job1', s['revision'], self.root, b'<p>first</p>')
        s = submit_round(self.store, self.evaluator, 'job1')
        self.assertEqual(report_route(self.store, 'job1', s['current']['evaluation_id']), 't1')
        report = self.evaluator.query(s['current']['evaluation_id'])
        with self.assertRaises(ValueError): apply_report(self.store, 'job1', 'bad', {**report, 'input_sha256':'wrong'})
        self.assertEqual(apply_report(self.store, 'job1', 'r1', report), 'revising')
        self.assertEqual(apply_report(self.store, 'job1', 'r1', report), 'duplicate')
        with self.assertRaises(Conflict): apply_report(self.store, 'job1', 'r1', {**report, 'job_id':'bad'})
        s = self.store.get_full('job1')
        p = {**self.plan, 'plan_id':'p2', 'based_on_report_id':'r1'}
        with self.assertRaises(ValueError):
            prepare_round(self.store, 'job1', s['revision'], self.root, b'<p>second</p>', {**p, 'preserve': []})
        self.assertEqual(s['last_report']['evaluation_id'], report['evaluation_id'])
        s = prepare_round(self.store, 'job1', s['revision'], self.root, b'<p>second</p>', p)
        self.assertEqual(s['round'], 2)
        s = submit_round(self.store, self.evaluator, 'job1')
        self.assertEqual(apply_report(self.store, 'job1', 'r2', self.evaluator.query(s['current']['evaluation_id'])), 'awaiting_review')
        self.assertEqual((self.root / 'job1' / 'round_001' / 'input.html').read_bytes(), b'<p>first</p>')
        self.assertEqual(self.store.get_full('job1')['task_id'], 't1')

    def test_retry_after_external_success_and_local_failure(self):
        s = bind_task(self.store, 'job1', 0, 't1')
        prepare_round(self.store, 'job1', s['revision'], self.root, b'<p>x</p>')
        class Ambiguous:
            def __init__(self, inner): self.inner = inner
            def submit(self, request, key):
                self.inner.submit(request, key)
                raise TimeoutError()
        with self.assertRaises(TimeoutError): submit_round(self.store, Ambiguous(self.evaluator), 'job1')
        s = submit_round(self.store, self.evaluator, 'job1')
        self.assertEqual(len(self.evaluator.requests), 1)
        self.assertEqual(submit_round(self.store, self.evaluator, 'job1')['current'], s['current'])

    def test_routing_and_dispatch_fail_closed(self):
        with self.assertRaises(ValueError): self.store.create_workflow('job2', {**self.category, 'needs_teacher_confirmation':True}, self.plan, 'synthetic')
        with self.assertRaises(Conflict): report_route(self.store, 'job1', 'wrong')
        bind_task(self.store, 'job1', 0, 't1')
        with self.assertRaises(Conflict): bind_task(self.store, 'job1', 1, 't2')
        with self.assertRaises(Conflict): prepare_round(self.store, 'job1', 0, self.root, b'x')

    def test_missed_event_recovery_and_durable_delivery(self):
        state = bind_task(self.store, 'job1', 0, 't1')
        prepare_round(self.store, 'job1', state['revision'], self.root, b'<p>x</p>')
        state = submit_round(self.store, self.evaluator, 'job1')
        report = {**self.evaluator.query(state['current']['evaluation_id']), 'report_id': 'r1'}
        class Query:
            def __init__(self, result): self.result = result
            def query(self, evaluation_id): return self.result
        self.assertEqual(reconcile_report(self.store, Query({'status':'running'}), 'job1'), 'pending')
        with self.assertRaises(ValueError):
            reconcile_report(self.store, Query({k:v for k,v in report.items() if k != 'report_id'}), 'job1')
        self.assertEqual(reconcile_report(self.store, Query(report), 'job1'), 'revising')
        reopened = OvStore('viking://~/resources/scieval/jobs', str(self.root / 'locks'), runner=self.store.runner)
        events = pending_report_deliveries(reopened, 'job1')
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['task_id'], 't1')
        self.assertEqual(apply_report(reopened, 'job1', 'r1', report), 'duplicate')
        self.assertEqual(len(pending_report_deliveries(reopened, 'job1')), 1)
        with self.assertRaises(Conflict):
            acknowledge_report_delivery(reopened, 'job1', events[0]['event_id'], 't2')
        acknowledge_report_delivery(reopened, 'job1', events[0]['event_id'], 't1')
        acknowledge_report_delivery(reopened, 'job1', events[0]['event_id'], 't1')
        self.assertEqual(pending_report_deliveries(reopened, 'job1'), [])
        with self.assertRaises(ValueError): apply_report(reopened, 'job1', 'different', report)
