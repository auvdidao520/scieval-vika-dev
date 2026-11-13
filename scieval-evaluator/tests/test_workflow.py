import base64
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import LABELS, make_handler

class Model(BaseHTTPRequestHandler):
    calls = 0
    bad = False
    def log_message(self, *args): pass
    def do_POST(self):
        type(self).calls += 1
        data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        assert data['messages'][1]['content'] == '<p>水会蒸发</p>'
        quote = '伪造' if type(self).bad else '水会蒸发'
        criteria = [{'criterion_id':k, 'status':'scored', 'score':2, 'evidence':[{'quote':quote,'locator':'p'}], 'reasoning':'说明', 'recommendations':['建议']} for k in LABELS]
        raw = json.dumps({'choices':[{'message':{'content':json.dumps({'criteria':criteria}, ensure_ascii=False)}}]}).encode()
        self.send_response(200); self.end_headers(); self.wfile.write(raw)

class WorkflowTest(unittest.TestCase):
    def test_http_cli_idempotency_and_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            Model.calls = 0; Model.bad = False
            model = HTTPServer(('127.0.0.1', 0), Model)
            dbpath = str(Path(tmp)/'db.sqlite')
            with sqlite3.connect(dbpath) as db:
                db.execute('CREATE TABLE evaluations (key TEXT PRIMARY KEY, digest TEXT NOT NULL, evaluation_id TEXT UNIQUE NOT NULL, report TEXT NOT NULL)')
            server = HTTPServer(('127.0.0.1', 0), make_handler(dbpath, 'secret', f'http://127.0.0.1:{model.server_port}', 'model-secret', 'test'))
            for s in (model, server): threading.Thread(target=s.serve_forever, daemon=True).start()
            self.addCleanup(model.server_close); self.addCleanup(server.server_close)
            self.addCleanup(model.shutdown); self.addCleanup(server.shutdown)
            html = Path(tmp)/'lesson.html'; html.write_text('<p>水会蒸发</p>')
            token = Path(tmp)/'token'; token.write_text('secret')
            base = f'http://127.0.0.1:{server.server_port}'
            cli = [sys.executable, str(Path(__file__).resolve().parents[1]/'evaluate.py'), str(html), '--url',base,'--token-file',str(token),'--job-id','job-1']
            for i in range(2):
                out = Path(tmp)/f'report{i}.json'
                subprocess.run(cli + ['--output',str(out)], check=True, capture_output=True)
                report = json.loads(out.read_text()); self.assertEqual(report['decision']['result'], 'human_review'); self.assertEqual(len(report['criteria']),13)
            self.assertEqual(Model.calls, 1)
            raw = html.read_bytes()
            payload = {'schema_version':'1.0','job_id':'other','round':1,'artifact':{'uri':str(html),'sha256':hashlib.sha256(raw).hexdigest(),'media_type':'text/html','content_base64':base64.b64encode(raw).decode()},'rubric':{'id':'scieval-equip-13','version':'draft-1'},'policy_version':'draft-1'}
            Model.bad = True
            req = Request(base+'/v1/evaluations',data=json.dumps(payload).encode(),headers={'Authorization':'Bearer secret','Idempotency-Key':'other'},method='POST')
            with self.assertRaises(HTTPError) as err: urlopen(req)
            self.assertEqual(err.exception.code,502)
            with sqlite3.connect(dbpath) as db: self.assertEqual(db.execute('SELECT count(*) FROM evaluations').fetchone()[0],1)

if __name__ == '__main__': unittest.main()
