// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
import assert from 'node:assert/strict';
import { execFileSync, fork } from 'node:child_process';
import { once } from 'node:events';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';
import { createLocalOutbox } from '../src/scieval/local-outbox.mjs';

const framework = fileURLToPath(new URL('../../../../scieval-vika-p0/', import.meta.url));
const fixture = fileURLToPath(new URL('./scieval-process-fixture.mjs', import.meta.url));
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

test('SciEval recovers a ledgered but unacknowledged event after process death', { timeout: 90000 }, async (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'scieval-process-'));
  const source = path.join(dir, 'source.db');
  const runtime = path.join(dir, 'runtime.db');
  let child;
  const mockDirs = [];
  t.after(async () => {
    if (child && child.exitCode === null && child.signalCode === null) {
      try { process.kill(-child.pid, 'SIGKILL'); } catch (error) { if (error.code !== 'ESRCH') throw error; }
      await once(child, 'exit').catch(() => {});
    }
    for (const mockDir of mockDirs) fs.rmSync(mockDir, { recursive: true, force: true });
    fs.rmSync(dir, { recursive: true, force: true });
  });
  const python = (code, ...args) => execFileSync('python3', ['-c', code, ...args], { cwd: framework, encoding: 'utf8' }).trim();
  const eventId = python('import sys; from framework import Store; s=Store(sys.argv[1]); s.create("job-1"); s.transition("job-1",0,"preparing"); print(s.db.execute("SELECT event_id FROM outbox").fetchone()[0]); s.close()', source);
  python('import sqlite3,sys; d=sqlite3.connect(sys.argv[1]); d.execute("CREATE TRIGGER fail_ack BEFORE UPDATE OF delivered ON outbox BEGIN SELECT RAISE(FAIL, \'injected failure\'); END"); d.close()', source);
  const outbox = createLocalOutbox({ databasePath: source });
  const start = () => fork(fixture, [source, runtime, eventId], { detached: true, execArgv: [], stdio: ['ignore', 'inherit', 'inherit', 'ipc'] });
  const waitForLedger = async (proc) => {
    const [message] = await Promise.race([
      once(proc, 'message'),
      once(proc, 'exit').then(([code, signal]) => { throw new Error(`daemon exited before ledger: ${code}, ${signal}`); }),
      new Promise((_, reject) => { const timer = setTimeout(() => reject(new Error('daemon ledger timeout')), 30000); timer.unref(); }),
    ]);
    assert.equal(message.ledgered, eventId);
    mockDirs.push(message.dir);
    return message.pid;
  };
  child = start();
  const firstPid = await waitForLedger(child);
  assert.deepEqual((await outbox.pending()).map((event) => event.event_id), [eventId]);
  process.kill(-child.pid, 'SIGKILL');
  const [code, signal] = await once(child, 'exit');
  assert.equal(code, null);
  assert.equal(signal, 'SIGKILL');
  assert.deepEqual((await outbox.pending()).map((event) => event.event_id), [eventId]);
  python('import sqlite3,sys; d=sqlite3.connect(sys.argv[1]); d.execute("DROP TRIGGER fail_ack"); d.close()', source);
  child = start();
  const secondPid = await waitForLedger(child);
  assert.notEqual(firstPid, secondPid);
  const deadline = Date.now() + 8000;
  while ((await outbox.pending()).length && Date.now() < deadline) await delay(50);
  assert.deepEqual(await outbox.pending(), []);
  child.send('stop');
  const [exitCode] = await once(child, 'exit');
  assert.equal(exitCode, 0);
  const count = python('import sqlite3,sys; d=sqlite3.connect(sys.argv[1]); print(d.execute("SELECT count(*) FROM connector_events WHERE connector_instance=? AND event_id=?", ("scieval",sys.argv[2])).fetchone()[0]); d.close()', runtime, eventId);
  assert.equal(count, '1');
});
