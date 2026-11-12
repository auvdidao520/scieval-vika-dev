// SPDX-License-Identifier: AGPL-3.0
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const fixture = fileURLToPath(new URL('./scieval-daemon-fixture.mjs', import.meta.url));
function start(dir) {
  const child = spawn(process.execPath, [fixture, dir], { detached: true, stdio: ['ignore', 'pipe', 'pipe'] });
  let stdout = '', stderr = '';
  child.stdout.on('data', c => { stdout += c; });
  child.stderr.on('data', c => { stderr += c; });
  const exit = new Promise(resolve => child.once('exit', (code, signal) => resolve({ code, signal })));
  return { child, exit, output: () => ({ stdout, stderr }) };
}
async function ready(proc) {
  const deadline = Date.now() + 20000;
  while (Date.now() < deadline) {
    if (proc.output().stdout.includes('READY')) return;
    if (proc.child.exitCode !== null) throw Error(`daemon exited: ${proc.output().stderr}`);
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  throw Error(`daemon timeout: ${proc.output().stderr}`);
}
function killGroup(proc, signal) { try { process.kill(-proc.child.pid, signal); } catch (e) { if (e.code !== 'ESRCH') throw e; } }

test('daemon fails closed on stale gateway after SIGKILL and restarts after operator cleanup', { timeout: 60000 }, async t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'scieval-daemon-'));
  const socketDir = path.join(root, 'socket'); fs.mkdirSync(socketDir, { mode: 0o700 });
  fs.writeFileSync(path.join(root, 'gateway.json'), JSON.stringify({socketDir, lockDir: socketDir, rootUri: 'viking://jobs', grants: []}), {mode: 0o600});
  const processes = [];
  t.after(async () => { for (const p of processes) { killGroup(p, 'SIGKILL'); await p.exit; } fs.rmSync(root, { recursive: true, force: true }); });
  const first = start(root); processes.push(first); await ready(first);
  const socket = path.join(socketDir, 'reservations.sock'); assert.equal(fs.lstatSync(socket).isSocket(), true);
  killGroup(first, 'SIGKILL'); await first.exit;
  const failed = start(root); processes.push(failed);
  const result = await failed.exit; assert.notEqual(result.code, 0); assert.match(failed.output().stderr, /socket already exists/);
  assert.equal(fs.lstatSync(socket).isSocket(), true);
  fs.unlinkSync(socket);
  const recovered = start(root); processes.push(recovered); await ready(recovered);
  recovered.child.kill('SIGTERM'); assert.equal((await recovered.exit).code, 0);
  assert.equal(fs.existsSync(socket), false);
});

test('reservation, activation and accepted report survive daemon crashes at commit boundaries', { timeout: 120000 }, async t => {
  const { DatabaseSync } = await import('node:sqlite');
  const { createHash } = await import('node:crypto');
  const net = await import('node:net');
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'scieval-journal-'));
  const socketDir = path.join(root, 'socket'); fs.mkdirSync(socketDir, {mode: 0o700});
  const socketPath = path.join(socketDir, 'reservations.sock');
  const stateFile = path.join(root, 'ov-state.json');
  const ov = path.join(root, 'ov');
  fs.writeFileSync(ov, `#!/bin/sh\ncat '${stateFile}'\n`, {mode: 0o700});
  const grant = {job_id:'job1', seq:1, event_id:'e1', connector_instance:'lark', source:'lark', scope_key:'scope', sender_id:'owner'};
  fs.writeFileSync(path.join(root, 'gateway.json'), JSON.stringify({socketDir, lockDir:socketDir, rootUri:'viking://jobs', ovBin:ov, grants:[grant]}));
  const processes = [];
  t.after(async () => { for (const p of processes) { killGroup(p, 'SIGKILL'); await p.exit; } fs.rmSync(root, {recursive:true, force:true}); });
  const boot = async () => { const p = start(root); processes.push(p); await ready(p); return p; };
  const crash = async p => { killGroup(p, 'SIGKILL'); await p.exit; fs.unlinkSync(socketPath); };
  const call = req => new Promise((resolve, reject) => {
    const s = net.connect(socketPath); let data = '';
    s.on('connect', () => s.write(JSON.stringify(req)+'\n'));
    s.on('data', c => data += c); s.on('end', () => resolve(data ? JSON.parse(data) : null)); s.on('error', reject);
  });
  const db = () => new DatabaseSync(path.join(root, 'data', 'state.sqlite3'));
  const insertEvent = (d, e) => d.prepare(`INSERT INTO connector_events(seq,connector_instance,event_id,kind,subject_type,subject_id,scope_key,event_json,source,sender_id,content,mentions_json,create_time_ms,received_at_ms,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).run(...e);
  let p = await boot();
  let d = db();
  insertEvent(d, [1,'lark','e1','message','chat','chat','scope',JSON.stringify({connector:'lark',instance:'lark',id:'e1',kind:'message',occurred_at:1,subject:{type:'chat',id:'chat'},content:{text:'request'}}),'lark','owner','request','[]',Date.now(),Date.now(),'DONE']); d.close();
  const reserve = {method:'reserve',job_id:'job1',origin_seq:1};
  const response = await call(reserve); assert.equal(response.ok, true, JSON.stringify(p.output())); const reserved = response.result; assert.ok(reserved.task_id);
  await crash(p);
  p = await boot();
  const retry = await call(reserve); assert.equal(retry.ok, true, JSON.stringify(p.output())); assert.deepEqual(retry.result, reserved);
  fs.writeFileSync(stateFile, JSON.stringify({schema_version:2,job_id:'job1',state:'received',task_id:reserved.task_id}));
  fs.writeFileSync(path.join(root,'crash-before-request'), '');
  const activation = {...reserve,method:'activate',task_id:reserved.task_id};
  await call(activation).catch(() => null); assert.equal((await p.exit).signal, 'SIGKILL');
  killGroup(p, 'SIGKILL'); fs.unlinkSync(socketPath); fs.unlinkSync(path.join(root,'crash-before-request'));
  d = db(); assert.equal(d.prepare('SELECT status FROM tasks WHERE id=?').get(reserved.task_id).status, 'queued'); d.close();
  p = await boot();
  assert.equal((await call(activation)).ok, true);
  const report = {report_id:'r1',job_id:'job1',round:1,evaluation_id:'eval1'};
  const digest = createHash('sha256').update(JSON.stringify(Object.fromEntries(Object.entries(report).sort()))).digest('hex');
  fs.writeFileSync(stateFile, JSON.stringify({schema_version:2,job_id:'job1',state:'received',task_id:reserved.task_id,outbox:[{kind:'evaluation_report',job_id:'job1',task_id:reserved.task_id,report_id:'r1',round:1,report,report_sha256:digest}]}));
  const event = {id:'evaluation.completed:r1',connector:'scieval',instance:'scieval',kind:'evaluation.completed',occurred_at:2,subject:{type:'scieval_job',id:'job1'},content:{text:JSON.stringify(report)}};
  d = db(); insertEvent(d, [2,'scieval',event.id,event.kind,'scieval_job','job1','scope',JSON.stringify(event),'scieval',null,JSON.stringify(report),'[]',Date.now(),Date.now(),'DONE']); d.close();
  assert.equal((await call({method:'accepted_scieval',report_id:'r1'})).result.length, 1);
  fs.writeFileSync(path.join(root,'crash-before-request'), '');
  const route = {method:'route_accepted',job_id:'job1',task_id:reserved.task_id,seq:2};
  await call(route).catch(() => null); assert.equal((await p.exit).signal, 'SIGKILL');
  killGroup(p, 'SIGKILL'); fs.unlinkSync(socketPath); fs.unlinkSync(path.join(root,'crash-before-request'));
  d = db(); assert.equal(d.prepare('SELECT count(*) n FROM task_inputs WHERE task_id=? AND seq=2').get(reserved.task_id).n, 1); d.close();
  p = await boot(); assert.equal((await call(route)).ok, true);
  d = db(); assert.equal(d.prepare('SELECT count(*) n FROM scieval_task_reservations').get().n, 1); assert.equal(d.prepare('SELECT count(*) n FROM task_inputs WHERE task_id=? AND seq=2').get(reserved.task_id).n, 1); d.close();
});
