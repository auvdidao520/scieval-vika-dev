#!/usr/bin/env node
// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// Run the committed daemon suite from a disposable snapshot on a local disk.
// Windows/WSL shared mounts can make Pi's module graph take minutes to import.
// This keeps the suite's real startup deadlines and never copies credentials,
// untracked files, or the checkout's node_modules. Commit edits before running.
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const checkout = fileURLToPath(new URL('..', import.meta.url));
const stage = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-daemon-regression-'));
const npmCli = process.env.npm_execpath;
try {
  const root = execFileSync('git', ['rev-parse', '--show-toplevel'], { cwd: checkout, encoding: 'utf8' }).trim();
  const app = path.join(stage, path.relative(root, checkout));
  const commit = execFileSync('git', ['rev-parse', 'HEAD'], { cwd: checkout, encoding: 'utf8' }).trim();
  console.log(`Testing committed Vika snapshot ${commit} in ${stage}`);
  console.log('Use a native local filesystem for TMPDIR; uncommitted edits are excluded.');
  const archive = path.join(stage, 'source.tar');
  execFileSync('git', ['archive', '--format=tar', `--output=${archive}`, 'HEAD'], { cwd: root });
  execFileSync('tar', ['-xf', archive, '-C', stage]);
  fs.rmSync(archive);
  const installArgs = ['ci', '--ignore-scripts', '--no-audit', '--no-fund'];
  execFileSync(npmCli ? process.execPath : 'npm', npmCli ? [npmCli, ...installArgs] : installArgs, { cwd: app, stdio: 'inherit' });
  const tests = fs.readdirSync(path.join(app, 'packages/daemon/test'))
    .filter((file) => file.endsWith('.test.mjs')).sort()
    .map((file) => `packages/daemon/test/${file}`);
  execFileSync(process.execPath, ['--test', '--test-timeout=30000', ...tests], { cwd: app, stdio: 'inherit' });
} catch (error) {
  process.exitCode = Number.isInteger(error.status) && error.status > 0 ? error.status : 1;
  console.error(`Daemon snapshot failed: ${error.code ?? error.signal ?? `exit ${process.exitCode}`}`);
} finally {
  fs.rmSync(stage, { recursive: true, force: true });
}
