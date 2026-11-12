// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
// Explicitly started, local-only experimental reservation gateway.
import fs from 'node:fs';
import net from 'node:net';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { SciEvalReservations } from './scieval-reservations.mjs';

const idPattern = /^[A-Za-z0-9_-]{1,64}$/;
const fields = ['seq', 'event_id', 'connector_instance', 'source', 'scope_key', 'sender_id'];

// Grants are provisioned by a trusted operator, never derived from a request.
export function createValidators({ grants, rootUri, ovBin = 'ov', readState }) {
  if (!Array.isArray(grants) || !rootUri?.startsWith('viking://') || rootUri.split('/').includes('..')) throw new TypeError('invalid trusted configuration');
  const byJob = new Map();
  for (const grant of grants) {
    if (!idPattern.test(grant.job_id) || !Number.isSafeInteger(grant.seq) || grant.seq < 1 ||
        fields.slice(1).some(k => typeof grant[k] !== 'string' || !grant[k]) || byJob.has(grant.job_id)) throw new TypeError('invalid grant');
    byJob.set(grant.job_id, Object.freeze({ ...grant }));
  }
  const reader = readState ?? ((job) => {
    const result = spawnSync(ovBin, ['read', `${rootUri.replace(/\/$/, '')}/${job}/state.json`], { encoding: 'utf8', timeout: 10000, maxBuffer: 1024 * 1024 });
    if (result.error || result.status !== 0) throw new Error('OV read failed');
    return JSON.parse(result.stdout);
  });
  return {
    authorizeOrigin(job, row) {
      const grant = byJob.get(job);
      return !!grant && row?.status === 'DONE' && fields.every(k => row[k] === grant[k]);
    },
    verifyBinding(job, seq, task) {
      if (!idPattern.test(job) || !Number.isSafeInteger(seq) || seq < 1 || typeof task !== 'string' || !task) return false;
      try {
        const state = reader(job);
        return state?.schema_version === 2 && state.job_id === job && state.state === 'received' && state.task_id === task;
      } catch { return false; }
    },
  };
}

// socketDir must be private to the daemon user. No model-facing access.
export async function startReservationGateway({ tasks, socketDir, grants, rootUri, ovBin, readState }) {
  const stat = fs.lstatSync(socketDir);
  if (!stat.isDirectory() || stat.uid !== process.getuid() || (stat.mode & 0o077)) throw new Error('socket directory must be owned by daemon user and private');
  const socketPath = path.join(socketDir, 'reservations.sock');
  if (fs.existsSync(socketPath)) throw new Error('socket already exists');
  const validators = createValidators({ grants, rootUri, ovBin, readState });
  const reservations = new SciEvalReservations({ tasks, ...validators });
  const clients = new Set();
  const server = net.createServer(socket => {
    clients.add(socket); socket.on('close', () => clients.delete(socket));
    socket.setTimeout(10000, () => socket.destroy());
    let data = Buffer.alloc(0), handled = false;
    socket.on('error', () => {});
    socket.on('data', chunk => {
      if (handled) return;
      data = Buffer.concat([data, chunk]);
      if (data.length > 8192) { socket.destroy(); return; }
      const end = data.indexOf(10);
      if (end < 0) return;
      handled = true;
      try {
        if (end !== data.length - 1) throw new Error('one request only');
        const request = JSON.parse(data.subarray(0, end).toString('utf8'));
        if (!request || typeof request !== 'object' || Array.isArray(request)) throw new Error('invalid request');
        let result;
        if (request.method === 'reserve' && Object.keys(request).sort().join(',') === 'job_id,method,origin_seq') result = reservations.reserve(request.job_id, request.origin_seq);
        else if (request.method === 'activate' && Object.keys(request).sort().join(',') === 'job_id,method,origin_seq,task_id') {
          reservations.activate(request.job_id, request.origin_seq, request.task_id);
          result = { job_id: request.job_id, origin_seq: request.origin_seq, task_id: request.task_id };
        } else throw new Error('unknown request');
        socket.end(JSON.stringify({ ok: true, result }) + '\n');
      } catch { socket.end(JSON.stringify({ ok: false, error: 'request rejected' }) + '\n'); }
    });
  });
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(socketPath, resolve); });
  fs.chmodSync(socketPath, 0o600);
  return { socketPath, async close() { for (const client of clients) client.destroy(); await new Promise(resolve => server.close(resolve)); } };
}
