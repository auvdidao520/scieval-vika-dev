import base64
import hashlib
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from framework import Conflict, snapshot, validate_report, CRITERIA
from http_evaluator import EvaluatorError, HttpEvaluator


def report(job_id, round_number, digest):
    return {
        'schema_version': '1.0', 'evaluation_id': 'eval-1', 'job_id': job_id,
        'round': round_number, 'input_uri': 'artifact://job/1', 'input_sha256': digest,
        'rubric_id': 'scieval-equip-13', 'rubric_version': 'draft-1',
        'policy_version': 'draft-1', 'evaluator_version': 'test', 'created_at': '2026-11-12T06:00:00Z',
        'criteria': [{'criterion_id': code, 'status': 'scored', 'score': 3,
                      'evidence': [], 'reasoning': 'fixture', 'recommendations': []} for code in CRITERIA],
        'decision': {'result': 'pass', 'reason': 'fixture'},
    }


class EvaluatorHandler(BaseHTTPRequestHandler):
    requests = []
    report_bytes = b''

    def log_message(self, *_args):
        pass

    def send_json(self, value, status=200, raw=None):
        body = raw if raw is not None else json.dumps(value, separators=(',', ':')).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get('Content-Length', '0'))
        payload = json.loads(self.rfile.read(length))
        type(self).requests.append({'method': 'POST', 'path': self.path, 'headers': dict(self.headers), 'payload': payload})
        self.send_json({'evaluation_id': 'eval-1', 'status': 'queued'}, 202)

    def do_GET(self):
        type(self).requests.append({'method': 'GET', 'path': self.path, 'headers': dict(self.headers)})
        if self.path == '/v1/evaluations/eval-1':
            self.send_json({'status': 'succeeded', 'report_url': '/v1/reports/eval-1',
                            'report_sha256': hashlib.sha256(type(self).report_bytes).hexdigest()})
        elif self.path == '/v1/reports/eval-1':
            self.send_json({}, raw=type(self).report_bytes)
        else:
            self.send_json({'error': 'not found'}, 404)


class HttpEvaluatorTests(unittest.TestCase):
    def setUp(self):
        EvaluatorHandler.requests = []
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), EvaluatorHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.temp = tempfile.TemporaryDirectory()
        self.token = Path(self.temp.name) / 'token'
        self.token.write_text('secret', encoding='utf-8')
        self.client = HttpEvaluator(f'http://127.0.0.1:{self.server.server_port}', str(self.token))

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def test_uploads_html_and_verifies_same_origin_report(self):
        artifact = snapshot(Path(self.temp.name) / 'lesson.html', '<p>教学设计</p>'.encode())
        request = {'job_id': 'job-1', 'round': 1, 'uri': artifact.uri, 'sha256': artifact.sha256}
        evaluation_id = self.client.submit(request, 'job-1:1:' + artifact.sha256)
        self.assertEqual(evaluation_id, 'eval-1')
        posted = EvaluatorHandler.requests[0]
        self.assertEqual(posted['headers']['Idempotency-Key'], 'job-1:1:' + artifact.sha256)
        self.assertEqual(posted['headers']['Authorization'], 'Bearer secret')
        encoded = posted['payload']['artifact']['content_base64']
        self.assertEqual(base64.b64decode(encoded).decode(), '<p>教学设计</p>')
        EvaluatorHandler.report_bytes = json.dumps(report('job-1', 1, artifact.sha256), separators=(',', ':')).encode()
        result = self.client.query(evaluation_id)
        validate_report(result, job_id='job-1', round_number=1, input_sha256=artifact.sha256)

    def test_rejects_cross_origin_report_and_digest_mismatch(self):
        artifact = snapshot(Path(self.temp.name) / 'lesson.html', b'<p>draft</p>')
        request = {'job_id': 'job-1', 'round': 1, 'uri': artifact.uri, 'sha256': artifact.sha256}
        self.client.submit(request, 'key')
        original = EvaluatorHandler.do_GET
        def cross_origin(handler):
            if handler.path == '/v1/evaluations/eval-1':
                handler.send_json({'status': 'succeeded', 'report_url': 'https://attacker.invalid/report'})
            else:
                original(handler)
        EvaluatorHandler.do_GET = cross_origin
        with self.assertRaises(EvaluatorError):
            self.client.query('eval-1')
        EvaluatorHandler.do_GET = original
        EvaluatorHandler.report_bytes = b'{}'
        def bad_digest(handler):
            if handler.path == '/v1/evaluations/eval-1':
                handler.send_json({'status': 'succeeded', 'report_url': '/v1/reports/eval-1',
                                   'report_sha256': '0' * 64})
            else:
                original(handler)
        EvaluatorHandler.do_GET = bad_digest
        with self.assertRaises(Conflict):
            self.client.query('eval-1')
        EvaluatorHandler.do_GET = original


if __name__ == '__main__':
    unittest.main()
