import tempfile
import unittest
from pathlib import Path
from foundations import Foundations, digest
from framework import Conflict
from ov_publish import OVPublisher


class Auth:
    def authorize(self, credential, action, job_id):
        return 'principal' if credential == action else None


class Transport:
    def __init__(self, fail=False): self.calls, self.fail = [], fail
    def submit(self, **request):
        self.calls.append(request)
        if self.fail: raise RuntimeError('timeout')
        return 'task-1'


class OVPublishTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.f = Foundations(str(Path(self.temp.name) / 'db'), Auth())
        self.f.store.create('job')
        self.f.write_resource('job', 'input.html', b'approved bytes')
        self.f.write_resource('job', 'report.json', b'report')
        self.f.store.transition('job', 0, 'awaiting_review')
        self.t = Transport()
        self.p = OVPublisher(self.f, 'viking://resources/project', self.t)

    def tearDown(self):
        self.f.close()
        self.temp.cleanup()

    def approve(self):
        self.f.review('job', 1, 'input.html', 1, digest(b'report'), 'v1', 'review', 'approved')

    def test_requires_approval_authorization_and_current_content(self):
        with self.assertRaises(ValueError): self.p.submit('job', 1, 'publish')
        self.approve()
        with self.assertRaises(PermissionError): self.p.submit('job', 2, 'review')
        self.f.write_resource('job', 'input.html', b'changed', 1)
        with self.assertRaises(Conflict): self.p.submit('job', 2, 'publish')
        self.assertEqual(self.t.calls, [])

    def test_accepted_is_not_published_and_duplicate_is_blocked(self):
        self.approve()
        result = self.p.submit('job', 2, 'publish')
        self.assertEqual(self.t.calls[0]['content'], b'approved bytes')
        self.assertEqual(result['task_id'], 'task-1')
        self.assertEqual(self.f.store.get('job')['state'], 'approved')
        with self.assertRaises(Conflict): self.p.submit('job', 2, 'publish')
        self.assertEqual(len(self.t.calls), 1)

    def test_timeout_stays_uncertain_across_restart(self):
        self.approve()
        t = Transport(fail=True)
        with self.assertRaises(RuntimeError): OVPublisher(self.f, 'viking://resources/project', t).submit('job', 2, 'publish')
        self.f.close()
        self.f = Foundations(str(Path(self.temp.name) / 'db'), Auth())
        with self.assertRaises(Conflict): OVPublisher(self.f, 'viking://resources/project', t).submit('job', 2, 'publish')
        self.assertEqual(self.f.db.execute('SELECT state FROM ov_submissions').fetchone()[0], 'attempting')
        self.assertEqual(len(t.calls), 1)

    def test_report_change_and_bad_root(self):
        self.approve()
        self.f.write_resource('job', 'report.json', b'changed', 1)
        with self.assertRaises(Conflict): self.p.submit('job', 2, 'publish')
        for root in ('', 'viking://resources', 'viking://resources/a/../b', 'viking://user/u/resources/vika/x'):
            with self.assertRaises(ValueError): OVPublisher(self.f, root, self.t)
        self.assertEqual(self.t.calls, [])
