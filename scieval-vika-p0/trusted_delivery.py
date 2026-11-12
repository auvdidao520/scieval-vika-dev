"""Reconcile report intents with an accepted Vika connector event.

The gateway is a privileged, deployment-provided adapter. It must resolve only
accepted Core ledger rows, and route those rows with Vika's task manager.
"""
from __future__ import annotations

import hashlib
import json
from framework import Conflict
from worker_loop import acknowledge_report_delivery, pending_report_deliveries


def deliver_pending(store, gateway, job_id):
    """Route matching accepted events; leave intents pending when none exists.

    The gateway returns full event envelopes from the durable accepted ledger,
    not arbitrary search results or a caller-supplied event ID. Route must be
    idempotent by (task, accepted sequence); exceptions leave intent pending.
    """
    state = store.get_full(job_id)
    outcomes = []
    for intent in pending_report_deliveries(store, job_id):
        if intent['job_id'] != job_id or intent['task_id'] != state['task_id']:
            raise Conflict('delivery binding mismatch')
        matches = gateway.accepted_scieval(intent['report_id'])
        if not matches:
            outcomes.append((intent['event_id'], 'awaiting_accepted_event'))
            continue
        if len(matches) != 1:
            raise Conflict('ambiguous accepted event')
        row = matches[0]
        event = row['event']
        report = json.loads(event['content']['text'])
        digest = hashlib.sha256(json.dumps(report, sort_keys=True, ensure_ascii=False,
                                            separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        if (row.get('status') != 'DONE' or not isinstance(row.get('seq'), int)
            or row['seq'] < 1 or row.get('connector_instance') != event.get('instance')
            or event.get('connector') != 'scieval'
            or event.get('kind') != 'evaluation.completed'
            or event.get('id') != 'evaluation.completed:' + intent['report_id']
            or event.get('subject') != {'type': 'scieval_job', 'id': job_id}
            or report.get('report_id') != intent['report_id']
            or report.get('job_id') != job_id
            or report.get('round') != intent['round']
            or report.get('evaluation_id') != intent['report'].get('evaluation_id')
            or digest != intent['report_sha256']):
            raise Conflict('accepted event does not match delivery intent')
        gateway.route_accepted(intent['task_id'], row, 'owner')
        acknowledge_report_delivery(store, job_id, intent['event_id'], intent['task_id'])
        outcomes.append((intent['event_id'], 'delivered'))
    return outcomes
