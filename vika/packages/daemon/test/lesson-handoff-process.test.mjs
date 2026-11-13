import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const fixture = fileURLToPath(new URL('./lesson-handoff-process-fixture.mjs', import.meta.url));
test('captured lesson bytes survive a killed host and are read by a new process', (t) => {
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), 'lesson-process-'));
  t.after(() => fs.rmSync(workspace, { recursive: true, force: true }));
  const captured = spawnSync(process.execPath, [fixture, 'capture', workspace], { encoding: 'utf8' });
  assert.equal(captured.signal, 'SIGKILL', captured.stderr);
  fs.writeFileSync(path.join(workspace, 'tasks/t1/lesson.html'), '<p>changed after capture</p>');
  const resumed = spawnSync(process.execPath, [fixture, 'read', workspace], { encoding: 'utf8' });
  assert.equal(resumed.status, 0, resumed.stderr);
  const handoff = JSON.parse(resumed.stdout);
  assert.equal(handoff.lesson_id, 'lesson');
  assert.equal(handoff.job_id, 'job');
  assert.equal(Buffer.from(handoff.artifact.content_base64, 'base64').toString(), '<p>captured lesson</p>');
});
