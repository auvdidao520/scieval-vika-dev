// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { afterEach, describe, it } from 'node:test';
import { ScievalClient } from '../src/scieval/client.mjs';
import { getConnectorManifest } from '../src/connectors/catalog.mjs';

const cleanups = [];
afterEach(async () => {
  while (cleanups.length) await cleanups.pop()();
});

function report(id = 'rpt-1', round = 1) {
  return {
    report_id: id,
    evaluation_id: `eval-${id}`,
    job_id: 'job-1',
    round,
    input_sha256: 'a'.repeat(64),
    created_at: '2026-11-12T06:00:00Z',
    criteria: [],
    decision: { result: 'revise', reason: 'fixture' },
  };
}

async function server(handler) {
  const instance = http.createServer(handler);
  await new Promise((resolve) => instance.listen(0, '127.0.0.1', resolve));
  cleanups.push(() => new Promise((resolve) => instance.close(resolve)));
  return `http://127.0.0.1:${instance.address().port}`;
}

function fixture(baseUrl, directory, overrides = {}) {
  const tokenFile = path.join(directory, 'scieval.token');
  if (!fs.existsSync(tokenFile)) fs.writeFileSync(tokenFile, 'test-token', { mode: 0o600 });
  return {
    dataDir: directory,
    instance: 'scieval',
    maxEventBytes: 100_000,
    scieval: {
      baseUrl,
      tokenFile,
      pollIntervalMs: 10,
      requestTimeoutMs: 1_000,
    },
    ...overrides,
  };
}

function client(config, warnings) {
  return new ScievalClient(config, null, {
    info() {},
    warn(message, fields) { warnings.push({ message, fields }); },
  });
}

describe('scieval connector', { concurrency: false }, () => {
  it('declares events only, without implying an evaluation submit tool', () => {
    const manifest = getConnectorManifest('scieval');
    assert.deepEqual(manifest.capabilities, ['events']);
    assert.deepEqual(manifest.actions, {});
  });

  it('polls authenticated events, keeps stable ids and resumes its durable cursor', async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'scieval-client-'));
    cleanups.push(() => fs.rmSync(directory, { recursive: true, force: true }));
    const requests = [];
    const baseUrl = await server((request, response) => {
      requests.push({ url: request.url, authorization: request.headers.authorization });
      response.setHeader('content-type', 'application/json');
      if (request.url === '/health') {
        response.end('{"ok":true}');
      } else if (request.url.startsWith('/v1/events?after=&')) {
        response.end(JSON.stringify({ events: [{ cursor: 'cursor-1', report: report() }], has_more: false }));
      } else {
        response.end(JSON.stringify({ events: [], has_more: false }));
      }
    });
    const config = fixture(baseUrl, directory);
    const warnings = [];
    const first = client(config, warnings);
    await first.preflight();
    const events = [];
    const unexpected = [];
    const received = new Promise((resolve) => {
      first.startSubscription((line) => {
        events.push(JSON.parse(line));
        resolve();
      }, (error) => unexpected.push(error));
    });
    await received;
    await first.stop();
    assert.deepEqual(unexpected, []);
    assert.equal(events[0].id, 'evaluation.completed:rpt-1');
    assert.equal(events[0].kind, 'evaluation.completed');
    assert.equal(events[0].subject.id, 'job-1');
    assert.equal(events[0].data.evaluation_id, 'eval-rpt-1');
    assert.equal(JSON.parse(events[0].content.text).decision.result, 'revise');
    assert.ok(requests.every((request) => request.authorization === 'Bearer test-token'));
    assert.deepEqual(JSON.parse(fs.readFileSync(path.join(directory, 'scieval', 'scieval-cursor.json'))).cursor, 'cursor-1');

    const second = client(config, warnings);
    await second.preflight();
    const replayed = [];
    second.startSubscription((line) => replayed.push(line), (error) => unexpected.push(error));
    await new Promise((resolve) => setTimeout(resolve, 30));
    await second.stop();
    assert.equal(replayed.length, 0);
    assert.ok(requests.some((request) => request.url.startsWith('/v1/events?after=cursor-1')));
    assert.deepEqual(unexpected, []);
    assert.deepEqual(warnings, []);
  });

  it('turns an oversized report into a bounded human-review event', async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'scieval-large-'));
    cleanups.push(() => fs.rmSync(directory, { recursive: true, force: true }));
    const large = { ...report('rpt-large'), large_text: 'x'.repeat(5_000) };
    const baseUrl = await server((_request, response) => {
      response.setHeader('content-type', 'application/json');
      response.end(JSON.stringify({ events: [{ cursor: 'c1', report: large }], has_more: false }));
    });
    const current = client(fixture(baseUrl, directory, { maxEventBytes: 1_000 }), []);
    const events = [];
    const received = new Promise((resolve) => {
      current.startSubscription((line) => { events.push(JSON.parse(line)); resolve(); }, () => {});
    });
    await received;
    await current.stop();
    assert.equal(events[0].kind, 'evaluation.failed');
    assert.equal(events[0].id, 'evaluation.completed:rpt-large');
    assert.equal(events[0].data.requires_human_review, true);
    assert.ok(Buffer.byteLength(JSON.stringify(events[0])) < 1_000);
  });

  it('fails preflight on provider errors and aborts an active request on stop', async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'scieval-errors-'));
    cleanups.push(() => fs.rmSync(directory, { recursive: true, force: true }));
    const unauthorized = await server((_request, response) => {
      response.statusCode = 401;
      response.setHeader('content-type', 'application/json');
      response.end('{"error":"unauthorized"}');
    });
    const rejected = client(fixture(unauthorized, directory), []);
    await assert.rejects(() => rejected.preflight(), /401/);

    const hanging = await server(async (_request, response) => {
      await new Promise((resolve) => setTimeout(resolve, 100));
      response.end('{}');
    });
    const current = client(fixture(hanging, directory), []);
    current.token = 'test-token';
    const pending = current.request('/v1/events');
    await current.stop();
    await assert.rejects(() => pending, /abort/i);
  });
});
