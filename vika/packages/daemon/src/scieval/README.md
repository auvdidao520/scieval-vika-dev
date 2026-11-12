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

## Read-only OpenViking resource discovery

`createSciEvalResourceReader` reuses Vika's configured OpenViking identity and existing list/read APIs. It is a manual, opt-in inspection interface; it does not write resources, update the P1 database, provide a kanban, or run automatically. Supply an explicitly selected canonical resource root and the instance's OV config path:

```js
import { createSciEvalResourceReader } from './scieval/ov-resources.mjs';
const resources = createSciEvalResourceReader({ configPath: '/path/to/instance-ov.json', root: 'viking://resources/project' });
const listing = await resources.list({ limit: 100 });
const page = await resources.read({ uri: 'viking://resources/project/notes.md', limit: 200 });
// page.data.next_offset is null at end; page.data.line_cut indicates an inaccessible remainder.
```

Only canonical resource URIs at or below the selected root are accepted; pagination is bounded. The root check is a caller convenience, not a security boundary: OpenViking's own authentication and authorization remain authoritative. The read API returns formatted, untrusted text and can truncate long lines. It is unsuitable for immutable artifacts, binary files, approval or integrity checks. A missing resource, unavailable service or invalid credentials is an error, not an empty result. The project resource root and an actual kanban service contract still need to be identified before live P1 synchronization can be implemented.

## Manual approval-gated OV submission (experimental)

`scieval-vika-p0/ov_publish.py` submits the exact locally approved artifact bytes through Vika's OV connection. It requires an explicitly selected canonical project resource root, an absolute OV config path, and an injected real authorizer for `publish`; the included synthetic authorizer is for demonstrations only. It is not enabled or scheduled by the daemon. Example from a trusted local Python process (with `scieval-vika-p0` on `PYTHONPATH`):

```python
from foundations import Foundations
from ov_publish import OVPublisher, NodeOVTransport
f = Foundations('/absolute/path/to/p1.sqlite', authorizer=production_authorizer)
p = OVPublisher(f, 'viking://resources/explicit-project-root', NodeOVTransport('/absolute/path/to/instance-ov.json'))
result = p.submit(job_id, approved_revision, publish_credential)
```

The adapter verifies authorization, approval, revision, approved artifact version/digest and report digest inside one SQLite write transaction. It saves an attempt with a random child target before calling the local Node subprocess, which uses the existing OV temporary upload and resource import APIs. The result means the asynchronous import was **accepted**, not completed, and the job remains `approved`. OV may transform imported content; this does not establish byte-for-byte stored integrity. SQLite, code, configuration and the subprocess interface are trusted; the subprocess is not an independently authenticated publication API. OV permissions remain authoritative. Keep the selected root dedicated to this purpose; random targets reduce collisions but OV `to` is a replacing operation and this is not an atomic create-only guarantee.

Any error or crash after recording the attempt leaves `attempting`, including cases where OV may have accepted the request. Accepted and uncertain attempts both prevent another submission for that job. There is no automatic retry or reconciliation API: an operator must inspect the recorded target and OV task and decide how to recover; do not delete the record just to retry. The adapter does not mark the job published, verify remote completion, or implement distributed exactly-once delivery. A real project root, production authorizer, OV integration and recovery workflow remain unverified.
