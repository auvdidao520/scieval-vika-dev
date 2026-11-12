// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
import { OVToolClient } from '../ov-tools.mjs';

// An explicit, read-only entry point for inspecting an existing resource tree.
// The configured OpenViking identity remains responsible for authorization.
function resourceUri(uri) {
  if (typeof uri !== 'string' || !/^viking:\/\/(?:resources(?:\/|$)|user\/[^/]+\/resources(?:\/|$))/.test(uri)
      || /[\s%\\?#]/u.test(uri) || uri.slice('viking://'.length).split('/').some((s) => !s || s === '.' || s === '..')) {
    throw new TypeError('expected a canonical OpenViking resource URI');
  }
  return uri;
}

export function createSciEvalResourceReader({ configPath, root, fetch: fetchImpl } = {}) {
  const base = resourceUri(root);
  const client = new OVToolClient({ configPath, ...(fetchImpl ? { fetch: fetchImpl } : {}) });
  function scoped(uri) {
    resourceUri(uri);
    if (uri !== base && !uri.startsWith(`${base}/`)) throw new TypeError('URI is outside the configured resource root');
    return uri;
  }
  return {
    async list({ uri = base, offset = 0, limit = 100 } = {}) {
      scoped(uri);
      if (!Number.isSafeInteger(offset) || offset < 0 || !Number.isSafeInteger(limit) || limit < 1 || limit > 200) throw new TypeError('invalid page');
      const result = await client.list({ uri, offset, limit });
      return result.data;
    },
    async read({ uri, offset = 0, limit = 200 } = {}) {
      scoped(uri);
      if (!Number.isSafeInteger(offset) || offset < 0 || !Number.isSafeInteger(limit) || limit < 1 || limit > 200) throw new TypeError('invalid page');
      // Reuse Vika's read endpoint and its truncation/continuation metadata.
      return client.read({ uri, offset, limit });
    },
  };
}
