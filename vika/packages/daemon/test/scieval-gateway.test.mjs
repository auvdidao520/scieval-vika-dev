import { archiveDoneEvent } from '../src/event-archive.mjs';
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
  db.exec(`CREATE TABLE connector_events(seq INTEGER PRIMARY KEY, status TEXT, event_id TEXT, connector_instance TEXT, scope_key TEXT, chat_id TEXT, thread_id TEXT, message_id TEXT, chat_type TEXT, source TEXT, sender_id TEXT, content TEXT, kind TEXT, subject_type TEXT, subject_id TEXT, event_json TEXT);
    INSERT INTO connector_events VALUES(1,'DONE','e1','lark','scope','chat',NULL,'m1','p2p','lark','owner','request',NULL,NULL,NULL,'{}');`);
  const inbox = new MessageInbox({db, runtime: {snapshot: () => ({work: {turnInProgress: true}})}});
  let starts = 0, binding = null;
  const tasks = new TaskManager({db, config: {workspace}, executor: {start() { starts++; return {done: new Promise(() => {}), stop() {}}; }}, inbox, lineFor: row => row.content});
  const server = await startReservationGateway({tasks, socketDir, lockDir: socketDir, grants: [grant], rootUri: 'viking://jobs', readState: () => binding});
  t.after(async () => {await server.close(); tasks.shutdown(); inbox.stop(); db.close(); fs.rmSync(workspace, {recursive: true, force: true});});
  const call = req => new Promise((resolve, reject) => {const socket = net.connect(server.socketPath); let data = ''; socket.on('connect', () => socket.write(JSON.stringify(req)+'\n')); socket.on('data', chunk => data += chunk); socket.on('end', () => resolve(JSON.parse(data))); socket.on('error', reject);});
  archiveDoneEvent(db, 1);
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
  const event = {id:'evaluation.completed:r1', connector:'scieval', instance:'scieval', kind:'evaluation.completed', subject:{type:'scieval_job', id:'job1'}, content:{text:'{}'}};
  db.prepare('INSERT INTO connector_events(seq,status,event_id,connector_instance,source,content,kind,subject_type,subject_id,event_json) VALUES (?,?,?,?,?,?,?,?,?,?)').run(2,'DONE',event.id,'scieval','scieval','report',event.kind,'scieval_job','job1',JSON.stringify(event));
  archiveDoneEvent(db, 2);
  assert.equal((await call({method:'accepted_scieval', report_id:'r1'})).result[0].event.id, event.id);
  assert.equal((await call({method:'route_accepted',job_id:'job1',task_id:reservation.task_id,seq:2})).ok, false);
  assert.equal(db.prepare('SELECT COUNT(*) AS n FROM task_inputs WHERE seq=2').get().n, 0);
  assert.equal((await call({method:'route_accepted',job_id:'job2',task_id:reservation.task_id,seq:2})).ok, false);
  assert.equal((await call({...req, note: 'untrusted'})).ok, false);
});

test('gateway close releases the socket for restart and refuses an occupied socket', async t => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'scieval-life-'));
  const db = new DatabaseSync(':memory:');
  db.exec('CREATE TABLE tasks(id TEXT PRIMARY KEY)');
  const options = {tasks:{db}, socketDir:dir, lockDir:dir, grants:[], rootUri:'viking://jobs'};
  let server = await startReservationGateway(options);
  t.after(async () => { if (server) await server.close(); db.close(); fs.rmSync(dir, {recursive:true, force:true}); });
  assert.equal(fs.statSync(server.socketPath).mode & 0o777, 0o600);
  await assert.rejects(startReservationGateway(options), /socket already exists/);
  await server.close(); server = null;
  assert.equal(fs.existsSync(path.join(dir, 'reservations.sock')), false);
  server = await startReservationGateway(options);
});

 test('rejects unsafe socket directory and dangling path without removing it', async t => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'scieval-perms-'));
  t.after(() => fs.rmSync(dir, {recursive:true, force:true}));
  const options = {tasks:{}, socketDir:dir, lockDir:dir, grants:[], rootUri:'viking://jobs'};
  fs.chmodSync(dir, 0o755);
  await assert.rejects(startReservationGateway(options), /private/);
  fs.chmodSync(dir, 0o700);
  const socketPath = path.join(dir, 'reservations.sock');
  fs.symlinkSync(path.join(dir, 'missing'), socketPath);
  await assert.rejects(startReservationGateway(options), /socket already exists/);
  assert.equal(fs.lstatSync(socketPath).isSymbolicLink(), true);
});
