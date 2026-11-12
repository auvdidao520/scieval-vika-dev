// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
// Privileged in-process building block. No network or model-facing endpoint.
import { randomUUID } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

export class SciEvalReservations {
  constructor({ tasks, authorizeOrigin, verifyBinding }) {
    if (!tasks || typeof authorizeOrigin !== 'function' || typeof verifyBinding !== 'function') {
      throw new TypeError('tasks and trusted authorization and binding verifiers required');
    }
    Object.assign(this, { tasks, authorizeOrigin, verifyBinding });
    tasks.db.exec(`CREATE TABLE IF NOT EXISTS scieval_task_reservations (
      job_id TEXT PRIMARY KEY, origin_seq INTEGER NOT NULL UNIQUE,
      task_id TEXT NOT NULL UNIQUE REFERENCES tasks(id)
    )`);
  }

  reserve(jobId, originSeq, { title = 'SciEval', note = '', requester = null, runtime = null } = {}) {
    if (typeof jobId !== 'string' || !/^[A-Za-z0-9_-]{1,64}$/.test(jobId) || !Number.isSafeInteger(originSeq) || originSeq < 1) {
      throw new TypeError('invalid job or origin');
    }
    const { tasks } = this;
    return tasks.transaction(() => {
      const row = tasks.db.prepare("SELECT * FROM connector_events WHERE seq = ? AND status = 'DONE'").get(originSeq);
      if (!row || this.authorizeOrigin(jobId, row) !== true) throw new Error('origin not authorized');
      const existing = tasks.db.prepare('SELECT * FROM scieval_task_reservations WHERE job_id = ? OR origin_seq = ?').all(jobId, originSeq);
      if (existing.length) {
        if (existing.length !== 1 || existing[0].job_id !== jobId || existing[0].origin_seq !== originSeq) throw new Error('reservation identity conflict');
        return { job_id: jobId, origin_seq: originSeq, task_id: existing[0].task_id };
      }
      const id = tasks.nextId();
      const time = tasks.now();
      const workdir = path.join(tasks.config.workspace, 'tasks', id);
      fs.mkdirSync(workdir, { recursive: true });
      tasks.db.prepare(`INSERT INTO tasks(id, title, status, origin_seq, origin_event, origin_instance, origin_scope, origin_chat,
        origin_thread, origin_message, origin_chat_type, origin_source, requester_id, requester_name, note, runtime, session_id, workdir,
        created_at_ms, updated_at_ms) VALUES (?, ?, 'reserved', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`).run(
        id, String(title).trim().slice(0, 120), row.seq, row.event_id, row.connector_instance, row.scope_key, row.chat_id ?? null,
        row.thread_id ?? null, row.message_id ?? null, row.chat_type ?? null, row.source, row.sender_id ?? null,
        requester, String(note).trim().slice(0, 400), runtime, randomUUID(), workdir, time, time);
      tasks.addInput(id, row, 'owner');
      tasks.db.prepare('INSERT INTO scieval_task_reservations VALUES (?, ?, ?)').run(jobId, originSeq, id);
      return { job_id: jobId, origin_seq: originSeq, task_id: id };
    });
  }

  activate(jobId, originSeq, taskId) {
    const { tasks } = this;
    tasks.transaction(() => {
      const reservation = tasks.db.prepare('SELECT * FROM scieval_task_reservations WHERE job_id = ?').get(jobId);
      if (!reservation || reservation.origin_seq !== originSeq || reservation.task_id !== taskId) throw new Error('reservation identity conflict');
      const row = tasks.db.prepare("SELECT * FROM connector_events WHERE seq = ? AND status = 'DONE'").get(originSeq);
      if (!row || this.authorizeOrigin(jobId, row) !== true || this.verifyBinding(jobId, originSeq, taskId) !== true) throw new Error('binding not verified');
      const task = tasks.get(taskId);
      if (!task || task.origin_seq !== originSeq) throw new Error('reserved task mismatch');
      if (task.status === 'reserved') tasks.setStatus(taskId, 'queued');
    });
    // Retry after a crash between commit and scheduling; never restart ended work.
    if (tasks.get(taskId)?.status === 'queued') tasks.request(taskId);
    return tasks.get(taskId);
  }
}
