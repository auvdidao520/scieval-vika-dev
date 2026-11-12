import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from framework import Conflict
from ov_store import OvError, OvStore


class FakeOv:
    def __init__(self):
        self.documents = {}
        self.calls = []

    def __call__(self, args):
        self.calls.append(args)
        if args[0] == 'read':
            content = self.documents.get(args[1])
            if content is None:
                return subprocess.CompletedProcess(args, 1, '', 'not found')
            return subprocess.CompletedProcess(args, 0, content, '')
        if args[0] == 'write':
            uri = args[1]
            if args[args.index('--mode') + 1] == 'create' and uri in self.documents:
                return subprocess.CompletedProcess(args, 1, '', 'already exists')
            content = Path(args[args.index('--from-file') + 1]).read_text(encoding='utf-8')
            self.documents[uri] = content
            return subprocess.CompletedProcess(args, 0, '{"status":"ok"}', '')
        return subprocess.CompletedProcess(args, 2, '', 'unknown command')


class OvStoreTests(unittest.TestCase):
    def test_cas_outbox_restart_and_delivery(self):
        with tempfile.TemporaryDirectory() as directory:
            fake = FakeOv()
            root = 'viking://~/resources/scieval/jobs'
            store = OvStore(root, directory, runner=fake)
            store.create('job-1')
            with self.assertRaises(OvError):
                store.create('job-1')
            first = store.transition('job-1', 0, 'preparing')
            self.assertEqual(first['event_id'], 'job-1:transition:1')
            with self.assertRaises(Conflict):
                store.transition('job-1', 0, 'preparing')
            store.transition('job-1', 1, 'evaluating')
            store.transition('job-1', 2, 'revising')
            store.transition('job-1', 3, 'evaluating')
            self.assertEqual(store.get('job-1')['round'], 2)
            self.assertEqual(len(store.pending('job-1')), 4)

            reopened = OvStore(root, directory, runner=fake)
            self.assertEqual(reopened.get('job-1')['revision'], 4)
            reopened.mark_delivered('job-1', first['event_id'])
            reopened.mark_delivered('job-1', first['event_id'])
            self.assertEqual(len(reopened.pending('job-1')), 3)
            state = json.loads(fake.documents[f'{root}/job-1/state.json'])
            self.assertEqual(state['revision'], 4)
            self.assertTrue(state['outbox'][0]['delivered'])
            self.assertTrue(all('--wait' in call for call in fake.calls if call[0] == 'write'))

    def test_rejects_job_ids_that_escape_the_resource_root(self):
        with tempfile.TemporaryDirectory() as directory:
            store = OvStore('viking://~/resources/scieval/jobs', directory, runner=FakeOv())
            with self.assertRaises(ValueError):
                store.get('../other')
            with self.assertRaises(ValueError):
                store.create('job/with/slash')


if __name__ == '__main__':
    unittest.main()
