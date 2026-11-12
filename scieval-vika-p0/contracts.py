"""Draft classification and improvement-plan contracts; no scoring decisions."""
from __future__ import annotations
import json
import math
from pathlib import Path
from framework import CRITERIA


def _object(value, keys, name):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError(f'{name}: unexpected or missing fields')


def _text(value, name):
    if not isinstance(value, str) or not value.strip() or len(value) > 4000:
        raise ValueError(f'{name}: expected nonempty bounded text')


def _texts(value, name):
    if not isinstance(value, list) or len(value) > 100:
        raise ValueError(f'{name}: expected bounded list')
    for item in value:
        _text(item, name)


def load_rubrics(path=None):
    # JSON is a YAML subset and keeps the prototype standard-library only.
    config = json.loads(Path(path or Path(__file__).with_name('category_rubrics.yaml')).read_text(encoding='utf-8'))
    _object(config, ('version', 'categories', 'stop_policy'), 'rubric')
    _text(config['version'], 'version')
    categories = config['categories']
    if not isinstance(categories, dict) or set(categories) != {'new_lesson', 'experiment_inquiry', 'review', 'project_based', 'other'}:
        raise ValueError('invalid category set')
    for category, rules in categories.items():
        _object(rules, ('required_focus', 'always_required'), category)
        for key in ('required_focus', 'always_required'):
            _texts(rules[key], key)
            if len(set(rules[key])) != len(rules[key]) or not set(rules[key]) <= CRITERIA:
                raise ValueError('invalid rubric criteria')
        if 'II.C' not in rules['always_required']:
            raise ValueError('scientific accuracy must remain required')
    policy = config['stop_policy']
    _object(policy, ('max_rounds', 'require_all_criteria_scored', 'require_teacher_approval'), 'stop_policy')
    if type(policy['max_rounds']) is not int or policy['max_rounds'] < 1 or policy['require_all_criteria_scored'] is not True or policy['require_teacher_approval'] is not True:
        raise ValueError('unsafe stop policy')
    return config


def validate_category(category, config):
    _object(category, ('category', 'category_version', 'subject', 'grade', 'confidence', 'teacher_requirements', 'needs_teacher_confirmation'), 'category')
    if not isinstance(category['category'], str) or category['category'] not in config['categories'] or category['category_version'] != config['version']:
        raise ValueError('unknown category or version')
    for field in ('subject', 'grade'):
        _text(category[field], field)
    confidence = category['confidence']
    if type(confidence) not in (float, int) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise ValueError('invalid confidence')
    _texts(category['teacher_requirements'], 'teacher_requirements')
    if type(category['needs_teacher_confirmation']) is not bool:
        raise ValueError('confirmation must be boolean')
    if category['category'] == 'other' and not category['needs_teacher_confirmation']:
        raise ValueError('other requires teacher confirmation')


def validate_plan(plan, *, round_number):
    _object(plan, ('plan_id', 'based_on_report_id', 'target_criteria', 'actions', 'preserve'), 'plan')
    _text(plan['plan_id'], 'plan_id')
    if type(round_number) is not int or round_number < 1:
        raise ValueError('invalid round')
    if round_number == 1:
        if plan['based_on_report_id'] is not None:
            raise ValueError('initial plan cannot cite a report')
    else:
        _text(plan['based_on_report_id'], 'based_on_report_id')
    _texts(plan['target_criteria'], 'target_criteria')
    targets = plan['target_criteria']
    if not targets or len(targets) != len(set(targets)) or not set(targets) <= CRITERIA:
        raise ValueError('invalid target criteria')
    if not isinstance(plan['actions'], list) or not plan['actions'] or len(plan['actions']) > 100:
        raise ValueError('invalid actions')
    for action in plan['actions']:
        _object(action, ('criterion_id', 'change', 'expected_evidence', 'priority'), 'action')
        if not isinstance(action['criterion_id'], str) or action['criterion_id'] not in targets or action['priority'] not in ('P0', 'P1', 'P2'):
            raise ValueError('invalid action criterion or priority')
        _text(action['change'], 'change')
        _text(action['expected_evidence'], 'expected_evidence')
    _texts(plan['preserve'], 'preserve')
