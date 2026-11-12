import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BRIDGE = Path(__file__).resolve().parents[1] / 'outbox_bridge.py'


class OutboxBatchTests(unittest.TestCase):
    def test_bad_row_does_not_block_valid_prefix_or_get_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'outbox.sqlite'
            with sqlite3.connect(database) as db:
                db.execute('CREATE TABLE outbox (event_id TEXT PRIMARY KEY, job_id TEXT, revision INTEGER, payload TEXT, delivered INTEGER DEFAULT 0)')
                good = json.dumps({'event_id': 'first', 'job_id': 'job', 'revision': 1})
                later = json.dumps({'event_id': 'last', 'job_id': 'job', 'revision': 3})
                db.executemany('INSERT INTO outbox VALUES (?, ?, ?, ?, 0)', [('first', 'job', 1, good), ('bad', 'job', 2, '{'), ('last', 'job', 3, later)])
            def call(request):
                return subprocess.run([sys.executable, str(BRIDGE), str(database)], input=json.dumps(request).encode(), capture_output=True)
            result = call({'command': 'pending'})
            self.assertEqual([e['event_id'] for e in json.loads(result.stdout)['events']], ['first'])
            self.assertEqual(call({'command': 'acknowledge', 'event_id': 'first'}).returncode, 0)
            result = call({'command': 'pending'})
            self.assertNotEqual(result.returncode, 0)
            with sqlite3.connect(database) as db:
                self.assertEqual(db.execute('SELECT event_id FROM outbox WHERE delivered=0 ORDER BY rowid').fetchall(), [('bad',), ('last',)])

    def test_backlog_is_batched_in_order_and_recovers_after_acknowledgements(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'outbox.sqlite'
            with sqlite3.connect(database) as db:
                db.execute('CREATE TABLE outbox (event_id TEXT PRIMARY KEY, job_id TEXT, revision INTEGER, payload TEXT, delivered INTEGER DEFAULT 0)')
                for i in range(105):
                    event = {'event_id': str(i), 'job_id': 'job', 'revision': i + 1, 'text': '测试'}
                    db.execute('INSERT INTO outbox VALUES (?, ?, ?, ?, 0)', (str(i), 'job', i + 1, json.dumps(event)))
            def call(request):
                return json.loads(subprocess.check_output([sys.executable, str(BRIDGE), str(database)], input=json.dumps(request).encode()))
            first = call({'command': 'pending'})['events']
            self.assertEqual([e['event_id'] for e in first], [str(i) for i in range(100)])
            self.assertEqual(call({'command': 'pending'})['events'], first)
            for event in first:
                call({'command': 'acknowledge', 'event_id': event['event_id']})
            self.assertEqual([e['event_id'] for e in call({'command': 'pending'})['events']], [str(i) for i in range(100, 105)])

    def test_byte_limit_leaves_later_events_pending(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'outbox.sqlite'
            with sqlite3.connect(database) as db:
                db.execute('CREATE TABLE outbox (event_id TEXT PRIMARY KEY, job_id TEXT, revision INTEGER, payload TEXT, delivered INTEGER DEFAULT 0)')
                for i in range(3):
                    event = {'event_id': str(i), 'job_id': 'job', 'revision': i + 1, 'text': 'x' * 600000}
                    db.execute('INSERT INTO outbox VALUES (?, ?, ?, ?, 0)', (str(i), 'job', i + 1, json.dumps(event)))
            result = subprocess.check_output([sys.executable, str(BRIDGE), str(database)], input=b'{"command":"pending"}')
            self.assertLessEqual(len(result) - 1, 1024 * 1024)
            self.assertEqual([e['event_id'] for e in json.loads(result)['events']], ['0'])
