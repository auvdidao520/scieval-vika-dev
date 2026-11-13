import hashlib
import json
import sqlite3
import tempfile
import threading
import unittest
from http.server import HTTPServer
from pathlib import Path

from http_evaluator import HttpEvaluator
from review_worker import review_worker_artifact, review_host_handoff
from server import make_handler
from test_workflow import Model


class WorkerReviewTests(unittest.TestCase):
    def test_worker_html_reaches_evaluator_and_retry_reuses_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            Model.calls = 0
            Model.bad = False
            model = HTTPServer(('127.0.0.1', 0), Model)
            dbpath = str(Path(tmp) / 'db')
            with sqlite3.connect(dbpath) as db:
                db.execute('CREATE TABLE evaluations (key TEXT PRIMARY KEY, digest TEXT NOT NULL, evaluation_id TEXT UNIQUE NOT NULL, report TEXT NOT NULL)')
            server = HTTPServer(('127.0.0.1', 0), make_handler(dbpath, 'secret', f'http://127.0.0.1:{model.server_port}', 'model-secret', 'test'))
            for instance in (model, server):
                threading.Thread(target=instance.serve_forever, daemon=True).start()
                self.addCleanup(instance.server_close)
                self.addCleanup(instance.shutdown)
            html = Path(tmp) / 'revised.html'
            html.write_text('<p>水会蒸发</p>', encoding='utf-8')
            token = Path(tmp) / 'token'
            token.write_text('secret')
            client = HttpEvaluator(f'http://127.0.0.1:{server.server_port}', str(token))
            args = dict(task_id='t1', job_id='lesson-1', round_number=2, html_path=html,
                        rubric={'id':'scieval-equip-13','version':'draft-1'}, policy_version='draft-1', pass_score=3)
            import base64
            raw = html.read_bytes()
            handoff = {'task_id': 't1', 'lesson_id': 'lesson', 'job_id': 'lesson-1', 'round': 2,
                       'rubric': args['rubric'], 'policy_version': 'draft-1', 'pass_score': 3, 'report_id': 1,
                       'artifact': {'media_type': 'text/html', 'sha256': hashlib.sha256(raw).hexdigest(),
                                    'content_base64': base64.b64encode(raw).decode()}}
            host_result = review_host_handoff(client, handoff)
            self.assertEqual((host_result['lesson_id'], host_result['report_id'], host_result['decision']), ('lesson', 1, 'human_review'))
            bad = {**handoff, 'artifact': {**handoff['artifact'], 'sha256': '0' * 64}}
            with self.assertRaises(ValueError): review_host_handoff(client, bad)
            self.assertEqual(review_host_handoff(client, handoff)['evaluation_id'], host_result['evaluation_id'])
            Model.calls = 0
            result = review_worker_artifact(client, **args)
            retry = review_worker_artifact(client, **args)
            self.assertEqual(Model.calls, 1)
            self.assertEqual(result['evaluation_id'], retry['evaluation_id'])
            self.assertEqual(result['artifact_sha256'], hashlib.sha256(html.read_bytes()).hexdigest())
            self.assertEqual(result['recommendation'], 'revise')
            self.assertEqual(result['decision'], 'human_review')
            self.assertEqual(len(result['criteria']), 13)
            self.assertTrue(all(x['result'] == 'revise' and x['evidence'][0]['quote'] == '水会蒸发' for x in result['criteria']))
            args['pass_score'] = 2
            passed = review_worker_artifact(client, **args)
            self.assertEqual(passed['recommendation'], 'pass')
            self.assertEqual(passed['decision'], 'human_review')

    def test_na_score_requires_human_review_and_zero_threshold_is_rejected(self):
        from framework import CRITERIA
        class Fake:
            def submit(self, request, key):
                self.request = request
                return 'evaluation'
            def query(self, evaluation_id):
                return {'schema_version': '1.0', 'evaluation_id': evaluation_id,
                        'job_id': 'j', 'round': 1, 'input_uri': self.request['uri'],
                        'input_sha256': self.request['sha256'], 'rubric_id': 'scieval-equip-13',
                        'rubric_version': 'draft-1', 'policy_version': 'draft-1',
                        'evaluator_version': 'test', 'created_at': 'now',
                        'criteria': [{'criterion_id': k, 'status': 'scored',
                                      'score': 0, 'evidence': [], 'reasoning': 'N/A',
                                      'recommendations': []} for k in CRITERIA],
                        'decision': {'result': 'human_review', 'reason': 'test'}}
        with tempfile.TemporaryDirectory() as tmp:
            html = Path(tmp) / 'lesson.html'
            html.write_text('<p>内容</p>')
            args = dict(task_id='t', job_id='j', round_number=1, html_path=html,
                        rubric={'id':'scieval-equip-13','version':'draft-1'}, policy_version='draft-1', pass_score=1)
            result = review_worker_artifact(Fake(), **args)
            self.assertEqual(result['recommendation'], 'human_review')
            self.assertTrue(all(item['result'] == 'human_review' for item in result['criteria']))
            args['pass_score'] = 0
            with self.assertRaises(ValueError): review_worker_artifact(Fake(), **args)

    def test_wrong_report_identity_rejected(self):
        class Fake:
            def submit(self, request, key): return 'expected'
            def query(self, evaluation_id): return {'job_id': 'wrong'}
        with tempfile.TemporaryDirectory() as tmp:
            html = Path(tmp) / 'lesson.html'
            html.write_text('<p>内容</p>')
            with self.assertRaises(ValueError):
                review_worker_artifact(Fake(), task_id='t', job_id='j', round_number=1, html_path=html,
                    rubric={'id':'scieval-equip-13','version':'draft-1'}, policy_version='draft-1', pass_score=2)

if __name__ == '__main__': unittest.main()
