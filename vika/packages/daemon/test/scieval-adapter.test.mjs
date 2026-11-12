import test from 'node:test';
import assert from 'node:assert/strict';
import { createSciEvalAdapter, harnessSubmitter, toRuntimeEvent } from '../src/scieval/adapter.mjs';
import { normalizeEvent } from '../src/event.mjs';

const event = { event_id: 'event-1', job_id: 'job-1', revision: 1, from: 'received', to: 'preparing', round: 1 };
function fixture() {
  const pending = [event]; const rows = new Map();
  const outbox = { pending: async () => [...pending], acknowledge: async (id) => { assert.equal(id, pending[0].event_id); pending.shift(); } };
  const store = { getInboundByEvent: { get: (instance, id) => rows.get(`${instance}:${id}`) } };
  const harness = { handleConnectorEvent: async (envelope) => {
    normalizeEvent(envelope);
    const key = `${envelope.instance}:${envelope.id}`;
    if (!rows.has(key)) rows.set(key, { event_json: JSON.stringify(envelope), filter_reason: null });
  } };
  return { pending, rows, outbox, store, harness };
}

test('disabled adapter has no side effects', async () => {
  const adapter = createSciEvalAdapter({ outbox: { pending() { throw Error('called'); } } });
  assert.deepEqual(await adapter.drain(), { enabled: false, delivered: 0 });
});
test('maps P1 events to valid Runtime envelopes without reply authority', () => {
  const envelope = toRuntimeEvent(event, 123);
  assert.equal(normalizeEvent(envelope).scopeKey, JSON.stringify(['scieval', 'job', 'job-1']));
  assert.equal(envelope.reply_route, null);
  assert.deepEqual(envelope.data.scieval, event);
  assert.throws(() => toRuntimeEvent({ ...event, revision: 0 }), /invalid/);
});
test('persists before acknowledgement and safely replays after ack failure', async () => {
  const f = fixture(); let fail = true;
  const outbox = { ...f.outbox, acknowledge: async (id) => { if (fail) { fail = false; throw Error('ack failed'); } await f.outbox.acknowledge(id); } };
  const adapter = createSciEvalAdapter({ enabled: true, outbox, submit: harnessSubmitter(f.harness, f.store) });
  await assert.rejects(adapter.drain(), /ack failed/);
  assert.equal(f.pending.length, 1); assert.equal(f.rows.size, 1);
  assert.equal((await adapter.drain()).delivered, 1);
  assert.equal(f.pending.length, 0); assert.equal(f.rows.size, 1);
});
test('silent drops, rejected events and id collisions never acknowledge', async () => {
  for (const mode of ['drop', 'filtered', 'collision']) {
    const f = fixture();
    if (mode !== 'drop') f.rows.set('scieval:event-1', { filter_reason: mode === 'filtered' ? 'stale_event' : null,
      event_json: JSON.stringify(toRuntimeEvent({ ...event, job_id: 'other' }, 123)) });
    f.harness.handleConnectorEvent = async () => {};
    const adapter = createSciEvalAdapter({ enabled: true, outbox: f.outbox, submit: harnessSubmitter(f.harness, f.store) });
    await assert.rejects(adapter.drain()); assert.equal(f.pending.length, 1);
  }
});
test('requires explicit durable acceptance', async () => {
  const f = fixture();
  const adapter = createSciEvalAdapter({ enabled: true, outbox: f.outbox, submit: async () => undefined });
  await assert.rejects(adapter.drain(), /confirm acceptance/); assert.equal(f.pending.length, 1);
});
