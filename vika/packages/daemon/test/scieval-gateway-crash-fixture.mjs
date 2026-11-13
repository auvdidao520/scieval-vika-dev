// SPDX-License-Identifier: AGPL-3.0
import { DatabaseSync } from 'node:sqlite';
import { startReservationGateway } from '../src/scieval-gateway.mjs';
const dir = process.argv[2];
const db = new DatabaseSync(':memory:');
db.exec('CREATE TABLE tasks(id TEXT PRIMARY KEY)');
await startReservationGateway({tasks:{db},socketDir:dir,lockDir:dir,grants:[],rootUri:'viking://jobs'});
process.stdout.write('ready\n');
