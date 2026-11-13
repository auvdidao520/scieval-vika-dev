// Exercise the host snapshot across an actual process exit and database reopen.
import fs from 'node:fs';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { TaskManager } from '../src/tasks.mjs';
import { MessageInbox } from '../src/inbox.mjs';

const [mode, workspace] = process.argv.slice(2);
const db = new DatabaseSync(path.join(workspace, 'state.sqlite3'));
const inbox = new MessageInbox({ db, runtime: { snapshot: () => ({ work: { turnInProgress: true } }) } });
let finish;
const executor = { start: () => ({ done: new Promise((resolve) => { finish = resolve; }), stop() {} }) };
const tasks = new TaskManager({ db, config: { workspace }, executor, inbox, lineFor: (row) => row.content });
if (mode === 'capture') {
  const task = tasks.create({ rows: [{ seq: 1, event_id: 'e1', connector_instance: 'test', source: 'test', scope_key: 'test:a', content: 'lesson' }], title: 'lesson' });
  tasks.bindLessonContext(task.id, { lessonId: 'lesson', jobId: 'job', round: 1, rubricId: 'scieval-equip-13', rubricVersion: 'draft-1', policyVersion: 'draft-1', passScore: 2, artifactPath: 'lesson.html' });
  fs.writeFileSync(path.join(task.workdir, 'lesson.html'), '<p>captured lesson</p>');
  tasks.report(task.id, { state: 'done' });
  finish({ text: '' });
  await new Promise((resolve) => setImmediate(resolve));
  const handoff = tasks.persistLessonReviewHandoff(task.id);
  fs.writeFileSync(path.join(workspace, 'report-id'), String(handoff.report_id));
  process.kill(process.pid, 'SIGKILL');
} else if (mode === 'read') {
  const reportId = Number(fs.readFileSync(path.join(workspace, 'report-id'), 'utf8'));
  process.stdout.write(JSON.stringify(tasks.getPersistedLessonReviewHandoff(reportId)));
  tasks.shutdown(); inbox.stop(); db.close();
} else throw new Error('unknown mode');
