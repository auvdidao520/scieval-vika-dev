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

The adapter is disabled by default; this API does not start a background worker or change daemon startup. Each pending/ack call launches the standard-library Python bridge with `execFile` (no shell); JSON is passed through stdin/stdout. A missing database is an error, not an empty newly created database. Process timeout and output limits can be configured with `timeoutMs` and `maxBuffer`. The full pending batch must fit the output limit; increase it or reduce backlog if necessary.

For each event the harness is invoked and the matching unfiltered Runtime ledger record and payload are checked before the P1 acknowledgement is committed. A failed process, filtered event, collision, or failed acknowledgement stops the drain and leaves the event pending. After a crash between ledger insertion and source acknowledgement, replay keeps the same event ID and the Runtime ledger deduplicates it. P1 outbox events have no occurrence timestamps, so `occurred_at` is the ingestion attempt time.

Ledger acceptance does not mean agent processing or external delivery completed. This local integration does not establish authenticated identity, distributed exactly-once delivery, automatic polling, or real SciEval evaluation; those require separate integration work. Treat the database and bridge configuration as privileged: callers with acknowledgement access can mark source events delivered.
