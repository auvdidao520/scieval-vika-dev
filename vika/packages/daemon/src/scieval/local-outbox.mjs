// Local, explicit opt-in bridge. Arguments are passed directly, never through a shell.
import { execFile } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const defaultBridge = fileURLToPath(new URL('../../../../../scieval-vika-p0/outbox_bridge.py', import.meta.url));

export function createLocalOutbox({ databasePath, python = 'python3', bridgePath = defaultBridge, timeoutMs = 10000, maxBuffer = 8 * 1024 * 1024 } = {}) {
  if (typeof databasePath !== 'string' || !path.isAbsolute(databasePath) || typeof python !== 'string' || !python
      || typeof bridgePath !== 'string' || !path.isAbsolute(bridgePath)
      || !Number.isSafeInteger(timeoutMs) || timeoutMs < 1 || !Number.isSafeInteger(maxBuffer) || maxBuffer < 1) {
    throw new TypeError('absolute database and bridge paths and valid process limits required');
  }
  function request(payload) {
    return new Promise((resolve, reject) => {
      const child = execFile(python, [bridgePath, databasePath], { timeout: timeoutMs, maxBuffer, encoding: 'utf8', windowsHide: true }, (error, stdout) => {
        if (error) return reject(new Error('SciEval outbox bridge failed', { cause: error }));
        try { resolve(JSON.parse(stdout)); } catch { reject(new Error('invalid SciEval outbox bridge response')); }
      });
      child.stdin.on('error', () => {}); // callback reports a failed child
      child.stdin.end(JSON.stringify(payload));
    });
  }
  return {
    async pending() {
      const response = await request({ command: 'pending' });
      if (!Array.isArray(response?.events)) throw new Error('invalid SciEval pending response');
      return response.events;
    },
    async acknowledge(eventId) {
      if (typeof eventId !== 'string' || !eventId || eventId.length > 1024) throw new TypeError('invalid event id');
      const response = await request({ command: 'acknowledge', event_id: eventId });
      if (response?.acknowledged !== true) throw new Error('invalid SciEval acknowledgement response');
    },
  };
}
