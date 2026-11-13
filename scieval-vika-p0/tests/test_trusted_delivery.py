import copy
import json
import unittest
from framework import Conflict
from worker_loop import bind_task, prepare_round, submit_round, apply_report, pending_report_deliveries
from trusted_delivery import deliver_pending
from test_worker_loop import WorkerLoopTests


class DeliveryTests(WorkerLoopTests):
    def test_trusted_delivery_and_recovery(self):
        state = bind_task(self.store, 'job1', 0, 't1')
        prepare_round(self.store, 'job1', state['revision'], self.root, b'x')
        state = submit_round(self.store, self.evaluator, 'job1')
        report = {**self.evaluator.query(state['current']['evaluation_id']), 'report_id': 'r1'}
        apply_report(self.store, 'job1', 'r1', report)
        class Gateway:
            rows = []
            calls = []
            fail = True
            def accepted_scieval(self, report_id): return self.rows
            def route_accepted(self, task, row, role):
                self.calls.append((task, row['seq'], role))
                if self.fail: raise TimeoutError('ack lost')
        gateway = Gateway()
        self.assertEqual(deliver_pending(self.store, gateway, 'job1')[0][1], 'awaiting_accepted_event')
        event = {'connector':'scieval', 'instance':'scieval', 'kind':'evaluation.completed',
                 'id':'evaluation.completed:r1', 'subject':{'type':'scieval_job','id':'job1'},
                 'content':{'text':json.dumps(report)}}
        row = {'seq':1, 'status':'DONE', 'connector_instance':'scieval', 'event':event}
        gateway.rows = [row]
        bad = copy.deepcopy(row)
        bad['event']['content']['text'] = json.dumps({**report, 'job_id':'other'})
        gateway.rows = [bad]
        with self.assertRaises(Conflict): deliver_pending(self.store, gateway, 'job1')
        gateway.rows = [row, row]
        with self.assertRaises(Conflict): deliver_pending(self.store, gateway, 'job1')
        gateway.rows = [row]
        with self.assertRaises(TimeoutError): deliver_pending(self.store, gateway, 'job1')
        self.assertEqual(len(pending_report_deliveries(self.store, 'job1')), 1)
        gateway.fail = False
        self.assertEqual(deliver_pending(self.store, gateway, 'job1')[0][1], 'delivered')
        self.assertEqual(gateway.calls, [('t1', 1, 'owner'), ('t1', 1, 'owner')])
        self.assertEqual(deliver_pending(self.store, gateway, 'job1'), [])
