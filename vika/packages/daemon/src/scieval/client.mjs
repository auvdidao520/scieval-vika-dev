// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import path from 'node:path';
import { eventMessage } from '../connectors/protocol.mjs';
import { safeError } from '../logger.mjs';

const DEFAULT_PAGE_LIMIT = 100;
const MAX_RESPONSE_BYTES = 1_000_000;

function sleep(ms, stopping) {
  return new Promise((resolve) => {
    const started = Date.now();
    const timer = setInterval(() => {
      if (stopping() || Date.now() - started >= ms) {
        clearInterval(timer);
        resolve();
      }
    }, Math.min(ms, 100));
  });
}

function requiredString(value, name, max = 256) {
  if (typeof value !== 'string' || !value || value.length > max
    || /[\u0000-\u001f\u007f]/.test(value)) throw new Error(`scieval event ${name} is invalid`);
  return value;
}

function normalizeReport(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('scieval event report must be an object');
  }
  const report = structuredClone(value);
  requiredString(report.report_id, 'report_id');
  requiredString(report.evaluation_id, 'evaluation_id');
  requiredString(report.job_id, 'job_id');
  requiredString(report.input_sha256, 'input_sha256', 64);
  if (!/^[a-f0-9]{64}$/.test(report.input_sha256)) throw new Error('scieval event input_sha256 is invalid');
  if (!Number.isSafeInteger(report.round) || report.round < 1) throw new Error('scieval event round is invalid');
  if (!Number.isFinite(Date.parse(report.created_at))) throw new Error('scieval event created_at is invalid');
  return report;
}

async function readJson(response, maxBytes = MAX_RESPONSE_BYTES) {
  const chunks = [];
  let size = 0;
  if (response.body) {
    for await (const chunk of response.body) {
      size += chunk.byteLength;
      if (size > maxBytes) {
        await response.body.cancel();
        throw new Error(`scieval response exceeds ${maxBytes} bytes`);
      }
      chunks.push(chunk);
    }
  }
  if (size === 0) return {};
  try {
    return JSON.parse(Buffer.concat(chunks, size).toString('utf8'));
  } catch {
    throw new Error('scieval response is not valid JSON');
  }
}

// Receives immutable evaluation reports over the provider's authenticated event
// API. Submission remains in the workspace orchestrator in this iteration.
export class ScievalClient {
  constructor(config, _registry, logger, { fetchImpl = globalThis.fetch } = {}) {
    this.config = config;
    this.settings = config.scieval;
    this.logger = logger;
    this.fetch = fetchImpl;
    this.instance = config.instance ?? 'scieval';
    this.statePath = path.join(config.dataDir, 'scieval', `${this.instance}-cursor.json`);
    this.token = null;
    this.loop = null;
    this.stopping = false;
    this.requestController = null;
  }

  async preflight() {
    this.token = fs.readFileSync(this.settings.tokenFile, 'utf8').trim();
    if (!this.token || this.token.length > 4096 || /[\r\n]/.test(this.token)) {
      throw new Error('scieval token file must contain one non-empty token');
    }
    await this.request('/health');
    return { version: 'scieval-events-v1', owner: null };
  }

  async request(pathname) {
    if (typeof this.fetch !== 'function') throw new Error('fetch is unavailable');
    const controller = new AbortController();
    this.requestController = controller;
    const timeout = setTimeout(() => controller.abort(), this.settings.requestTimeoutMs);
    try {
      const response = await this.fetch(new URL(pathname, `${this.settings.baseUrl}/`), {
        headers: { accept: 'application/json', authorization: `Bearer ${this.token}` },
        redirect: 'error',
        signal: controller.signal,
      });
      if (!response.ok) {
        let detail = '';
        try { detail = JSON.stringify(await readJson(response, 4_000)).slice(0, 500); } catch {}
        throw new Error(`scieval API returned ${response.status}${detail ? `: ${detail}` : ''}`);
      }
      return await readJson(response);
    } finally {
      clearTimeout(timeout);
      if (this.requestController === controller) this.requestController = null;
    }
  }

  loadCursor() {
    try {
      const state = JSON.parse(fs.readFileSync(this.statePath, 'utf8'));
      if (state.version !== 1 || state.instance !== this.instance || typeof state.cursor !== 'string') {
        throw new Error('scieval cursor state is invalid');
      }
      return state.cursor;
    } catch (error) {
      if (error.code === 'ENOENT') return '';
      throw error;
    }
  }

  saveCursor(cursor) {
    fs.mkdirSync(path.dirname(this.statePath), { recursive: true, mode: 0o700 });
    const temporary = `${this.statePath}.${process.pid}.tmp`;
    fs.writeFileSync(temporary, `${JSON.stringify({ version: 1, instance: this.instance, cursor })}\n`, { mode: 0o600 });
    fs.renameSync(temporary, this.statePath);
  }

  envelope(item) {
    const cursor = requiredString(item?.cursor, 'cursor', 512);
    const report = normalizeReport(item?.report);
    const completed = eventMessage('scieval', {
      id: `evaluation.completed:${report.report_id}`,
      instance: this.instance,
      kind: 'evaluation.completed',
      occurred_at: Date.parse(report.created_at),
      subject: { type: 'scieval_job', id: report.job_id },
      content: {
        title: `scieval ${report.job_id} round ${report.round}`,
        text: JSON.stringify(report),
      },
      data: {
        report_id: report.report_id,
        evaluation_id: report.evaluation_id,
        round: report.round,
        input_sha256: report.input_sha256,
      },
    });
    if (Buffer.byteLength(JSON.stringify(completed)) <= this.config.maxEventBytes) return { cursor, envelope: completed };

    // Do not silently drop a valid but oversized report. Emit a bounded event
    // that sends the job to human reconciliation instead of automatic revision.
    const failure = eventMessage('scieval', {
      id: `evaluation.completed:${report.report_id}`,
      instance: this.instance,
      kind: 'evaluation.failed',
      occurred_at: Date.parse(report.created_at),
      subject: { type: 'scieval_job', id: report.job_id },
      content: {
        title: `scieval report too large: ${report.job_id} round ${report.round}`,
        text: JSON.stringify({ report_id: report.report_id, evaluation_id: report.evaluation_id,
          error: 'report_exceeds_event_limit', requires_human_review: true }),
      },
      data: { report_id: report.report_id, evaluation_id: report.evaluation_id, requires_human_review: true },
    });
    return { cursor, envelope: failure };
  }

  startSubscription(onLine, onUnexpectedExit) {
    if (this.loop) throw new Error('SciEval watcher is already running');
    this.stopping = false;
    this.loop = this.watch(onLine).catch((error) => {
      if (!this.stopping) onUnexpectedExit({ code: null, signal: null, stderr: error.message });
    }).finally(() => { this.loop = null; });
  }

  async watch(onLine) {
    let cursor = this.loadCursor();
    let failures = 0;
    while (!this.stopping) {
      let hasMore = false;
      try {
        const page = await this.request(`/v1/events?after=${encodeURIComponent(cursor)}&limit=${DEFAULT_PAGE_LIMIT}`);
        if (!page || !Array.isArray(page.events)) throw new Error('scieval event page must contain an events array');
        for (const item of page.events) {
          if (this.stopping) break;
          const normalized = this.envelope(item);
          onLine(JSON.stringify(normalized.envelope));
          // Connector 0.2 has no Core ACK. A redelivery after a crash keeps the
          // same report id and is deduplicated by Core; active jobs also reconcile.
          this.saveCursor(normalized.cursor);
          cursor = normalized.cursor;
        }
        hasMore = page.has_more === true;
        failures = 0;
      } catch (error) {
        if (!this.stopping) {
          failures += 1;
          this.logger.warn('scieval_poll_failed', { error: safeError(error), failures });
        }
      }
      if (!this.stopping) {
        const delay = hasMore ? 0 : failures
          ? Math.min(60_000, this.settings.pollIntervalMs * 2 ** Math.min(failures - 1, 4))
          : this.settings.pollIntervalMs;
        await sleep(delay, () => this.stopping);
      }
    }
  }

  async stop() {
    this.stopping = true;
    this.requestController?.abort();
    await this.loop;
  }
}
