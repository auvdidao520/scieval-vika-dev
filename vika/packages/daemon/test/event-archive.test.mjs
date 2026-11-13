// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { test } from 'node:test';
import { DatabaseSync, backup } from 'node:sqlite';
import { spawnSync } from 'node:child_process';
import { migrateEventArchive, archiveDoneEvent, readArchivedEvent, archiveDoneBatch, hydrateEventRow, rollbackShadowArchiveBatch } from '../src/event-archive.mjs';

test('shadow archive survives restart and legacy reader, with transactional rollback', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'event-archive-'));
  const file = path.join(dir, 'state.db');
  let db = new DatabaseSync(file);
  try {
    db.exec("PRAGMA foreign_keys=ON; CREATE TABLE connector_events(seq INTEGER PRIMARY KEY, status TEXT, event_json TEXT NOT NULL, connector_instance TEXT, event_id TEXT, UNIQUE(connector_instance,event_id))");
    const json = JSON.stringify({ content: '重试', nested: { n: 1 } });
    db.prepare("INSERT INTO connector_events VALUES (1, ?, ?, 'c', '1'), (2, ?, ?, 'c', '2')").run('DONE', json, 'PENDING', '{}');
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
    assert.throws(() => db.prepare('DELETE FROM connector_events WHERE seq=1').run(), /immutable/);
    assert.throws(() => db.prepare("UPDATE connector_events SET event_json='{}' WHERE seq=1").run(), /immutable/);
    assert.throws(() => db.prepare('DELETE FROM connector_event_archive WHERE seq=1').run(), /immutable/);
    assert.throws(() => db.exec(`INSERT OR REPLACE INTO connector_events VALUES (1,'DONE','{}','other','other')`), /immutable/);
    assert.throws(() => db.exec(`INSERT OR REPLACE INTO connector_events VALUES (99,'DONE','{}','c','1')`), /immutable/);
    assert.throws(() => db.exec(`INSERT OR REPLACE INTO connector_event_archive SELECT * FROM connector_event_archive WHERE seq=1`), /immutable/);
    assert.equal(readArchivedEvent(db, 1), json);
    db.exec('DROP TRIGGER event_archive_no_update'); // simulate corruption outside normal SQL access
    db.prepare('UPDATE connector_event_archive SET version=2 WHERE seq=1').run();
    assert.throws(() => readArchivedEvent(db, 1), /unsupported/);
    db.prepare("UPDATE connector_event_archive SET version=1, sha256='bad' WHERE seq=1").run();
    assert.throws(() => readArchivedEvent(db, 1), /integrity/);
    db.prepare("UPDATE connector_event_archive SET payload=x'00' WHERE seq=1").run();
    assert.throws(() => readArchivedEvent(db, 1), /payload/);
  } finally { db.close(); fs.rmSync(dir, { recursive: true, force: true }); }
});

test('offline shadow rollback validates each batch and restores legacy writes', () => {
  const db = new DatabaseSync(':memory:');
  try {
    db.exec(`CREATE TABLE connector_events(seq INTEGER PRIMARY KEY, status TEXT, event_json TEXT NOT NULL, connector_instance TEXT, event_id TEXT, UNIQUE(connector_instance,event_id));
      INSERT INTO connector_events VALUES(1,'DONE','{"x":1}','c','1'),(2,'DONE','{"x":2}','c','2')`);
    migrateEventArchive(db);
    archiveDoneEvent(db, 1); archiveDoneEvent(db, 2);
    assert.equal(rollbackShadowArchiveBatch(db, {limit: 1}), 1);
    db.exec("UPDATE connector_events SET event_json='{}' WHERE seq=1");
    assert.throws(() => db.exec('DELETE FROM connector_event_archive WHERE seq=2'), /immutable/);
    db.exec('DROP TRIGGER event_archive_no_update');
    db.exec("UPDATE connector_event_archive SET sha256='bad' WHERE seq=2");
    assert.throws(() => rollbackShadowArchiveBatch(db), /integrity/);
    assert.equal(db.prepare('SELECT count(*) n FROM connector_event_archive').get().n, 1);
    assert.throws(() => db.exec('DELETE FROM connector_event_archive WHERE seq=2'), /immutable/);
  } finally { db.close(); }
});

test('SIGKILL during downgrade transaction restores archive and protection', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'archive-rollback-'));
  const file = path.join(dir, 'state.db');
  let db = new DatabaseSync(file);
  try {
    db.exec(`PRAGMA journal_mode=WAL; CREATE TABLE connector_events(seq INTEGER PRIMARY KEY, status TEXT, event_json TEXT NOT NULL, connector_instance TEXT, event_id TEXT, UNIQUE(connector_instance,event_id)); INSERT INTO connector_events VALUES(1,'DONE','{}','c','1')`);
    migrateEventArchive(db); archiveDoneEvent(db, 1); db.close();
    const child = spawnSync(process.execPath, ['--input-type=module', '-e', `import {DatabaseSync} from 'node:sqlite'; const db=new DatabaseSync(process.argv[1]); db.exec('BEGIN IMMEDIATE; DROP TRIGGER event_archive_no_delete; DELETE FROM connector_event_archive'); process.kill(process.pid,'SIGKILL')`, file]);
    assert.equal(child.signal, 'SIGKILL');
    db = new DatabaseSync(file);
    assert.equal(readArchivedEvent(db, 1), '{}');
    assert.throws(() => db.exec('DELETE FROM connector_event_archive'), /immutable/);
    assert.equal(rollbackShadowArchiveBatch(db), 1);
    db.close(); db = new DatabaseSync(file);
    assert.equal(db.prepare('SELECT event_json FROM connector_events').get().event_json, '{}');
    db.exec("UPDATE connector_events SET event_json='legacy' WHERE seq=1");
  } finally { db.close(); fs.rmSync(dir, {recursive:true, force:true}); }
});


test('bounded batches survive SIGKILL and WAL backup retains committed archive', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'event-batch-'));
  const file = path.join(dir, 'db.sqlite');
  const copy = path.join(dir, 'backup.sqlite');
  let db = new DatabaseSync(file);
  try {
    db.exec("PRAGMA journal_mode=WAL; CREATE TABLE connector_events(seq INTEGER PRIMARY KEY, status TEXT, event_json TEXT, received_at_ms INTEGER, connector_instance TEXT, event_id TEXT, UNIQUE(connector_instance,event_id)); INSERT INTO connector_events VALUES(1,'DONE','{\"x\":1}',1,'c','1'),(2,'DONE','{\"x\":2}',2,'c','2'),(3,'PENDING','{}',1,'c','3')");
    migrateEventArchive(db);
    const moduleUrl = new URL('../src/event-archive.mjs', import.meta.url).href;
    const child = spawnSync(process.execPath, ['--input-type=module', '-e', `import {DatabaseSync} from 'node:sqlite'; import {archiveDoneBatch} from ${JSON.stringify(moduleUrl)}; const db=new DatabaseSync(process.argv[1]); db.exec('BEGIN IMMEDIATE'); archiveDoneBatch(db,{before:10,limit:1}); process.kill(process.pid,'SIGKILL')`, file]);
    assert.equal(child.signal, 'SIGKILL');
    assert.equal(db.prepare('SELECT count(*) n FROM connector_event_archive').get().n, 0);
    db.exec('BEGIN IMMEDIATE'); assert.equal(archiveDoneBatch(db, {before:10,limit:1}), 1); db.exec('COMMIT');
    assert.equal(hydrateEventRow(db, db.prepare('SELECT * FROM connector_events WHERE seq=1').get()).event_json, '{"x":1}');
    await backup(db, copy);
    const restored = new DatabaseSync(copy);
    try { assert.equal(readArchivedEvent(restored, 1), '{"x":1}'); assert.equal(restored.prepare('SELECT count(*) n FROM connector_event_archive').get().n, 1); } finally { restored.close(); }
    db.exec('BEGIN IMMEDIATE'); assert.equal(archiveDoneBatch(db, {before:10,limit:10}), 1); db.exec('COMMIT');
    db.exec('BEGIN IMMEDIATE'); assert.equal(archiveDoneBatch(db, {before:10,limit:10}), 0); db.exec('COMMIT');
  } finally { db.close(); fs.rmSync(dir,{recursive:true,force:true}); }
});
