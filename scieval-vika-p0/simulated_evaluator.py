"""Synthetic evaluator for development only; no claims about educational quality."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from framework import CRITERIA, Conflict

class SimulatedEvaluator:
    def __init__(self, fixture_path):
        self.fixture = json.loads(Path(fixture_path).read_text(encoding='utf-8'))
        self.requests = {}

    def submit(self, request, idempotency_key):
        required = ('job_id', 'round', 'uri', 'sha256')
        if any(key not in request for key in required):
            raise ValueError('missing request field')
        content = Path(request['uri']).read_bytes()
        if hashlib.sha256(content).hexdigest() != request['sha256']:
            raise ValueError('artifact digest mismatch')
        if str(request['round']) not in self.fixture['rounds']:
            raise ValueError('no synthetic fixture for round')
        frozen = json.dumps(request, sort_keys=True, ensure_ascii=False)
        if idempotency_key in self.requests:
            old, eid = self.requests[idempotency_key]
            if old != frozen:
                raise Conflict('idempotency key reused for different request')
            return eid
        eid = 'sim-' + hashlib.sha256(idempotency_key.encode()).hexdigest()[:24]
        self.requests[idempotency_key] = (frozen, eid)
        return eid

    def query(self, evaluation_id):
        match = next((frozen for frozen, eid in self.requests.values() if eid == evaluation_id), None)
        if match is None:
            raise KeyError(evaluation_id)
        request = json.loads(match)
        config = self.fixture['rounds'][str(request['round'])]
        return {'schema_version':'1.0', 'evaluation_id':evaluation_id,
                'job_id':request['job_id'], 'round':request['round'],
                'input_uri':request['uri'], 'input_sha256':request['sha256'],
                'rubric_id':'scieval-equip-13', 'rubric_version':'synthetic',
                'policy_version':'synthetic', 'evaluator_version':self.fixture['fixture_version'],
                'created_at':datetime.now(timezone.utc).isoformat(),
                'synthetic':True, 'provenance':self.fixture['provenance'],
                'criteria':[{'criterion_id':criterion, 'status':'scored', 'score':config['default_score'],
                             'evidence':[], 'reasoning':'模拟评分，不对应真实课件证据',
                             'recommendations':['模拟建议：仅供流程测试']}
                            for criterion in sorted(CRITERIA)],
                'decision':{'result':config['decision'], 'reason':'预设的模拟结果，不能用于真实评价或发布'}}
