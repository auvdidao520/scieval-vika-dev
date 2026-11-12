// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { submitOV } from '../src/scieval/ov-publish-bridge.mjs';

test('upload and submit approved bytes using configured OV identity', async (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ov-publish-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const configPath = path.join(dir, 'ov.json');
  fs.writeFileSync(configPath, JSON.stringify({ url: 'http://localhost:1', api_key: 'key' }));
  const requests = [];
  const fetch = async (url, init) => {
    requests.push({ url, init });
    return new Response(JSON.stringify({ status: 'ok', result: requests.length === 1 ? { temp_file_id: 'temp' } : { task_id: 'task' } }));
  };
  const target = `viking://resources/project/submission-${'a'.repeat(32)}`;
  assert.deepEqual(await submitOV({ configPath, target, content: Buffer.from('approved').toString('base64'), name: 'artifact.html' }, fetch), { task_id: 'task' });
  assert.equal(await requests[0].init.body.get('file').text(), 'approved');
  assert.equal(requests[0].init.headers['X-API-Key'], 'key');
  assert.equal(JSON.parse(requests[1].init.body).to, target);
  assert.equal(JSON.parse(requests[1].init.body).wait, false);
  await assert.rejects(submitOV({ configPath, target: `viking://user/u/resources/vika/submission-${'a'.repeat(32)}`, content: '', name: 'artifact.html' }, fetch));
  assert.equal(requests.length, 2);
});

test('missing server task id is an uncertain outcome', async (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ov-publish-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const configPath = path.join(dir, 'ov.json');
  fs.writeFileSync(configPath, JSON.stringify({ url: 'http://localhost:1' }));
  let calls = 0;
  const fetch = async () => new Response(JSON.stringify({ status: 'ok', result: ++calls === 1 ? { temp_file_id: 'temp' } : {} }));
  await assert.rejects(submitOV({ configPath, target: `viking://resources/p/submission-${'b'.repeat(32)}`, content: '', name: 'artifact.html' }, fetch), /missing task id/);
});
