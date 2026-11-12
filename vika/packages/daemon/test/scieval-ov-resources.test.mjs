// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { test } from 'node:test';
import { fakeOV } from './ov-server-fixture.mjs';
import { createSciEvalResourceReader } from '../src/scieval/ov-resources.mjs';

test('SciEval resource reader uses the configured OV identity and supports paged read-only discovery', async (t) => {
  const ov = await fakeOV(t);
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'scieval-ov-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const configPath = path.join(dir, 'ov.json');
  fs.writeFileSync(configPath, JSON.stringify({ url: ov.url, api_key: 'test-key' }));
  const root = 'viking://resources/project';
  ov.state.listings.set(root, { entries: [{ uri: `${root}/notes.md`, isDir: false }] });
  ov.state.files.set(`${root}/notes.md`, 'one\ntwo\nthree');
  const reader = createSciEvalResourceReader({ configPath, root });
  assert.equal((await reader.list()).entries[0].uri, `${root}/notes.md`);
  const page = await reader.read({ uri: `${root}/notes.md`, limit: 2 });
  assert.match(page.text, /one\ntwo/);
  assert.equal(page.data.next_offset, 2);
  assert.match((await reader.read({ uri: `${root}/notes.md`, offset: 2 })).text, /three/);
  assert.ok(ov.state.requests.every((r) => r.headers['x-api-key'] === 'test-key' && r.route.startsWith('GET ')));
  await assert.rejects(reader.read({ uri: 'viking://resources/project-other/secret' }), /outside/);
  await assert.rejects(reader.read({ uri: `${root}/../secret` }), /canonical/);
  await assert.rejects(reader.list({ limit: 201 }), /invalid page/);
  ov.state.down = true;
  await assert.rejects(reader.list(), (error) => error.code === 'openviking_unavailable');
});
