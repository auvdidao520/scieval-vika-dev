// Runs one drain at a time; failures leave the outbox intact for the next poll.
import { createLocalSciEvalAdapter } from './adapter.mjs';
import { safeError } from '../logger.mjs';

export async function createSciEvalScheduler({ databasePath, pollIntervalMs, harness, store, logger, adapterFactory = createLocalSciEvalAdapter }) {
  if (!Number.isSafeInteger(pollIntervalMs) || pollIntervalMs < 1) throw new TypeError('invalid SciEval poll interval');
  let stopped = false;
  let timer = null;
  let active = null;
  const adapter = await adapterFactory({ enabled: true, databasePath, harness, store, shouldStop: () => stopped });
  function run() {
    if (stopped) return;
    active = Promise.resolve().then(() => adapter.drain()).catch((error) => {
      logger.warn('scieval_bridge_drain_failed', { error: safeError(error) });
    }).finally(() => {
      active = null;
      if (!stopped) {
        timer = setTimeout(run, pollIntervalMs);
        timer.unref();
      }
    });
  }
  return {
    start() { if (!stopped && !active && !timer) run(); },
    async stop() {
      stopped = true;
      if (timer) clearTimeout(timer);
      timer = null;
      await active;
    },
  };
}
