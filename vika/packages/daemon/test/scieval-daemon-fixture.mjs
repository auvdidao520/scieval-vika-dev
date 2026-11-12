// SPDX-License-Identifier: AGPL-3.0
import path from 'node:path';
import fs from 'node:fs';
import { startMockEnv } from './mock-env.mjs';
const root = process.argv[2];
try {
  const env = await startMockEnv({ config: {
    scheduler: 'workers', scievalGatewayConfigFile: path.join(root, 'gateway.json'),
    dataDir: path.join(root, 'data'), databasePath: path.join(root, 'data', 'state.sqlite3'),
    lockPath: path.join(root, 'data', 'daemon.lock'), actionSocketPath: path.join(root, 'data', 'actions.sock'),
    tempDir: path.join(root, 'data', 'tmp'), workspace: path.join(root, 'workspace'),
  }});
  const request = env.daemon.tasks.request.bind(env.daemon.tasks);
  env.daemon.tasks.request = (...args) => {
    if (fs.existsSync(path.join(root, 'crash-before-request'))) process.kill(process.pid, 'SIGKILL');
    return request(...args);
  };
  process.stdout.write('READY\n');
  process.on('SIGTERM', async () => { await env.stop(); process.exit(0); });
} catch (error) { process.stderr.write(String(error.stack || error)); process.exit(1); }
