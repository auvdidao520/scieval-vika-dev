// Test-only daemon host: each invocation has its own Node process and heap.
import { startMockEnv } from './mock-env.mjs';

const [source, runtime, eventId] = process.argv.slice(2);
const env = await startMockEnv({ config: { scievalBridgeEnabled: true, scievalDatabasePath: source, scievalPollIntervalMs: 100, databasePath: runtime } });
process.on('message', async (message) => {
  if (message === 'stop') { await env.stop(); process.exit(0); }
});
await env.waitFor(() => !!env.daemon.store.getInboundByEvent.get('scieval', eventId));
process.send?.({ ledgered: eventId, pid: process.pid, dir: env.dir });
