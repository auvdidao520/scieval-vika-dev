import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { createLocalOutbox } from '../src/scieval/local-outbox.mjs';
import { createLocalSciEvalAdapter, createSciEvalAdapter, harnessSubmitter } from '../src/scieval/adapter.mjs';
import { Store } from '../src/store.mjs';
import { Harness } from '../src/harness.mjs';

const frameworkDir = new URL('../../../../scieval-vika-p0/', import.meta.url).pathname;
const logger = { info() {}, warn() {}, error() {}, debug() {} };
function setup(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'scieval bridge ; '));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const databasePath = path.join(dir, 'p1 $(no-shell).db');
  execFileSync('python3', ['-c', 'import sys; from framework import Store; s=Store(sys.argv[1]); s.create("job-1"); s.transition("job-1",0,"preparing"); s.close()', databasePath], { cwd: frameworkDir });
  return { dir, databasePath };
}
function runtime(t, dir) {
  const store = new Store(path.join(dir, 'runtime.db'), logger);
  const harness = new Harness({ store, logger, recordActivity() {}, config: { maxEventBytes: 100000, maxEventAgeMs: 60000, ignoredSenderIds: new Set() },
    runtime: { snapshot: () => ({ session: { connected: false }, work: { turnInProgress: false } }), submit() {} } });
  let closed = false;
  const close = () => { if (!closed) { closed = true; harness.stop(); store.close(); } };
  t.after(close);
  return { store, harness, close };
}

test('real subprocess bridge replays after acknowledgement failure across restart against Runtime ledger', async (t) => {
  const { dir, databasePath } = setup(t);
  let { store, harness, close } = runtime(t, dir);
  const first = createLocalOutbox({ databasePath });
  const [event] = await first.pending();
  const adapter = createSciEvalAdapter({ enabled: true, outbox: { pending: first.pending, acknowledge: async () => { throw Error('simulated crash'); } }, submit: harnessSubmitter(harness, store) });
  await assert.rejects(adapter.drain(), /simulated crash/);
  assert.equal((await first.pending()).length, 1);
  assert.ok(store.getInboundByEvent.get('scieval', event.event_id));
  close();
  // Reopen the persistent Runtime ledger and start a fresh bridge process per call.
  ({ store, harness, close } = runtime(t, dir));
  const restarted = await createLocalSciEvalAdapter({ enabled: true, databasePath, harness, store });
  assert.deepEqual(await restarted.drain(), { enabled: true, delivered: 1 });
  assert.deepEqual(await createLocalOutbox({ databasePath }).pending(), []);
  assert.equal(store.db.prepare('SELECT COUNT(*) AS n FROM connector_events').get().n, 1);
  await createLocalOutbox({ databasePath }).acknowledge(event.event_id); // idempotent
});

test('invalid database, unknown id and Runtime rejection leave source untouched', async (t) => {
  const { dir, databasePath } = setup(t);
  const bridge = createLocalOutbox({ databasePath });
  await assert.rejects(bridge.acknowledge('missing'));
  await assert.rejects(createLocalOutbox({ databasePath: path.join(dir, 'missing.db') }).pending());
  assert.equal(fs.existsSync(path.join(dir, 'missing.db')), false);
  const { store, harness } = runtime(t, dir);
  harness.isStopping = () => true;
  const adapter = await createLocalSciEvalAdapter({ enabled: true, databasePath, harness, store });
  await assert.rejects(adapter.drain(), /not accepted/);
  assert.equal((await bridge.pending()).length, 1);
  assert.deepEqual(await (await createLocalSciEvalAdapter()).drain(), { enabled: false, delivered: 0 });
});
