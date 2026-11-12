// SPDX-License-Identifier: AGPL-3.0
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import net from 'node:net';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { DatabaseSync } from 'node:sqlite';
import { TaskManager } from '../src/tasks.mjs';
import { MessageInbox } from '../src/inbox.mjs';
import { startReservationGateway } from '../src/scieval-gateway.mjs';

const root = fileURLToPath(new URL('../../../../', import.meta.url));
const client = path.join(root, 'scieval-vika-p0/tests/fixtures/delivery_client.py');
function run(args) { return new Promise((resolve, reject) => { const p = spawn('python3', [client, ...args]); let err=''; p.stderr.on('data', c => err+=c); p.on('error', reject); p.on('exit', code => resolve({code, err})); }); }
function call(socketPath, request) { return new Promise((resolve,reject) => { const s=net.connect(socketPath); let data=''; s.on('connect',()=>s.write(JSON.stringify(request)+'\n')); s.on('data',c=>data+=c); s.on('end',()=>resolve(JSON.parse(data))); s.on('error',reject); }); }

test('Python delivery validates digest and recovers after route succeeds but client crashes', async t => {
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'scieval-e2e-'));
  const db=new DatabaseSync(path.join(dir,'ledger.db'));
  db.exec(`CREATE TABLE connector_events(seq INTEGER PRIMARY KEY, status TEXT, event_id TEXT, connector_instance TEXT, scope_key TEXT, chat_id TEXT, thread_id TEXT, message_id TEXT, chat_type TEXT, source TEXT, sender_id TEXT, content TEXT, kind TEXT, subject_type TEXT, subject_id TEXT, event_json TEXT);
    INSERT INTO connector_events VALUES(1,'DONE','e1','lark','scope','chat',NULL,'m1','p2p','lark','owner','request',NULL,NULL,NULL,NULL);`);
  const inbox=new MessageInbox({db,runtime:{snapshot:()=>({work:{turnInProgress:true}})}});
  const tasks=new TaskManager({db,config:{workspace:dir},executor:{start(){return {done:new Promise(()=>{}),stop(){}};}},inbox,lineFor:r=>r.content});
  const grant={job_id:'job1',seq:1,event_id:'e1',connector_instance:'lark',source:'lark',scope_key:'scope',sender_id:'owner'};
  let binding;
  const server=await startReservationGateway({tasks,socketDir:dir,lockDir:dir,grants:[grant],rootUri:'viking://jobs',readState:()=>binding});
  t.after(async()=>{await server.close();tasks.shutdown();inbox.stop();db.close();fs.rmSync(dir,{recursive:true,force:true});});
  const reservation=(await call(server.socketPath,{method:'reserve',job_id:'job1',origin_seq:1})).result;
  binding={schema_version:2,job_id:'job1',state:'received',task_id:reservation.task_id};
  assert.equal((await call(server.socketPath,{method:'activate',job_id:'job1',origin_seq:1,task_id:reservation.task_id})).ok,true);
  const report={report_id:'r1',job_id:'job1',round:1,evaluation_id:'eval1',score:42};
  const event={id:'evaluation.completed:r1',connector:'scieval',instance:'scieval',kind:'evaluation.completed',subject:{type:'scieval_job',id:'job1'},content:{text:JSON.stringify(report)}};
  db.prepare('INSERT INTO connector_events(seq,status,event_id,connector_instance,source,content,kind,subject_type,subject_id,event_json) VALUES (?,?,?,?,?,?,?,?,?,?)').run(2,'DONE',event.id,'scieval','scieval','report',event.kind,'scieval_job','job1',JSON.stringify(event));
  const state=path.join(dir,'state.json'); const args=[server.socketPath,state,reservation.task_id];
  for (const mode of ['init','tamper']) { const r=await run([...args,mode]); assert.equal(r.code,0,r.err); }
  const count=()=>db.prepare('SELECT COUNT(*) n FROM task_inputs WHERE seq=2').get().n;
  assert.equal(count(),0);
  const crash=await run([...args,'crash']); assert.equal(crash.code,23,crash.err); assert.equal(count(),1);
  assert.equal(JSON.parse(fs.readFileSync(state)).outbox[0].delivered,false);
  const recover=await run([...args,'recover']); assert.equal(recover.code,0,recover.err); assert.equal(count(),1);
  assert.equal(JSON.parse(fs.readFileSync(state)).outbox[0].delivered,true);
  // The privileged RPC does not check the intent or digest; access to this socket is trusted.
  const forged={...event,id:'evaluation.completed:unmatched',content:{text:'{}'}};
  db.prepare('INSERT INTO connector_events(seq,status,event_id,connector_instance,source,content,kind,subject_type,subject_id,event_json) VALUES (?,?,?,?,?,?,?,?,?,?)').run(3,'DONE',forged.id,'scieval','scieval','unmatched',forged.kind,'scieval_job','job1',JSON.stringify(forged));
  assert.equal((await call(server.socketPath,{method:'route_accepted',job_id:'job1',task_id:reservation.task_id,seq:3})).ok,true);
  assert.equal(db.prepare('SELECT COUNT(*) n FROM task_inputs WHERE seq=3').get().n,1);
});

test('SIGKILL leaves stale socket and restart fails closed until operator removes it', async t => {
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'scieval-crash-'));
  const fixture=fileURLToPath(new URL('./scieval-gateway-crash-fixture.mjs',import.meta.url));
  const child=spawn(process.execPath,[fixture,dir],{stdio:['ignore','pipe','pipe']});
  t.after(()=>{child.kill('SIGKILL');fs.rmSync(dir,{recursive:true,force:true});});
  await new Promise((resolve,reject)=>{child.stdout.once('data',resolve);child.once('error',reject);child.once('exit',code=>reject(Error(`child exited ${code}`)));});
  const exited=new Promise(resolve=>child.once('exit',resolve)); child.kill('SIGKILL'); await exited;
  const socketPath=path.join(dir,'reservations.sock'); assert.equal(fs.lstatSync(socketPath).isSocket(),true);
  const db=new DatabaseSync(':memory:'); db.exec('CREATE TABLE tasks(id TEXT PRIMARY KEY)');
  const options={tasks:{db},socketDir:dir,lockDir:dir,grants:[],rootUri:'viking://jobs'};
  await assert.rejects(startReservationGateway(options),/socket already exists/);
  fs.unlinkSync(socketPath);
  const server=await startReservationGateway(options); await server.close(); db.close();
});
