// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
// Explicitly started, local-only experimental reservation gateway.
import fs from 'node:fs';
import net from 'node:net';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
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
    readTrustedState(job) { return reader(job); },
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
// The helper locks the inherited open file description. The daemon keeps its
// descriptor open until activation commits, including if the helper exits.
export function withJobLock(lockDir, job, fn) {
  if (!idPattern.test(job)) throw new Error('invalid job');
  const dir = fs.lstatSync(lockDir);
  if (!dir.isDirectory() || dir.uid !== process.getuid() || (dir.mode & 0o077)) throw new Error('lock directory must be private');
  const fd = fs.openSync(path.join(lockDir, `${job}.lock`), fs.constants.O_CREAT | fs.constants.O_RDWR | fs.constants.O_NOFOLLOW, 0o600);
  try {
    const stat = fs.fstatSync(fd);
    if (!stat.isFile() || stat.uid !== process.getuid() || (stat.mode & 0o077) || stat.nlink !== 1) throw new Error('invalid lock file');
    const result = spawnSync('python3', ['-c', 'import fcntl; fcntl.flock(3, fcntl.LOCK_EX)'], { stdio: ['ignore', 'ignore', 'pipe', fd], timeout: 10000 });
    if (result.error || result.status !== 0) throw new Error('lock unavailable');
    return fn();
  } finally { fs.closeSync(fd); }
}

export async function startReservationGateway({ tasks, socketDir, lockDir, grants, rootUri, ovBin, readState }) {
  if (!lockDir) throw new Error('shared OV lock directory required');
  const stat = fs.lstatSync(socketDir);
  if (!stat.isDirectory() || stat.uid !== process.getuid() || (stat.mode & 0o077)) throw new Error('socket directory must be owned by daemon user and private');
  const socketPath = path.join(socketDir, 'reservations.sock');
  try { fs.lstatSync(socketPath); throw new Error('socket already exists'); }
  catch (error) { if (error.code !== 'ENOENT') throw error; }
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
          withJobLock(lockDir, request.job_id, () => reservations.activate(request.job_id, request.origin_seq, request.task_id));
          result = { job_id: request.job_id, origin_seq: request.origin_seq, task_id: request.task_id };
        } else if (request.method === 'accepted_scieval' && Object.keys(request).sort().join(',') === 'method,report_id') {
          if (typeof request.report_id !== 'string' || !request.report_id || request.report_id.length > 256) throw new Error('invalid report');
          result = tasks.db.prepare("SELECT seq, status, connector_instance, event_json FROM connector_events WHERE event_id = ? AND status = 'DONE' AND source = 'scieval' AND kind = 'evaluation.completed' ORDER BY seq LIMIT 3")
            .all('evaluation.completed:' + request.report_id).map(({event_json, ...row}) => ({...row, event: JSON.parse(event_json)}));
        } else if (request.method === 'route_accepted' && Object.keys(request).sort().join(',') === 'job_id,method,seq,task_id') {
          if (!idPattern.test(request.job_id) || !Number.isSafeInteger(request.seq) || typeof request.task_id !== 'string') throw new Error('invalid route');
          withJobLock(lockDir, request.job_id, () => {
            const binding = tasks.db.prepare('SELECT * FROM scieval_task_reservations WHERE job_id = ? AND task_id = ?').get(request.job_id, request.task_id);
            const row = tasks.db.prepare("SELECT * FROM connector_events WHERE seq = ? AND status = 'DONE' AND source = 'scieval' AND kind = 'evaluation.completed' AND subject_type = 'scieval_job' AND subject_id = ?").get(request.seq, request.job_id);
            if (!binding || !row) throw new Error('route rejected');
            const duplicates = tasks.db.prepare("SELECT count(*) AS n FROM connector_events WHERE event_id = ? AND status = 'DONE' AND source = 'scieval' AND kind = 'evaluation.completed'").get(row.event_id);
            if (duplicates.n !== 1) throw new Error('ambiguous event');
            const state = validators.readTrustedState(request.job_id);
            const check = spawnSync('python3', ['-I', fileURLToPath(new URL('./scieval-verify-report.py', import.meta.url))], {
              input: JSON.stringify({ state, row, job: request.job_id, task: request.task_id }), timeout: 10000, maxBuffer: 1024 * 1024,
            });
            if (check.error || check.status !== 0) throw new Error('report rejected');
            tasks.route(request.task_id, [row], 'owner');
          });
          result = { job_id: request.job_id, task_id: request.task_id, seq: request.seq };
        } else throw new Error('unknown request');
        const response = JSON.stringify({ ok: true, result }) + '\n';
        if (Buffer.byteLength(response) > 1024 * 1024) throw new Error('response too large');
        socket.end(response);
      } catch { socket.end(JSON.stringify({ ok: false, error: 'request rejected' }) + '\n'); }
    });
  });
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(socketPath, resolve); });
  try {
    fs.chmodSync(socketPath, 0o600);
    const socketStat = fs.lstatSync(socketPath);
    if (!socketStat.isSocket() || socketStat.uid !== process.getuid() || (socketStat.mode & 0o777) !== 0o600) throw new Error('invalid socket permissions');
  } catch (error) {
    await new Promise(resolve => server.close(resolve));
    throw error;
  }
  return { socketPath, async close() { for (const client of clients) client.destroy(); await new Promise(resolve => server.close(resolve)); } };
}
