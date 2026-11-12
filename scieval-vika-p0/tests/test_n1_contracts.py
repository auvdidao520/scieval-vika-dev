import copy
import json
import tempfile
import unittest
from pathlib import Path
from contracts import (CONTRACT_VERSION, category_gate, load_rubrics,
                       validate_category, validate_plan, validate_task_binding)


class N1ContractTests(unittest.TestCase):
    def setUp(self):
        self.config = load_rubrics()
        self.category = dict(category='experiment_inquiry', category_version=self.config['version'],
                             subject='science', grade='初二', confidence=.91,
                             teacher_requirements=['保留三个活动'], needs_teacher_confirmation=False)
        self.plan = dict(plan_id='J1-r2', based_on_report_id='report-1', target_criteria=['II.B'],
                         actions=[dict(criterion_id='II.B', change='收集学生解释',
                                       expected_evidence='初始观点记录', priority='P0')],
                         preserve=['保留三个活动'])
        self.binding = dict(schema_version=1, job_id='J1', task_id='t17', source_event_id='lark:event:1')

    def gate(self, candidate, **changes):
        return category_gate(candidate, self.config, **dict(min_confidence=.8, policy_confirmed=True, **changes))

    def test_all_categories_keep_scientific_accuracy_and_full_report(self):
        from framework import CRITERIA
        self.assertEqual(len(CRITERIA), 13)
        for name in self.config['categories']:
            validate_category({**self.category, 'category': name, 'needs_teacher_confirmation': name == 'other'}, self.config)
            self.assertIn('II.C', self.config['categories'][name]['always_required'])

    def test_missing_input_asks_one_question(self):
        result = self.gate(None)
        self.assertEqual(result['status'], 'needs_confirmation')
        self.assertEqual(result['reason'], 'missing_category')
        self.assertTrue(result['question'])

    def test_unconfirmed_policy_never_ready(self):
        for kwargs in ({}, {'min_confidence': .8}, {'policy_confirmed': True}):
            self.assertEqual(category_gate(self.category, self.config, **kwargs)['reason'], 'unconfirmed_policy')

    def test_low_confidence_and_boundary(self):
        for value in (0, .799):
            self.assertEqual(self.gate({**self.category, 'confidence': value})['reason'], 'low_confidence')
        for value in (.8, 1):
            self.assertEqual(self.gate({**self.category, 'confidence': value})['status'], 'ready')

    def test_other_and_explicit_confirmation_block_ready(self):
        self.assertEqual(self.gate({**self.category, 'category': 'other', 'needs_teacher_confirmation': True})['reason'], 'other_category')
        self.assertEqual(self.gate({**self.category, 'needs_teacher_confirmation': True})['reason'], 'teacher_confirmation')

    def test_bad_policy_parameters_fail_closed(self):
        for threshold in (True, -1, 1.1, float('inf'), float('nan'), '0.8'):
            with self.subTest(threshold=threshold), self.assertRaises(ValueError):
                category_gate(self.category, self.config, min_confidence=threshold)
        with self.assertRaises(ValueError):
            category_gate(self.category, self.config, policy_confirmed='yes')

    def test_malformed_proposal_is_not_silently_accepted(self):
        for candidate in ([], {}, {**self.category, 'confidence': False}, {**self.category, 'subject': ''}):
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                self.gate(candidate)

    def test_binding_roundtrip(self):
        validate_task_binding(json.loads(json.dumps(self.binding)))

    def test_binding_rejects_path_and_type_confusion(self):
        for field, value in (('job_id', '../J1'), ('job_id', ''), ('job_id', 'x'*65),
                             ('task_id', '/tmp/task'), ('task_id', None),
                             ('schema_version', True), ('schema_version', 2),
                             ('source_event_id', '')):
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                validate_task_binding({**self.binding, field: value})
        with self.assertRaises(ValueError):
            validate_task_binding({**self.binding, 'note': 'not a plan'})

    def test_plan_requires_exact_previous_report_when_context_given(self):
        validate_plan(self.plan, round_number=2, expected_report_id='report-1')
        with self.assertRaisesRegex(ValueError, 'plan report identity mismatch'):
            validate_plan(self.plan, round_number=2, expected_report_id='report-other')

    def test_every_plan_target_needs_an_action(self):
        with self.assertRaisesRegex(ValueError, 'every target criterion requires an action'):
            validate_plan({**self.plan, 'target_criteria': ['II.B', 'III.B']}, round_number=2)

    def test_invalid_plan_fields(self):
        for field, value in (('plan_id', ''), ('actions', []), ('target_criteria', ['unknown']),
                             ('preserve', [12]), ('based_on_report_id', None)):
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_plan({**self.plan, field: value}, round_number=2)
        for number in (True, 0, -1, 1.5):
            with self.assertRaises(ValueError): validate_plan(self.plan, round_number=number)

    def test_rubric_rejects_redline_removal_and_unsafe_stop(self):
        mutations = [lambda c: c['categories']['review'].update(always_required=[]),
                     lambda c: c['categories']['review'].update(required_focus=['unknown']),
                     lambda c: c['categories']['review'].update(required_focus=['II.B', 'II.B']),
                     lambda c: c['stop_policy'].update(require_teacher_approval=False),
                     lambda c: c['stop_policy'].update(require_all_criteria_scored=False),
                     lambda c: c['stop_policy'].update(max_rounds=True),
                     lambda c: c.update(version='')]
        for mutation in mutations:
            config = copy.deepcopy(self.config); mutation(config)
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory)/'rubric.yaml'; path.write_text(json.dumps(config))
                with self.assertRaises(ValueError): load_rubrics(path)

    def test_version_file_matches_contract(self):
        self.assertEqual(Path(__file__).resolve().parents[2].joinpath('VERSION').read_text().strip(), CONTRACT_VERSION)
