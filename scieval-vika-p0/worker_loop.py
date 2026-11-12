"""Deterministic single-job primitives. No IM, HTML generation or autonomous approval."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from contracts import load_rubrics, validate_plan
from framework import Conflict, snapshot, validate_report


def bind_task(store, job_id, revision, task_id):
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError('task_id required')
    def mutate(state):
        if state['task_id'] not in (None, task_id):
            raise Conflict('job already assigned to another task')
        if state['state'] != 'received':
            raise Conflict('cannot change task after work starts')
        state['task_id'] = task_id
    return store.update_workflow(job_id, revision, mutate)


def prepare_round(store, job_id, revision, workspace, content, plan=None):
    """Persist immutable snapshot and submission intent before any network call."""
    state = store.get_full(job_id)
    if state['revision'] != revision:
        raise Conflict('stale revision')
    if not state['task_id'] or state['state'] not in ('received', 'revising'):
        raise Conflict('job is not ready to prepare')
    number = state['round'] + int(state['state'] == 'revising')
    selected = state['improvement_plan'] if number == 1 else plan
    validate_plan(selected, round_number=number)
    if number > 1 and selected['based_on_report_id'] != state.get('last_report_id'):
        raise ValueError('plan must reference the last report')
    required = set(state['category']['teacher_requirements']) | set(state['improvement_plan']['preserve'])
    if not required <= set(selected['preserve']):
        raise ValueError('plan must preserve teacher requirements and prior constraints')
    if not isinstance(content, bytes):
        raise ValueError('HTML content must be bytes')
    # job_id is validated by the store before accessing a caller-controlled path.
    artifact = snapshot(Path(workspace) / job_id / f'round_{number:03d}' / 'input.html', content)
    key = f'{job_id}:{number}:{artifact.sha256}'
    current = {'artifact_uri': artifact.uri, 'artifact_sha256': artifact.sha256,
               'idempotency_key': key, 'evaluation_id': None}
    def mutate(s):
        if s['state'] != state['state'] or s['task_id'] != state['task_id']:
            raise Conflict('job changed during preparation')
        s.update(state='preparing', round=number, current=current, improvement_plan=selected)
    return store.update_workflow(job_id, revision, mutate)


def submit_round(store, evaluator, job_id):
    """Retry an ambiguous submission with exactly the persisted key and bytes."""
    state = store.get_full(job_id)
    if state['state'] == 'evaluating' and state['current']['evaluation_id']:
        return state
    if state['state'] != 'preparing' or not state.get('current'):
        raise Conflict('no prepared submission')
    c = state['current']
    request = {'job_id': job_id, 'round': state['round'], 'uri': c['artifact_uri'],
               'sha256': c['artifact_sha256'],
               'rubric': {'id': 'scieval-equip-13', 'version': state['rubric_version']}}
    eid = evaluator.submit(request, c['idempotency_key'])
    if not isinstance(eid, str) or not eid:
        raise ValueError('invalid evaluation id')
    def mutate(s):
        if s['state'] != 'preparing' or s['current'] != c:
            raise Conflict('submission intent changed')
        s['current']['evaluation_id'] = eid
        s['state'] = 'evaluating'
    return store.update_workflow(job_id, state['revision'], mutate)


def apply_report(store, job_id, report_id, report):
    """Reject stale/mismatched reports without advancing the job."""
    if not isinstance(report_id, str) or not report_id.strip():
        raise ValueError('report_id required')
    encoded = json.dumps(report, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
    digest = hashlib.sha256(encoded).hexdigest()
    state = store.get_full(job_id)
    previous = state['processed_reports'].get(report_id)
    if previous is not None:
        if previous != digest:
            raise Conflict('report id reused with different content')
        return 'duplicate'
    current = state.get('current') or {}
    if state['state'] != 'evaluating':
        raise Conflict('job is not evaluating')
    validate_report(report, job_id=job_id, round_number=state['round'], input_sha256=current.get('artifact_sha256'))
    if report['evaluation_id'] != current.get('evaluation_id') or report['rubric_version'] != state['rubric_version']:
        raise ValueError('evaluation or rubric mismatch')
    decision = report['decision']['result']
    complete = all(item['status'] == 'scored' for item in report['criteria'])
    maximum = load_rubrics()['stop_policy']['max_rounds']
    target = 'revising' if decision == 'revise' and complete and state['round'] < maximum else 'awaiting_review'
    def mutate(s):
        if s['state'] != 'evaluating' or s['current'] != current:
            raise Conflict('job changed while applying report')
        s['processed_reports'][report_id] = digest
        s['last_report_id'] = report_id
        s['last_report'] = report
        s['state'] = target
        s['review_reason'] = None if target == 'revising' else ('incomplete' if not complete else 'max_rounds' if decision == 'revise' else decision)
    store.update_workflow(job_id, state['revision'], mutate)
    return target


def report_route(store, job_id, evaluation_id):
    state = store.get_full(job_id)
    if state['state'] != 'evaluating' or not state.get('task_id') or (state.get('current') or {}).get('evaluation_id') != evaluation_id:
        raise Conflict('no active matching task for evaluation')
    return state['task_id']
