// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
import { normalizeEvent } from '../event.mjs';

const STATES = new Set(['received', 'preparing', 'evaluating', 'revising', 'awaiting_review', 'approved', 'rejected', 'published']);

// P1's outbox has no creation timestamp. Use ingress time to avoid classifying
// a recovered event as stale. The persistent event id remains unchanged.
export function toRuntimeEvent(event, now = Date.now()) {
  if (!event || typeof event !== 'object' || Array.isArray(event)
      || typeof event.event_id !== 'string' || !event.event_id
      || typeof event.job_id !== 'string' || !event.job_id
      || !Number.isSafeInteger(event.revision) || event.revision < 1
      || !Number.isSafeInteger(event.round) || event.round < 1
      || !STATES.has(event.from) || !STATES.has(event.to)) throw new Error('invalid SciEval outbox event');
  const change = { event_id: event.event_id, job_id: event.job_id, revision: event.revision,
    from: event.from, to: event.to, round: event.round };
  const envelope = {
    protocol_version: '0.2', type: 'EVENT', connector: 'scieval', instance: 'scieval',
    id: event.event_id, kind: 'scieval.job.transition', occurred_at: now,
    subject: { type: 'job', id: event.job_id }, actor: null,
    content: { title: 'SciEval job state changed',
      text: `Job ${event.job_id}: ${event.from} -> ${event.to} (revision ${event.revision}, round ${event.round})`, attachments: [] },
    conversation: null, message: null, reply_route: null, data: { scieval: change },
  };
  normalizeEvent(envelope);
  return envelope;
}

// Harness returns undefined both for successful ingestion and for silent drops.
// Verify its persisted ledger, including the payload, before acknowledging P1.
export function harnessSubmitter(harness, store) {
  if (typeof harness?.handleConnectorEvent !== 'function' || typeof store?.getInboundByEvent?.get !== 'function') {
    throw new TypeError('harness and its event store are required');
  }
  return async (envelope) => {
    await harness.handleConnectorEvent(envelope);
    const row = store.getInboundByEvent.get(envelope.instance, envelope.id);
    if (!row || row.filter_reason) throw new Error('SciEval event was not accepted by Runtime');
    let saved;
    try { saved = JSON.parse(row.event_json); } catch { throw new Error('invalid Runtime ledger entry'); }
    const expected = envelope.data.scieval;
    const actual = saved.data?.scieval;
    if (saved.connector !== envelope.connector || saved.kind !== envelope.kind
        || saved.subject?.id !== envelope.subject.id || saved.subject?.type !== 'job'
        || !actual || Object.keys(expected).some((key) => actual[key] !== expected[key])) {
      throw new Error('SciEval event id collision');
    }
    return { accepted: true };
  };
}

// Inject a local bridge implementing pending() and acknowledge(eventId), and a
// durable submitter. No polling, credentials, network, or activation by default.
export function createSciEvalAdapter({ enabled = false, outbox, submit, now = Date.now, shouldStop = () => false } = {}) {
  if (enabled !== true) return { async drain() { return { enabled: false, delivered: 0 }; } };
  if (typeof outbox?.pending !== 'function' || typeof outbox?.acknowledge !== 'function' || typeof submit !== 'function') {
    throw new TypeError('enabled SciEval adapter requires outbox and submit');
  }
  let running = false;
  return { async drain() {
    if (running) throw new Error('SciEval adapter is already draining');
    running = true;
    let delivered = 0;
    try {
      const events = await outbox.pending();
      if (!Array.isArray(events)) throw new TypeError('outbox pending must return an array');
      for (const event of events) {
        if (shouldStop()) break;
        const result = await submit(toRuntimeEvent(event, now()));
        if (result?.accepted !== true) throw new Error('Runtime did not confirm acceptance');
        await outbox.acknowledge(event.event_id);
        delivered++;
      }
      return { enabled: true, delivered };
    } finally { running = false; }
  } };
}

// Manual integration point: callers decide when to drain; no background worker.
export async function createLocalSciEvalAdapter({ enabled = false, harness, store, shouldStop, ...options } = {}) {
  if (enabled !== true) return createSciEvalAdapter();
  const { createLocalOutbox } = await import('./local-outbox.mjs');
  return createSciEvalAdapter({ enabled: true, outbox: createLocalOutbox(options), submit: harnessSubmitter(harness, store), shouldStop });
}
