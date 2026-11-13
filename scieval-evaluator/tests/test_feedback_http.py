import base64
import hashlib
import json
import sqlite3
import tempfile
import threading
import unittest
from http.server import HTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from server import make_handler
from http_evaluator import HttpEvaluator, EvaluatorError


class FeedbackHTTPTests(unittest.TestCase):
    def test_client_classifies_captured_html_and_checks_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            dbpath = str(Path(tmp) / 'db')
            with sqlite3.connect(dbpath) as db:
                db.execute('CREATE TABLE feedback_classifications (key TEXT PRIMARY KEY, digest TEXT NOT NULL, result TEXT NOT NULL)')
            server = HTTPServer(('127.0.0.1', 0), make_handler(dbpath, 'secret', 'http://localhost', 'model', 'test'))
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            token = Path(tmp) / 'token'; token.write_text('secret')
            raw = '<p>实验步骤</p>'.encode()
            request = {'job_id':'job', 'round':1, 'uri':'lesson.html', 'content_bytes':raw,
                       'sha256':hashlib.sha256(raw).hexdigest(),
                       'rubric':{'id':'scieval-equip-13','version':'draft-1'}, 'policy_version':'draft-1'}
            result = {'job_id':'job', 'input_sha256':request['sha256'], 'rubric':request['rubric'],
                      'status':'human_review', 'issues':[], 'clarification':''}
            client = HttpEvaluator(f'http://127.0.0.1:{server.server_port}', str(token))
            with patch('feedback.classify', return_value=result) as classify:
                self.assertEqual(client.classify_feedback(request, '实验步骤有误', 'event-1'), result)
                self.assertEqual(client.classify_feedback(request, '实验步骤有误', 'event-1'), result)
                self.assertEqual(classify.call_count, 1)
                with self.assertRaises(EvaluatorError):
                    client.classify_feedback(request, '其他反馈', 'event-1')
                with self.assertRaises(ValueError):
                    client.classify_feedback(request, '', 'event-2')
            wrong = dict(result, job_id='different')
            with patch('feedback.classify', return_value=wrong):
                with self.assertRaisesRegex(EvaluatorError, 'identity'):
                    client.classify_feedback(request, '实验步骤有误', 'event-2')

    def test_idempotency_conflict_and_failed_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            dbpath = str(Path(tmp) / 'db')
            with sqlite3.connect(dbpath) as db:
                db.execute('CREATE TABLE feedback_classifications (key TEXT PRIMARY KEY, digest TEXT NOT NULL, result TEXT NOT NULL)')
            server = HTTPServer(('127.0.0.1', 0), make_handler(dbpath, 'secret', 'http://localhost', 'model', 'test'))
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            raw = '<p>实验步骤</p>'.encode()
            data = {'schema_version':'1.0', 'job_id':'job', 'round':1,
                    'rubric':{'id':'scieval-equip-13','version':'draft-1'}, 'policy_version':'draft-1',
                    'artifact':{'uri':'lesson.html','media_type':'text/html', 'content_base64':base64.b64encode(raw).decode(), 'sha256':hashlib.sha256(raw).hexdigest()},
                    'feedback':'实验步骤有误'}
            def post(payload, key='event-1', token='secret'):
                req = Request(f'http://127.0.0.1:{server.server_port}/v1/feedback/classifications',
                              data=json.dumps(payload).encode(), method='POST',
                              headers={'Authorization':'Bearer '+token, 'Idempotency-Key':key})
                with urlopen(req) as response:
                    return json.load(response)
            result = {'status':'human_review', 'issues':[]}
            with patch('feedback.classify', side_effect=[ValueError('bad'), result]) as classify:
                with self.assertRaises(HTTPError) as err: post(data, token='bad')
                self.assertEqual(err.exception.code, 401)
                with self.assertRaises(HTTPError) as err: post(data)
                self.assertEqual(err.exception.code, 502)
                self.assertEqual(post(data), result)
                self.assertEqual(post(data), result)
                self.assertEqual(classify.call_count, 2)
                changed = dict(data, feedback='其他反馈')
                with self.assertRaises(HTTPError) as err: post(changed)
                self.assertEqual(err.exception.code, 409)
                with self.assertRaises(HTTPError) as err: post(data, key='')
                self.assertEqual(err.exception.code, 400)
                self.assertEqual(classify.call_count, 2)


if __name__ == '__main__':
    unittest.main()
