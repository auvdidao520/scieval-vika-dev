import tempfile
import unittest
from p1 import P1
from framework import Conflict

class TestAuthorizer:
    def authorize(self, credential, action, job_id):
        return credential if credential in ('local-reviewer', 'reviewer', 'publisher') else None


class P1Tests(unittest.TestCase):
    def test_two_rounds_restart_review_and_local_publish(self):
        with tempfile.TemporaryDirectory() as root:
            p = P1(root, TestAuthorizer())
            p.create('job')
            p.submit('job', 1, b'<h1>draft</h1>')
            p.close()
            p = P1(root, TestAuthorizer())
            self.assertEqual(p.run_one(), ('job:round:1', 'succeeded'))
            self.assertEqual(p.f.kanban()[0]['state'], 'revising')
            p.submit('job', 2, b'<h1>revised</h1>')
            self.assertEqual(p.run_one(), ('job:round:2', 'succeeded'))
            self.assertEqual(p.f.kanban()[0]['state'], 'awaiting_review')
            self.assertTrue(p.f.read_resource('job', 'report.json')['content'])
            p.review('job', 'local-reviewer', 'approved')
            p.publish_local('job', 'publisher')
            self.assertEqual(p.f.kanban()[0]['state'], 'published')
            p.close()

    def test_replay_and_conflicting_submission(self):
        with tempfile.TemporaryDirectory() as root:
            p = P1(root, TestAuthorizer())
            p.create('job')
            p.submit('job', 1, b'one')
            self.assertEqual(p.run_one()[1], 'succeeded')
            p.submit('job', 1, b'one')
            with self.assertRaises(Conflict):
                p.submit('job', 1, b'two')
            # Simulate a worker crash after completing its side effects.
            p.f.db.execute("UPDATE dispatch SET state='running',lease_until=0 WHERE task_id='job:round:1'")
            p.f.db.commit()
            self.assertEqual(p.run_one()[1], 'succeeded')
            p.close()

    def test_failed_task_can_be_retried_after_repair(self):
        with tempfile.TemporaryDirectory() as root:
            p = P1(root, TestAuthorizer())
            p.create('job')
            p.submit('job', 1, b'one')
            source = p.loop.directory('job', 1) / 'submitted.html'
            source.write_bytes(b'tampered')
            self.assertEqual(p.run_one(), ('job:round:1', 'failed'))
            source.write_bytes(b'one')
            p.f.retry_failed('job:round:1')
            self.assertEqual(p.run_one(), ('job:round:1', 'succeeded'))
            self.assertEqual(p.f.kanban()[0]['state'], 'revising')
            with self.assertRaises(Conflict):
                p.f.retry_failed('job:round:1')
            p.close()

    def test_changed_review_report_or_artifact_is_rejected(self):
        for change in ('report.json', 'round-2-input.html'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as root:
                p = P1(root, TestAuthorizer())
                p.create('job')
                p.submit('job', 1, b'one'); p.run_one()
                p.submit('job', 2, b'two'); p.run_one()
                original = p.f.read_resource('job', change)
                p.f.write_resource('job', change, b'{}' if change == 'report.json' else b'changed', original['version'])
                with self.assertRaises((ValueError, Conflict)):
                    p.review('job', 'reviewer', 'approved')
                self.assertEqual(p.f.store.get('job')['state'], 'awaiting_review')
                p.close()

    def test_reject_and_no_publication(self):
        with tempfile.TemporaryDirectory() as root:
            p = P1(root, TestAuthorizer())
            p.create('job')
            p.submit('job', 1, b'one'); p.run_one()
            p.submit('job', 2, b'two'); p.run_one()
            p.review('job', 'reviewer', 'rejected')
            with self.assertRaises(ValueError): p.publish_local('job', 'publisher')
            p.close()

    def test_changed_report_after_approval_blocks_publication(self):
        with tempfile.TemporaryDirectory() as root:
            p = P1(root, TestAuthorizer())
            p.create('job')
            p.submit('job', 1, b'one'); p.run_one()
            p.submit('job', 2, b'two'); p.run_one()
            p.review('job', 'reviewer', 'approved')
            report = p.f.read_resource('job', 'report.json')
            p.f.write_resource('job', 'report.json', b'changed', report['version'])
            with self.assertRaises(Conflict):
                p.publish_local('job', 'publisher')
            self.assertEqual(p.f.store.get('job')['state'], 'approved')
            self.assertEqual(p.f.db.execute('SELECT COUNT(*) FROM publications').fetchone()[0], 0)
            p.close()
