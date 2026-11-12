from framework import Conflict
from task_recovery import recover_task_binding
from test_worker_loop import WorkerLoopTests


class Gateway:
    def __init__(self):
        self.reservation = None
        self.creations = 0
        self.activations = 0
        self.fail_activation = False

    def reserve(self, job, seq):
        if self.reservation is None:
            self.creations += 1
            self.reservation = {'job_id': job, 'origin_seq': seq, 'task_id': 't1'}
        return self.reservation

    def activate(self, job, seq, task):
        if self.fail_activation:
            raise TimeoutError('activation outcome unknown')
        self.activations += 1


class RecoveryTests(WorkerLoopTests):
    def test_retry_after_reservation_and_after_activation_failure(self):
        gateway = Gateway()
        original = self.store.update_workflow
        def failed_write(*args):
            raise RuntimeError('write failed')
        self.store.update_workflow = failed_write
        with self.assertRaisesRegex(RuntimeError, 'write failed'):
            recover_task_binding(self.store, gateway, 'job1', 1)
        self.assertEqual(gateway.activations, 0)
        self.store.update_workflow = original
        gateway.fail_activation = True
        with self.assertRaises(TimeoutError):
            recover_task_binding(self.store, gateway, 'job1', 1)
        self.assertEqual(self.store.get_full('job1')['task_id'], 't1')
        gateway.fail_activation = False
        recover_task_binding(self.store, gateway, 'job1', 1)
        self.assertEqual(gateway.creations, 1)
        self.assertEqual(gateway.activations, 1)

    def test_mismatched_reservation_never_activates(self):
        gateway = Gateway()
        gateway.reservation = {'job_id': 'other', 'origin_seq': 1, 'task_id': 't1'}
        with self.assertRaises(Conflict): recover_task_binding(self.store, gateway, 'job1', 1)
        self.assertIsNone(self.store.get_full('job1')['task_id'])
        self.assertEqual(gateway.activations, 0)
        with self.assertRaises(ValueError): recover_task_binding(self.store, gateway, 'job1', True)

    def test_existing_different_binding_is_not_overwritten(self):
        from worker_loop import bind_task
        bind_task(self.store, 'job1', 0, 't2')
        gateway = Gateway()
        with self.assertRaises(Conflict): recover_task_binding(self.store, gateway, 'job1', 1)
        self.assertEqual(gateway.activations, 0)
        self.assertEqual(self.store.get_full('job1')['task_id'], 't2')
