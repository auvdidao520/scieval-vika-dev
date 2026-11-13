// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { test } from 'node:test';
import { DatabaseSync } from 'node:sqlite';
import { migrateEventArchive, archiveDoneEvent, readArchivedEvent } from '../src/event-archive.mjs';

test('shadow archive survives restart and legacy reader, with transactional rollback', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'event-archive-'));
  const file = path.join(dir, 'state.db');
  let db = new DatabaseSync(file);
  try {
    db.exec("PRAGMA foreign_keys=ON; CREATE TABLE connector_events(seq INTEGER PRIMARY KEY, status TEXT, event_json TEXT NOT NULL)");
    const json = JSON.stringify({ content: '重试', nested: { n: 1 } });
    db.prepare('INSERT INTO connector_events VALUES (1, ?, ?), (2, ?, ?)').run('DONE', json, 'PENDING', '{}');
    migrateEventArchive(db);
    assert.throws(() => archiveDoneEvent(db, 2), /not found/);
    db.exec('BEGIN IMMEDIATE');
    assert.equal(archiveDoneEvent(db, 1), true);
    db.exec('ROLLBACK');
    assert.throws(() => readArchivedEvent(db, 1), /missing/);
    db.exec('BEGIN IMMEDIATE'); archiveDoneEvent(db, 1); db.exec('COMMIT');
    db.close(); db = new DatabaseSync(file);
    assert.equal(readArchivedEvent(db, 1), json);
    assert.equal(db.prepare('SELECT event_json FROM connector_events WHERE seq=1').get().event_json, json);
    assert.equal(archiveDoneEvent(db, 1), false);
    db.prepare('UPDATE connector_event_archive SET version=2 WHERE seq=1').run();
    assert.throws(() => readArchivedEvent(db, 1), /unsupported/);
    db.prepare("UPDATE connector_event_archive SET version=1, sha256='bad' WHERE seq=1").run();
    assert.throws(() => readArchivedEvent(db, 1), /integrity/);
    db.prepare("UPDATE connector_event_archive SET payload=x'00' WHERE seq=1").run();
    assert.throws(() => readArchivedEvent(db, 1), /payload/);
  } finally { db.close(); fs.rmSync(dir, { recursive: true, force: true }); }
});
