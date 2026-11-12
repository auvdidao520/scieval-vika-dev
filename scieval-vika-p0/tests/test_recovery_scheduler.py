import unittest
from unittest.mock import patch
from recovery_scheduler import RecoveryScheduler

class Registry:
    def list_jobs(self): return [('bad', 1), ('good', 2)]
class Store:
    def get_full(self, job):
        if job == 'bad': raise RuntimeError('unavailable')
        return {'state': 'received'}
class SchedulerTests(unittest.TestCase):
    @patch('recovery_scheduler.deliver_pending', return_value=[])
    @patch('recovery_scheduler.recover_task_binding')
    def test_failure_isolated_and_valid_job_recovered(self, recover, deliver):
        scheduler = RecoveryScheduler(Store(), object(), object(), Registry())
        with self.assertLogs(level='ERROR'):
            result = scheduler.run_once()
        self.assertEqual(result['jobs']['bad']['error'], 'RuntimeError')
        recover.assert_called_once_with(scheduler.store, scheduler.gateway, 'good', 2)
        deliver.assert_called_once()

    def test_discovery_failure_propagates_and_lock_is_released(self):
        class Bad:
            def list_jobs(self): raise RuntimeError('offline')
        scheduler = RecoveryScheduler(Store(), object(), object(), Bad())
        for _ in range(2):
            with self.assertRaises(RuntimeError): scheduler.run_once()

    def test_concurrent_scan_is_skipped(self):
        scheduler = RecoveryScheduler(Store(), object(), object(), Registry())
        scheduler._round_lock.acquire()
        try: self.assertEqual(scheduler.run_once()['skipped'], 'scan_in_progress')
        finally: scheduler._round_lock.release()

    @patch('recovery_scheduler.deliver_pending', return_value=[])
    @patch('recovery_scheduler.reconcile_report')
    @patch('recovery_scheduler.submit_round')
    def test_preparing_submits_then_polls(self, submit, poll, delivery):
        class Single:
            def list_jobs(self): return [('j', 1)]
        class States:
            def __init__(self): self.states = iter([{'state':'preparing'}, {'state':'preparing'}, {'state':'evaluating'}])
            def get_full(self, job): return next(self.states)
        s = RecoveryScheduler(States(), object(), object(), Single())
        self.assertEqual(s.run_once()['jobs']['j']['deliveries'], [])
        submit.assert_called_once(); poll.assert_called_once(); delivery.assert_called_once()

if __name__ == '__main__': unittest.main()
