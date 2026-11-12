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
