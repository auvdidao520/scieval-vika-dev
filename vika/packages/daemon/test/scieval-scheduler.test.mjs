import test from 'node:test';
import assert from 'node:assert/strict';
import { createSciEvalScheduler } from '../src/scieval/scheduler.mjs';

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const logger = { warn() {} };

test('retries failures without overlapping and waits for an active drain on stop', async () => {
  let calls = 0;
  let concurrent = 0;
  let maxConcurrent = 0;
  let release;
  let entered;
  const started = new Promise((resolve) => { entered = resolve; });
  const scheduler = await createSciEvalScheduler({ pollIntervalMs: 5, logger,
    adapterFactory: async () => ({ async drain() {
      calls++;
      maxConcurrent = Math.max(maxConcurrent, ++concurrent);
      try {
        if (calls === 1) throw Error('temporary');
        entered();
        await new Promise((resolve) => { release = resolve; });
      } finally { concurrent--; }
    } }) });
  scheduler.start();
  scheduler.start();
  await started;
  await sleep(20);
  assert.equal(calls, 2);
  let stopped = false;
  const stopping = scheduler.stop().then(() => { stopped = true; });
  await sleep(5);
  assert.equal(stopped, false);
  release();
  await stopping;
  await sleep(15);
  assert.equal(calls, 2);
  assert.equal(maxConcurrent, 1);
  scheduler.start();
  assert.equal(calls, 2);
});

test('stop cancels an idle retry timer', async () => {
  let calls = 0;
  const scheduler = await createSciEvalScheduler({ pollIntervalMs: 30, logger,
    adapterFactory: async () => ({ async drain() { calls++; } }) });
  scheduler.start();
  await sleep(5);
  await scheduler.stop();
  await sleep(40);
  assert.equal(calls, 1);
});

test('stopping a drain leaves remaining events pending', async () => {
  const { createSciEvalAdapter } = await import('../src/scieval/adapter.mjs');
  let stopped = false;
  const acked = [];
  const event = (id) => ({ event_id: id, job_id: 'job', revision: 1, round: 1, from: 'received', to: 'preparing' });
  const adapter = createSciEvalAdapter({ enabled: true, shouldStop: () => stopped,
    outbox: { async pending() { return [event('a'), event('b')]; }, async acknowledge(id) { acked.push(id); } },
    async submit() { stopped = true; return { accepted: true }; },
  });
  assert.deepEqual(await adapter.drain(), { enabled: true, delivered: 1 });
  assert.deepEqual(acked, ['a']);
});
