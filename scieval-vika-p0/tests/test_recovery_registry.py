import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from recovery_registry import RecoveryRegistry
from framework import Conflict


class RegistryTests(unittest.TestCase):
    def test_persists_and_conflicts_do_not_replace_origin(self):
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / 'registry.db')
            registry = RecoveryRegistry(path)
            registry.register('job', 4)
            registry.register('job', 4)
            with self.assertRaises(Conflict): registry.register('job', 5)
            registry.close()
            registry = RecoveryRegistry(path)
            self.assertEqual(registry.list_jobs(), [('job', 4)])
            registry.close()

    def test_validation_and_two_connections(self):
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / 'registry.db')
            a, b = RecoveryRegistry(path), RecoveryRegistry(path)
            for job, seq in [('../x', 1), ('j', True), ('j', 0), ('j', 2**63)]:
                with self.assertRaises(ValueError): a.register(job, seq)
            a.register('j', 2)
            with self.assertRaises(Conflict): b.register('j', 3)
            self.assertEqual(b.list_jobs(), [('j', 2)])
            a.close(); b.close()

    def test_registry_usable_by_scheduler_thread(self):
        with tempfile.TemporaryDirectory() as d:
            registry = RecoveryRegistry(str(Path(d) / 'registry.db'))
            registry.register('j', 1)
            results = []
            thread = threading.Thread(target=lambda: results.extend(registry.list_jobs()))
            thread.start(); thread.join()
            self.assertEqual(results, [('j', 1)])
            registry.close()

    def test_database_failure_is_not_empty_registry(self):
        with tempfile.TemporaryDirectory() as d:
            registry = RecoveryRegistry(str(Path(d) / 'registry.db'))
            registry.close()
            with self.assertRaises(sqlite3.ProgrammingError): registry.list_jobs()

if __name__ == '__main__': unittest.main()
