"""Single-process recovery driver; dependencies must be trusted local adapters.

The caller supplies a durable registry of (job_id, origin_seq) pairs. Starting
and stopping the driver is explicit; importing this module starts no threads.
"""
from __future__ import annotations

import logging
import threading

from task_recovery import recover_task_binding
from trusted_delivery import deliver_pending
from worker_loop import reconcile_report, submit_round


class RecoveryScheduler:
    def __init__(self, store, gateway, evaluator, registry, *, interval=30, logger=None):
        if interval <= 0:
            raise ValueError('interval must be positive')
        self.store, self.gateway, self.evaluator, self.registry = store, gateway, evaluator, registry
        self.interval = interval
        self.logger = logger or logging.getLogger(__name__)
        self._stop = threading.Event()
        self._round_lock = threading.Lock()

    def run_once(self):
        """Run one scan. A failure on one job does not prevent other jobs.

        Discovery failure propagates: treating it as an empty registry would hide
        an outage. All state-changing operations are retried on the next scan.
        """
        if not self._round_lock.acquire(blocking=False):
            return {'skipped': 'scan_in_progress', 'jobs': {}}
        try:
            jobs = list(self.registry.list_jobs())
            results = {}
            seen = set()
            for entry in jobs:
                try:
                    job, seq = entry
                    if not isinstance(job, str) or not job or isinstance(seq, bool) or not isinstance(seq, int) or seq < 1:
                        raise ValueError('invalid registry entry')
                    if job in seen:
                        raise ValueError('duplicate job in registry')
                    seen.add(job)
                    state = self.store.get_full(job)
                    if state['state'] == 'received':
                        recover_task_binding(self.store, self.gateway, job, seq)
                    state = self.store.get_full(job)
                    if state['state'] == 'preparing':
                        submit_round(self.store, self.evaluator, job)
                    state = self.store.get_full(job)
                    if state['state'] == 'evaluating':
                        reconcile_report(self.store, self.evaluator, job)
                    results[job] = {'deliveries': deliver_pending(self.store, self.gateway, job)}
                except Exception as exc:
                    key = entry[0] if isinstance(entry, (tuple, list)) and entry and isinstance(entry[0], str) else repr(entry)
                    self.logger.exception('recovery failed for job %s', key)
                    results[key] = {'error': type(exc).__name__}
            return {'jobs': results}
        finally:
            self._round_lock.release()

    def run(self):
        """Scan immediately, then periodically, until stop() is called."""
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:
                self.logger.exception('recovery scan failed')
            self._stop.wait(self.interval)

    def stop(self):
        self._stop.set()
