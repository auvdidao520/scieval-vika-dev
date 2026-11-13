import unittest
from unittest.mock import patch
from recovery_runtime import start_recovery


class Delivery:
    def accepted_scieval(self, report_id): return []
    def route_accepted(self, task_id, row, role): pass


class RuntimeTest(unittest.TestCase):
    def test_explicit_start_and_stop(self):
        with patch('recovery_runtime.RecoveryScheduler') as cls:
            scheduler = cls.return_value
            runtime = start_recovery(store=object(), registry=object(), evaluator=object(), delivery=Delivery(), socket_path='/tmp/test.sock')
            runtime.stop(timeout=2)
            scheduler.run.assert_called_once()
            scheduler.stop.assert_called_once()
            self.assertTrue(callable(cls.call_args.args[1].reserve))
            self.assertTrue(callable(cls.call_args.args[1].route_accepted))
