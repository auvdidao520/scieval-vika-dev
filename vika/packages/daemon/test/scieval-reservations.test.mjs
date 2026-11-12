// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { TaskManager } from '../src/tasks.mjs';
import { MessageInbox } from '../src/inbox.mjs';
import { SciEvalReservations } from '../src/scieval-reservations.mjs';

test('accepted ledger reservation is atomic, inactive and recoverable', (t) => {
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), 'scieval-reserve-'));
  const db = new DatabaseSync(':memory:');
  db.exec(`CREATE TABLE connector_events(seq INTEGER PRIMARY KEY, status TEXT, event_id TEXT, connector_instance TEXT,
    scope_key TEXT, chat_id TEXT, thread_id TEXT, message_id TEXT, chat_type TEXT, source TEXT, sender_id TEXT, content TEXT);
    INSERT INTO connector_events VALUES (1,'DONE','e1','lark','scope','chat',NULL,'m1','p2p','lark','owner','request');
    INSERT INTO connector_events VALUES (2,'RULE_IGNORED','e2','lark','scope','chat',NULL,'m2','p2p','lark','owner','ignored');`);
  const inbox = new MessageInbox({ db, runtime: { snapshot: () => ({ work: { turnInProgress: true } }) } });
  let starts = 0, bound = false;
  const executor = { start() { starts++; return { done: new Promise(() => {}), stop() {} }; } };
  const tasks = new TaskManager({ db, config: { workspace }, executor, inbox, lineFor: row => row.content });
  t.after(() => { tasks.shutdown(); inbox.stop(); db.close(); fs.rmSync(workspace, { recursive: true, force: true }); });
  const gateway = new SciEvalReservations({ tasks, authorizeOrigin: (job, row) => job === 'job1' && row.sender_id === 'owner', verifyBinding: () => bound });
  assert.throws(() => gateway.reserve('job1', 2), /origin not authorized/);
  assert.throws(() => gateway.reserve('job1', 99), /origin not authorized/);
  assert.throws(() => gateway.reserve('job2', 1), /origin not authorized/);
  db.exec("CREATE TRIGGER fail_input BEFORE INSERT ON task_inputs BEGIN SELECT RAISE(ABORT, 'injected input failure'); END");
  assert.throws(() => gateway.reserve('job1', 1), /injected input failure/);
  assert.equal(db.prepare('SELECT COUNT(*) n FROM tasks').get().n, 0);
  db.exec('DROP TRIGGER fail_input');
  const reservation = gateway.reserve('job1', 1);
  assert.deepEqual(gateway.reserve('job1', 1), reservation);
  assert.equal(tasks.get(reservation.task_id).status, 'reserved');
  tasks.request(reservation.task_id);
  assert.throws(() => tasks.route(reservation.task_id, [{ seq: 1 }], 'owner'), /reserved/);
  tasks.restore();
  assert.equal(starts, 0);
  assert.throws(() => gateway.activate('job1', 1, reservation.task_id), /binding not verified/);
  assert.throws(() => gateway.activate('job1', 1, 'wrong'), /identity conflict/);
  bound = true;
  gateway.activate('job1', 1, reservation.task_id);
  gateway.activate('job1', 1, reservation.task_id);
  assert.equal(starts, 1);
});
