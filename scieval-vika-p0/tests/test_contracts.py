import copy
import unittest
from contracts import load_rubrics, validate_category, validate_plan

class ContractTests(unittest.TestCase):
    def setUp(self):
        self.config = load_rubrics()
        self.category = {'category': 'experiment_inquiry', 'category_version': self.config['version'], 'subject': 'science', 'grade': '初二', 'confidence': .91, 'teacher_requirements': ['保留三个活动'], 'needs_teacher_confirmation': False}
        self.plan = {'plan_id': 'p1', 'based_on_report_id': None, 'target_criteria': ['II.B'], 'actions': [{'criterion_id': 'II.B', 'change': '收集学生解释', 'expected_evidence': '记录初始观点', 'priority': 'P0'}], 'preserve': ['保留三个活动']}

    def test_valid_contracts(self):
        validate_category(self.category, self.config)
        validate_plan(self.plan, round_number=1)
        self.assertIn('II.C', self.config['categories']['experiment_inquiry']['always_required'])

    def test_invalid_categories(self):
        for changes in ({'category': 'unknown'}, {'category_version': 'old'}, {'confidence': float('nan')}, {'confidence': True}, {'grade': ''}, {'category': 'other'}, {'extra': 1}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_category({**self.category, **changes}, self.config)
        validate_category({**self.category, 'category': 'other', 'needs_teacher_confirmation': True}, self.config)

    def test_plan_report_link_and_actions(self):
        for change in ({'based_on_report_id': None}, {'based_on_report_id': ''}):
            with self.assertRaises(ValueError): validate_plan({**self.plan, **change}, round_number=2)
        validate_plan({**self.plan, 'based_on_report_id': 'report-1'}, round_number=2)
        bad = copy.deepcopy(self.plan)
        bad['actions'][0]['criterion_id'] = 'II.C'
        with self.assertRaises(ValueError): validate_plan(bad, round_number=1)
        with self.assertRaises(ValueError): validate_plan({**self.plan, 'target_criteria': ['II.B', 'II.B']}, round_number=1)
