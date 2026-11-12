"""Explicit composition and lifecycle for a local recovery scheduler.

The caller must start the daemon reservation gateway and provide the separate,
trusted accepted-event delivery adapter. Importing this module does no work.
"""
import threading
from recovery_scheduler import RecoveryScheduler
from reservation_gateway import ReservationGateway


class _Gateway:
    def __init__(self, reservations, delivery):
        self.reserve = reservations.reserve
        self.activate = reservations.activate
        self.accepted_scieval = delivery.accepted_scieval
        self.route_accepted = delivery.route_accepted


def start_recovery(*, store, registry, evaluator, delivery, socket_path, interval=30):
    """Start the explicit recovery thread; call stop() before closing dependencies."""
    gateway = _Gateway(ReservationGateway(socket_path), delivery)
    scheduler = RecoveryScheduler(store, gateway, evaluator, registry, interval=interval)
    thread = threading.Thread(target=scheduler.run, name='scieval-recovery', daemon=False)
    thread.start()
    return RecoveryRuntime(scheduler, thread)


class RecoveryRuntime:
    def __init__(self, scheduler, thread):
        self.scheduler, self.thread = scheduler, thread

    def stop(self, timeout=None):
        self.scheduler.stop()
        self.thread.join(timeout)
        if self.thread.is_alive():
            raise TimeoutError('recovery thread did not stop; keep dependencies open')
