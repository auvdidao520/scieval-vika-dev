# SciEval P1 Runtime adapter

This explicitly invoked local bridge connects an existing P1 SQLite outbox to the Vika Runtime event ledger. Python 3 and Node 22.19+ are required. No external credentials or network access are used at runtime. The database and bridge paths must be trusted local paths; run under an account permitted to read and update the P1 database.

```js
import { createLocalSciEvalAdapter } from './scieval/adapter.mjs';
const adapter = await createLocalSciEvalAdapter({
  enabled: true,
  databasePath: '/absolute/path/to/p1.sqlite',
  harness, store, // the actual Vika harness and its matching persistent store
  // python: 'python3', bridgePath: '/absolute/path/to/outbox_bridge.py',
});
await adapter.drain(); // caller controls scheduling and retries
```

The adapter is disabled by default; the manual API does not start a background worker. The separate daemon opt-in below starts polling. Each pending/ack call launches the standard-library Python bridge with `execFile` (no shell); JSON is passed through stdin/stdout. A missing database is an error, not an empty newly created database. Process timeout and output limits can be configured with `timeoutMs` and `maxBuffer`. Each drain processes at most 100 events and 1 MiB of JSON, in source order; later polls process the remainder. Set `maxBuffer` above 1 MiB plus the trailing newline. A single oversized event fails the poll and requires operator intervention.

For each event the harness is invoked and the matching unfiltered Runtime ledger record and payload are checked before the P1 acknowledgement is committed. A failed process, filtered event, collision, or failed acknowledgement stops the drain and leaves the event pending. After a crash between ledger insertion and source acknowledgement, replay keeps the same event ID and the Runtime ledger deduplicates it. P1 outbox events have no occurrence timestamps, so `occurred_at` is the ingestion attempt time.

A malformed or identity-mismatched outbox row does not prevent earlier valid rows in the same batch from being delivered. Once that row reaches the head of the queue, the poll fails visibly and does not skip it; repair is required before later rows can be processed.

Ledger acceptance does not mean agent processing or external delivery completed. This local integration does not establish authenticated identity, distributed exactly-once delivery or real SciEval evaluation; those require separate integration work. Treat the database and bridge configuration as privileged: callers with acknowledgement access can mark source events delivered.

## Daemon lifecycle

The daemon can poll the same local outbox when explicitly enabled:

```sh
VIKA_SCIEVAL_BRIDGE_ENABLED=true
VIKA_SCIEVAL_DATABASE_PATH=/absolute/path/to/p1.sqlite
VIKA_SCIEVAL_POLL_INTERVAL_MS=5000
```

The bridge is disabled by default. Set these environment variables on the daemon process; the database path must be absolute and the bundled Python bridge and Python 3 must be available locally. Polling starts after the Runtime and connector subscriptions are ready. Failed drains are logged and retried after the interval (100–3600000 ms, default 5000); only one drain runs at a time. Shutdown cancels the next poll and waits for the current event to finish before stopping the harness and closing the ledger. Remaining events are retried on the next start.
