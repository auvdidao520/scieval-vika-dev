// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
// Trusted local subprocess of ov_publish.py; never expose as an unauthenticated API.
import { OVToolClient, reservedTarget } from '../ov-tools.mjs';

export async function submitOV({ configPath, target, content, name }, fetchImpl) {
  if (typeof configPath !== 'string' || !configPath.startsWith('/') ||
      typeof target !== 'string' || !/^viking:\/\/(?:resources|user\/[^/]+\/resources)(?:\/[^/]+)+\/submission-[a-f0-9]{32}$/.test(target) ||
      /[\s%\\?#\x00-\x1f\x7f]/u.test(target) || target.slice(9).split('/').some((p) => !p || p === '.' || p === '..') || reservedTarget(target, { replaces: true })) {
    throw new Error('invalid configuration or target');
  }
  if (typeof content !== 'string' || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(content)) throw new Error('invalid content');
  if (typeof name !== 'string' || !/^artifact\.[a-z0-9]{1,10}$/.test(name)) throw new Error('invalid filename');
  const bytes = Buffer.from(content, 'base64');
  if (bytes.length > 20 * 1024 * 1024) throw new Error('artifact exceeds upload limit');
  const client = new OVToolClient({ configPath, ...(fetchImpl ? { fetch: fetchImpl } : {}) });
  const upload = await client.conn().upload('/api/v1/resources/temp_upload', { data: bytes, name });
  if (!upload.result?.temp_file_id) throw new Error('upload not accepted');
  const result = await client.call('/api/v1/resources', { temp_file_id: upload.result.temp_file_id, source_name: name, to: target, wait: false, create_parent: true });
  if (typeof result.result?.task_id !== 'string' || !result.result.task_id.trim()) throw new Error('missing task id');
  return { task_id: result.result.task_id };
}

if (process.argv[1] && import.meta.url === new URL(`file://${process.argv[1]}`).href) {
  try {
    let input = '';
    for await (const chunk of process.stdin) {
      input += chunk;
      if (input.length > 28 * 1024 * 1024) throw new Error('request too large');
    }
    process.stdout.write(JSON.stringify(await submitOV(JSON.parse(input))));
  } catch {
    process.stderr.write('OV submission failed or outcome unknown\n');
    process.exitCode = 1;
  }
}
