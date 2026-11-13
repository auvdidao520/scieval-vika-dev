import base64
import hashlib
import json
import unittest
from unittest.mock import patch
from feedback import check_feedback, classify

class Response:
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self, _): return json.dumps({'choices':[{'message':{'content':json.dumps(self.result)}}]}).encode()

class FeedbackTests(unittest.TestCase):
    def setUp(self):
        self.html = '<section>错误的实验步骤</section>'
        raw = self.html.encode()
        self.data = {'schema_version':'1.0','job_id':'lesson','round':1,'rubric':{'id':'scieval-equip-13','version':'draft-1'},'policy_version':'draft-1','artifact':{'uri':'lesson.html','media_type':'text/html','content_base64':base64.b64encode(raw).decode(),'sha256':hashlib.sha256(raw).hexdigest()},'feedback':'实验步骤有误，也需要形成性评估'}
        self.item = {'criterion_id':'II.C','feedback_quote':'实验步骤有误','artifact_quote':'错误的实验步骤','confidence':.9,'acceptance':'核对步骤'}
    def run_model(self, result):
        response=Response(); response.result=result
        with patch('feedback.build_opener') as opener:
            opener.return_value.open.return_value=response
            return classify(self.data,check_feedback(self.data),'http://localhost','token','model')
    def test_multiple_issues_are_retained(self):
        second={'criterion_id':'III.B','feedback_quote':'形成性评估','artifact_quote':'错误的实验步骤','confidence':.8,'acceptance':'补充检测'}
        result=self.run_model({'issues':[self.item,second], 'clarification':''})
        self.assertEqual(result['status'],'human_review')
        self.assertEqual(len(result['issues']),2)
    def test_uncertain_evidence_requires_clarification(self):
        self.item['artifact_quote']=''
        self.assertEqual(self.run_model({'issues':[self.item], 'clarification':''})['status'],'needs_clarification')
    def test_invented_evidence_is_rejected(self):
        self.item['feedback_quote']='不存在'
        with self.assertRaises(ValueError): self.run_model({'issues':[self.item], 'clarification':''})
    def test_digest_mismatch_rejected(self):
        self.data['artifact']['sha256']='0'*64
        with self.assertRaises(ValueError): check_feedback(self.data)

if __name__ == '__main__': unittest.main()
