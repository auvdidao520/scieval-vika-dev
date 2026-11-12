import hashlib, json, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from delivery_gateway import DeliveryGateway
from trusted_delivery import deliver_pending
from framework import Conflict

socket, filename, task, mode = sys.argv[1:]
report = {'report_id':'r1', 'job_id':'job1', 'round':1, 'evaluation_id':'eval1', 'score':42}
digest = hashlib.sha256(json.dumps(report, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
class Store:
    def get_full(self, job): return json.loads(Path(filename).read_text())
    def update_workflow(self, job, revision, mutate):
        state = self.get_full(job); assert state['revision'] == revision
        mutate(state); state['revision'] += 1
        Path(filename).write_text(json.dumps(state)); return state
if mode == 'init':
    intent = {'kind':'evaluation_report', 'event_id':'intent1', 'job_id':'job1', 'task_id':task, 'report_id':'r1', 'round':1, 'report':report, 'report_sha256':digest, 'delivered':False}
    Path(filename).write_text(json.dumps({'task_id':task, 'revision':0, 'outbox':[intent]})); sys.exit()
store = Store(); gateway = DeliveryGateway(socket)
if mode == 'tamper':
    state = store.get_full('job1'); state['outbox'][0]['report_sha256'] = '0'*64
    Path(filename).write_text(json.dumps(state))
    try: deliver_pending(store, gateway, 'job1')
    except Conflict: pass
    else: raise AssertionError('digest mismatch accepted')
    state['outbox'][0]['report_sha256'] = digest; Path(filename).write_text(json.dumps(state)); sys.exit()
if mode == 'crash':
    real = gateway.route_accepted
    def route_then_crash(*args):
        real(*args)
        os._exit(23)
    gateway.route_accepted = route_then_crash
    deliver_pending(store, gateway, 'job1'); raise AssertionError('expected crash')
assert deliver_pending(store, gateway, 'job1') == [('intent1', 'delivered')]
assert deliver_pending(store, gateway, 'job1') == []
