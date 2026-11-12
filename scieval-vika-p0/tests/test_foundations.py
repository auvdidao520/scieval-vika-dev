import tempfile
import unittest
from pathlib import Path
from foundations import Foundations, digest
from framework import Conflict


class TestAuthorizer:
    def authorize(self, credential, action, job_id):
        return credential if credential in ('alice', 'publisher') else None


class FoundationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / 'state.sqlite')
        self.f = Foundations(self.path, TestAuthorizer())
        self.f.store.create('job')

    def tearDown(self):
        self.f.close()
        self.tmp.cleanup()

    def prepare(self):
        self.f.write_resource('job', 'input.html', b'hello')
        self.f.write_resource('job', 'report.json', b'report')
        self.f.store.transition('job', 0, 'awaiting_review')

    def test_review_publication_and_bypass(self):
        self.prepare()
        with self.assertRaises(ValueError):
            self.f.store.transition('job', 1, 'approved')
        with self.assertRaises(Conflict):
            self.f.review('job', 1, 'input.html', 1, '0'*64, 'p1', 'alice', 'approved')
        self.f.review('job', 1, 'input.html', 1, digest(b'report'), 'p1', 'alice', 'approved')
        self.assertEqual(self.f.publish_local('job', 2, 'publisher'), digest(b'hello'))
        self.assertEqual(self.f.publish_local('job', 2, 'publisher'), digest(b'hello'))
        self.assertEqual(self.f.kanban()[0]['state'], 'published')
        self.assertEqual(len(self.f.store.pending()), 3)

    def test_changed_artifact_blocks_publish(self):
        self.prepare()
        self.f.review('job', 1, 'input.html', 1, digest(b'report'), 'p1', 'alice', 'approved')
        self.f.write_resource('job', 'input.html', b'changed', 1)
        with self.assertRaises(Conflict):
            self.f.publish_local('job', 2, 'publisher')
        self.assertEqual(self.f.store.get('job')['state'], 'approved')

    def test_dispatch_restart_and_idempotency(self):
        self.f.enqueue('t', 'double', {'n': 3})
        self.f.enqueue('t', 'double', {'n': 3})
        with self.assertRaises(Conflict):
            self.f.enqueue('t', 'double', {'n': 4})
        self.f.close()
        self.f = Foundations(self.path, TestAuthorizer())
        self.assertEqual(self.f.run_one({'double': lambda x: x['n'] * 2}), ('t', 'succeeded'))
        self.assertEqual(self.f.db.execute('SELECT result FROM dispatch').fetchone()[0], '6')
        self.assertIsNone(self.f.run_one({}))

    def test_expired_lease_and_version_conflict(self):
        self.f.enqueue('t', 'echo', {'x': 1})
        self.f.db.execute("UPDATE dispatch SET state='running',lease_until=0 WHERE task_id='t'")
        self.f.db.commit()
        self.assertEqual(self.f.run_one({'echo': lambda x: x}), ('t', 'succeeded'))
        self.f.write_resource('job', 'a', b'a')
        with self.assertRaises(Conflict):
            self.f.write_resource('job', 'a', b'b')
        self.assertEqual(self.f.read_resource('job', 'a')['content'], b'a')
