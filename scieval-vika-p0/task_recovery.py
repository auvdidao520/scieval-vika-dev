"""Recover a task binding through a privileged, idempotent gateway.

The gateway must validate accepted origin input, atomically reserve one task per
job, and keep that task inactive until activation. It is not implemented here.
"""
from framework import Conflict
from worker_loop import bind_task


def recover_task_binding(store, gateway, job_id, origin_seq):
    """Reserve, persist the association, then activate; all steps may be retried."""
    if isinstance(origin_seq, bool) or not isinstance(origin_seq, int) or origin_seq < 1:
        raise ValueError('accepted origin sequence required')
    state = store.get_full(job_id)
    if state['state'] != 'received':
        raise Conflict('task association can only be recovered before work starts')
    reservation = gateway.reserve(job_id, origin_seq)
    if not isinstance(reservation, dict) or reservation.get('job_id') != job_id or reservation.get('origin_seq') != origin_seq:
        raise Conflict('task reservation identity mismatch')
    task_id = reservation.get('task_id')
    if not isinstance(task_id, str) or not task_id.strip():
        raise Conflict('invalid reserved task')
    if state['task_id'] is not None and state['task_id'] != task_id:
        raise Conflict('persisted task differs from reservation')
    if state['task_id'] is None:
        try:
            state = bind_task(store, job_id, state['revision'], task_id)
        except Conflict:
            state = store.get_full(job_id)
            if state['state'] != 'received' or state['task_id'] != task_id:
                raise
    gateway.activate(job_id, origin_seq, task_id)
    return state
