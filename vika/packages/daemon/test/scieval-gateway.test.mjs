// SPDX-License-Identifier: AGPL-3.0
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import net from 'node:net';
import { spawnSync } from 'node:child_process';
import { DatabaseSync } from 'node:sqlite';
import { TaskManager } from '../src/tasks.mjs';
import { MessageInbox } from '../src/inbox.mjs';
import { createValidators, startReservationGateway } from '../src/scieval-gateway.mjs';

const grant = { job_id: 'job1', seq: 1, event_id: 'e1', connector_instance: 'lark', source: 'lark', scope_key: 'scope', sender_id: 'owner' };
const state = { schema_version: 2, job_id: 'job1', state: 'received', task_id: 't1' };
test('trusted grant and persisted binding fail closed on mismatches or OV failures', () => {
  let current = state;
  const v = createValidators({ grants: [grant], rootUri: 'viking://jobs', readState: () => { if (!current) throw Error('unavailable'); return current; } });
  const row = { ...grant, status: 'DONE' };
  assert.equal(v.authorizeOrigin('job1', row), true);
  for (const key of ['status', 'seq', 'event_id', 'connector_instance', 'source', 'scope_key', 'sender_id']) assert.equal(v.authorizeOrigin('job1', { ...row, [key]: 'wrong' }), false, key);
  assert.equal(v.authorizeOrigin('job2', row), false);
  assert.equal(v.verifyBinding('job1', 1, 't1'), true);
  for (const [key, value] of Object.entries({schema_version: 1, job_id: 'job2', state: 'preparing', task_id: 't2'})) { current = {...state, [key]: value}; assert.equal(v.verifyBinding('job1', 1, 't1'), false, key); }
  current = null; assert.equal(v.verifyBinding('job1', 1, 't1'), false);
});

test('private socket reserves then requires persisted OV binding for activation', async t => {
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), 'scieval-gw-'));
  const socketDir = path.join(workspace, 'private'); fs.mkdirSync(socketDir, { mode: 0o700 });
  const db = new DatabaseSync(':memory:');
  db.exec(`CREATE TABLE connector_events(seq INTEGER PRIMARY KEY, status TEXT, event_id TEXT, connector_instance TEXT, scope_key TEXT, chat_id TEXT, thread_id TEXT, message_id TEXT, chat_type TEXT, source TEXT, sender_id TEXT, content TEXT);
    INSERT INTO connector_events VALUES(1,'DONE','e1','lark','scope','chat',NULL,'m1','p2p','lark','owner','request');`);
  const inbox = new MessageInbox({db, runtime: {snapshot: () => ({work: {turnInProgress: true}})}});
  let starts = 0, binding = null;
  const tasks = new TaskManager({db, config: {workspace}, executor: {start() { starts++; return {done: new Promise(() => {}), stop() {}}; }}, inbox, lineFor: row => row.content});
  const server = await startReservationGateway({tasks, socketDir, lockDir: socketDir, grants: [grant], rootUri: 'viking://jobs', readState: () => binding});
  t.after(async () => {await server.close(); tasks.shutdown(); inbox.stop(); db.close(); fs.rmSync(workspace, {recursive: true, force: true});});
  const call = req => new Promise((resolve, reject) => {const socket = net.connect(server.socketPath); let data = ''; socket.on('connect', () => socket.write(JSON.stringify(req)+'\n')); socket.on('data', chunk => data += chunk); socket.on('end', () => resolve(JSON.parse(data))); socket.on('error', reject);});
  const req = {method:'reserve', job_id:'job1', origin_seq:1};
  const reservation = (await call(req)).result;
  assert.deepEqual((await call(req)).result, reservation);
  const activation = {...req, method:'activate', task_id: reservation.task_id};
  assert.equal((await call(activation)).ok, false); assert.equal(starts, 0);
  binding = {...state, task_id: reservation.task_id};
  const lockPath = path.join(socketDir, 'job1.lock');
  const probe = () => spawnSync('python3', ['-c', `import fcntl,sys
f=open(sys.argv[1], 'a+b')
try: fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
except BlockingIOError: sys.exit(7)`, lockPath]).status;
  const setStatus = tasks.setStatus.bind(tasks);
  tasks.setStatus = (...args) => { assert.equal(probe(), 7, 'competing OvStore writer must be excluded before commit'); return setStatus(...args); };
  assert.equal((await call(activation)).ok, true); assert.equal((await call(activation)).ok, true); assert.equal(starts, 1);
  assert.equal(probe(), 0, 'lock is released after activation');
  assert.equal((await call({...req, note: 'untrusted'})).ok, false);
});
