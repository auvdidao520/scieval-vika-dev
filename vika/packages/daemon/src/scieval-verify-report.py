# SPDX-License-Identifier: AGPL-3.0
"""Verify an accepted report against the trusted persisted outbox."""
import hashlib
import json
import sys

try:
    data = json.load(sys.stdin)
    state, row, job, task = (data[k] for k in ('state', 'row', 'job', 'task'))
    event = json.loads(row['event_json'])
    report = json.loads(event['content']['text'])
    report_id = report['report_id']
    intents = [i for i in state['outbox'] if i.get('kind') == 'evaluation_report'
               and i.get('job_id') == job and i.get('task_id') == task
               and i.get('report_id') == report_id]
    assert state['schema_version'] == 2 and state['job_id'] == job and state['task_id'] == task
    assert len(intents) == 1
    intent = intents[0]
    digest = hashlib.sha256(json.dumps(report, sort_keys=True, ensure_ascii=False,
                                      separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    assert row['connector_instance'] == event['instance']
    assert event['connector'] == 'scieval' and event['kind'] == 'evaluation.completed'
    assert event['id'] == row['event_id'] == 'evaluation.completed:' + report_id
    assert event['subject'] == {'type': 'scieval_job', 'id': job}
    assert report['job_id'] == job and report['round'] == intent['round']
    assert report['evaluation_id'] == intent['report']['evaluation_id']
    assert digest == intent['report_sha256']
except (Exception, AssertionError):
    sys.exit(1)
