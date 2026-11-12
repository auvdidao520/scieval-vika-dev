import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from foundations import Foundations, digest
from framework import Conflict
from identity import SyntheticLocalReviewerProvider

class IdentityConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / 'state.sqlite')
        self.f = Foundations(self.path, SyntheticLocalReviewerProvider())
        self.f.store.create('job')
        self.f.write_resource('job', 'input.html', b'input')
        self.f.write_resource('job', 'report.json', b'report')
        self.f.store.transition('job', 0, 'awaiting_review')

    def tearDown(self):
        self.f.close()
        self.tmp.cleanup()

    def review(self, f):
        return f.review('job', 1, 'input.html', 1, digest(b'report'), 'p1', 'demo-review-token', 'approved')

    def race(self, fn):
        barrier = threading.Barrier(2)
        def worker():
            f = Foundations(self.path, SyntheticLocalReviewerProvider())
            try:
                barrier.wait(timeout=5)
                try:
                    return ('ok', fn(f))
                except Exception as exc:
                    return ('error', exc)
            finally:
                f.close()
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(worker) for _ in range(2)]
            return [future.result(timeout=10) for future in futures]

    def test_identity_denied_and_synthetic_identity_recorded(self):
        for credential in (None, 'synthetic-reviewer', 'demo-publish-token'):
            with self.assertRaises(PermissionError):
                self.f.review('job', 1, 'input.html', 1, digest(b'report'), 'p1', credential, 'approved')
        no_provider = Foundations(self.path)
        try:
            with self.assertRaises(PermissionError):
                self.review(no_provider)
        finally:
            no_provider.close()
        self.review(self.f)
        self.assertEqual(self.f.db.execute('SELECT reviewer FROM reviews').fetchone()[0], 'synthetic-reviewer')
        with self.assertRaises(PermissionError):
            self.f.publish_local('job', 2, 'demo-review-token')
        self.assertEqual(self.f.store.get('job')['state'], 'approved')

    def test_concurrent_reviews_one_wins(self):
        results = self.race(self.review)
        self.assertEqual([x[0] for x in results].count('ok'), 1)
        self.assertIsInstance(next(x[1] for x in results if x[0] == 'error'), Conflict)
        self.assertEqual(self.f.db.execute('SELECT COUNT(*) FROM reviews').fetchone()[0], 1)
        self.assertEqual(self.f.store.get('job')['state'], 'approved')
        self.assertEqual(len(self.f.store.pending()), 2)

    def test_concurrent_publications_are_idempotent(self):
        self.review(self.f)
        results = self.race(lambda f: f.publish_local('job', 2, 'demo-publish-token'))
        self.assertEqual(results, [('ok', digest(b'input'))] * 2)
        self.assertEqual(self.f.db.execute('SELECT COUNT(*) FROM publications').fetchone()[0], 1)
        self.assertEqual(self.f.store.get('job')['state'], 'published')
        self.assertEqual(len(self.f.store.pending()), 3)

    def test_stale_review_and_publication_across_connections(self):
        other = Foundations(self.path, SyntheticLocalReviewerProvider())
        try:
            other.write_resource('job', 'input.html', b'new', 1)
            with self.assertRaises(Conflict):
                self.review(self.f)
            self.assertEqual(self.f.db.execute('SELECT COUNT(*) FROM reviews').fetchone()[0], 0)
            self.f.review('job', 1, 'input.html', 2, digest(b'report'), 'p1', 'demo-review-token', 'approved')
            other.write_resource('job', 'report.json', b'changed', 1)
            with self.assertRaises(Conflict):
                self.f.publish_local('job', 2, 'demo-publish-token')
            self.assertEqual(self.f.db.execute('SELECT COUNT(*) FROM publications').fetchone()[0], 0)
            self.assertEqual(self.f.store.get('job')['state'], 'approved')
        finally:
            other.close()
