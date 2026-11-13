// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
// Shadow archive only: legacy readers still require connector_events.event_json.
import { createHash } from 'node:crypto';
import { gzipSync, gunzipSync } from 'node:zlib';

const MAX_BYTES = 8 * 1024 * 1024;
const digest = bytes => createHash('sha256').update(bytes).digest('hex');

export function migrateEventArchive(db) {
  db.exec(`CREATE TABLE IF NOT EXISTS connector_event_archive (
    seq INTEGER PRIMARY KEY REFERENCES connector_events(seq) ON DELETE CASCADE,
    version INTEGER NOT NULL, payload BLOB NOT NULL,
    byte_length INTEGER NOT NULL, sha256 TEXT NOT NULL
  );
  CREATE TRIGGER IF NOT EXISTS archived_event_no_insert BEFORE INSERT ON connector_events
  WHEN EXISTS (SELECT 1 FROM connector_events e JOIN connector_event_archive a ON a.seq=e.seq
    WHERE e.seq=NEW.seq OR (e.connector_instance=NEW.connector_instance AND e.event_id=NEW.event_id))
  BEGIN SELECT RAISE(ABORT, 'archived event is immutable'); END;
  CREATE TRIGGER IF NOT EXISTS event_archive_no_insert BEFORE INSERT ON connector_event_archive
  WHEN EXISTS (SELECT 1 FROM connector_event_archive WHERE seq=NEW.seq)
  BEGIN SELECT RAISE(ABORT, 'event archive is immutable'); END;
  CREATE TRIGGER IF NOT EXISTS archived_event_no_delete BEFORE DELETE ON connector_events
  WHEN EXISTS (SELECT 1 FROM connector_event_archive WHERE seq=OLD.seq)
  BEGIN SELECT RAISE(ABORT, 'archived event is immutable'); END;
  CREATE TRIGGER IF NOT EXISTS archived_event_no_update BEFORE UPDATE ON connector_events
  WHEN EXISTS (SELECT 1 FROM connector_event_archive WHERE seq=OLD.seq)
  BEGIN SELECT RAISE(ABORT, 'archived event is immutable'); END;
  CREATE TRIGGER IF NOT EXISTS archived_event_no_update_replace BEFORE UPDATE ON connector_events
  WHEN EXISTS (SELECT 1 FROM connector_events e JOIN connector_event_archive a ON a.seq=e.seq
    WHERE e.seq=NEW.seq OR (e.connector_instance=NEW.connector_instance AND e.event_id=NEW.event_id))
  BEGIN SELECT RAISE(ABORT, 'archived event is immutable'); END;
  CREATE TRIGGER IF NOT EXISTS event_archive_no_update BEFORE UPDATE ON connector_event_archive
  BEGIN SELECT RAISE(ABORT, 'event archive is immutable'); END;
  CREATE TRIGGER IF NOT EXISTS event_archive_no_delete BEFORE DELETE ON connector_event_archive
  BEGIN SELECT RAISE(ABORT, 'event archive is immutable'); END;`);
}

// Caller owns the transaction. This never changes the original row.
export function archiveDoneEvent(db, seq) {
  const row = db.prepare("SELECT event_json FROM connector_events WHERE seq = ? AND status = 'DONE'").get(seq);
  if (!row) throw new Error('DONE event not found');
  const bytes = Buffer.from(row.event_json, 'utf8');
  if (bytes.length > MAX_BYTES) throw new Error('event exceeds archive limit');
  JSON.parse(row.event_json);
  const prior = db.prepare('SELECT seq FROM connector_event_archive WHERE seq = ?').get(seq);
  if (prior) {
    if (readArchivedEvent(db, seq) !== row.event_json) throw new Error('archive differs from event');
    return false;
  }
  db.prepare('INSERT INTO connector_event_archive VALUES (?, 1, ?, ?, ?)')
    .run(seq, gzipSync(bytes), bytes.length, digest(bytes));
  return true;
}

export function readArchivedEvent(db, seq) {
  const row = db.prepare('SELECT version, payload, byte_length, sha256 FROM connector_event_archive WHERE seq = ?').get(seq);
  if (!row) throw new Error('archive missing');
  if (row.version !== 1) throw new Error('unsupported event archive version');
  if (!Number.isSafeInteger(row.byte_length) || row.byte_length < 0 || row.byte_length > MAX_BYTES) throw new Error('invalid archive size');
  let bytes;
  try { bytes = gunzipSync(row.payload, { maxOutputLength: MAX_BYTES }); }
  catch { throw new Error('invalid archive payload'); }
  if (bytes.length !== row.byte_length || digest(bytes) !== row.sha256) throw new Error('archive integrity mismatch');
  const value = bytes.toString('utf8');
  if (!Buffer.from(value, 'utf8').equals(bytes)) throw new Error('invalid archive UTF-8');
  try { JSON.parse(value); } catch { throw new Error('invalid archive JSON'); }
  return value;
}

// The hot copy remains mandatory until all consumers and downgrade paths are ready.
export function hydrateEventRow(db, row) {
  if (!row) return row;
  const archived = db.prepare('SELECT 1 FROM connector_event_archive WHERE seq = ?').get(row.seq);
  if (!archived) return row;
  const event_json = readArchivedEvent(db, row.seq);
  if (event_json !== row.event_json) throw new Error('archive differs from event');
  return { ...row, event_json };
}

// Caller holds BEGIN IMMEDIATE; each batch commits or rolls back as a unit.
export function archiveDoneBatch(db, { before, limit = 100 } = {}) {
  if (!Number.isSafeInteger(before) || !Number.isSafeInteger(limit) || limit < 1 || limit > 1000) throw new TypeError('invalid archive batch');
  const rows = db.prepare(`SELECT seq FROM connector_events e WHERE status = 'DONE' AND received_at_ms < ?
    AND NOT EXISTS (SELECT 1 FROM connector_event_archive a WHERE a.seq=e.seq) ORDER BY seq LIMIT ?`).all(before, limit);
  for (const row of rows) archiveDoneEvent(db, row.seq);
  return rows.length;
}

// Offline downgrade preparation for shadow archives only. Stop all archivers
// until the old binary starts. Each batch owns an IMMEDIATE transaction.
// Validate the entire selected batch before removing its write protections.
export function rollbackShadowArchiveBatch(db, { limit = 100 } = {}) {
  if (!Number.isSafeInteger(limit) || limit < 1 || limit > 1000) throw new TypeError('invalid rollback batch');
  db.exec('BEGIN IMMEDIATE');
  try {
    const count = rollbackShadowArchiveBatchInTransaction(db, limit);
    db.exec('COMMIT');
    return count;
  } catch (error) {
    try { db.exec('ROLLBACK'); } catch {}
    throw error;
  }
}

function rollbackShadowArchiveBatchInTransaction(db, limit) {
  const rows = db.prepare(`SELECT a.seq, e.event_json FROM connector_event_archive a
    LEFT JOIN connector_events e ON e.seq=a.seq ORDER BY a.seq LIMIT ?`).all(limit);
  for (const row of rows) {
    if (typeof row.event_json !== 'string' || readArchivedEvent(db, row.seq) !== row.event_json) {
      throw new Error('archive differs from event');
    }
  }
  if (!rows.length) return 0;
  // SQLite rolls back both the DDL and deletes on failure or process death.
  db.exec('DROP TRIGGER event_archive_no_delete');
  const remove = db.prepare('DELETE FROM connector_event_archive WHERE seq=?');
  for (const row of rows) remove.run(row.seq);
  db.exec(`CREATE TRIGGER event_archive_no_delete BEFORE DELETE ON connector_event_archive
    BEGIN SELECT RAISE(ABORT, 'event archive is immutable'); END`);
  return rows.length;
}
