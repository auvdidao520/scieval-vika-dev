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
  )`);
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
