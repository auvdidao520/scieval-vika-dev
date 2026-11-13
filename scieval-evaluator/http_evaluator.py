"""Authenticated HTTP evaluator adapter. No third-party dependency."""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from framework import Conflict

MAX_ARTIFACT_BYTES = 5 * 1024 * 1024
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class EvaluatorError(RuntimeError):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


class HttpEvaluator:
    """Submit immutable HTML bytes and retrieve reports from one trusted origin.

    The caller owns retry policy and must reuse the same idempotency key after a
    timeout. This client never invents a new key or follows a report URL to a
    different origin.
    """

    def __init__(self, base_url: str, token_file: str, *, timeout: float = 20.0, opener=None):
        parsed = urlsplit(base_url)
        loopback = parsed.hostname in ('localhost', '127.0.0.1', '::1')
        if parsed.scheme != 'https' and not (parsed.scheme == 'http' and loopback):
            raise ValueError('evaluator base URL must use HTTPS, except loopback HTTP')
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('evaluator base URL must not contain credentials, query or fragment')
        self.base_url = base_url.rstrip('/') + '/'
        self.origin = (parsed.scheme, parsed.netloc)
        self.token = Path(token_file).read_text(encoding='utf-8').strip()
        if not self.token or len(self.token) > 4096 or '\n' in self.token or '\r' in self.token:
            raise ValueError('token file must contain one non-empty token')
        self.timeout = timeout
        # Never forward the bearer token through an automatic redirect.
        self.opener = opener or build_opener(_NoRedirect).open

    def _request(self, method: str, path: str, payload: dict | None = None,
                 idempotency_key: str | None = None) -> tuple[dict, bytes]:
        url = urljoin(self.base_url, path.lstrip('/'))
        if (urlsplit(url).scheme, urlsplit(url).netloc) != self.origin:
            raise ValueError('request URL leaves the evaluator origin')
        body = None if payload is None else json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
        headers = {'Accept': 'application/json', 'Authorization': f'Bearer {self.token}'}
        if body is not None:
            headers['Content-Type'] = 'application/json; charset=utf-8'
        if idempotency_key:
            headers['Idempotency-Key'] = idempotency_key
        request = Request(url, data=body, headers=headers, method=method)
        try:
            with self.opener(request, timeout=self.timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as error:
            detail = error.read(4_000).decode('utf-8', errors='replace')
            raise EvaluatorError(f'evaluator HTTP {error.code}: {detail[:500]}') from error
        except URLError as error:
            raise EvaluatorError(f'evaluator request failed: {error.reason}') from error
        if len(raw) > MAX_RESPONSE_BYTES:
            raise EvaluatorError('evaluator response is too large')
        try:
            parsed = json.loads(raw.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise EvaluatorError('evaluator response is not valid JSON') from error
        if not isinstance(parsed, dict):
            raise EvaluatorError('evaluator response must be a JSON object')
        return parsed, raw

    def submit(self, request: dict, idempotency_key: str) -> str:
        required = ('job_id', 'round', 'uri', 'sha256')
        if any(key not in request for key in required):
            raise ValueError('missing request field')
        if not isinstance(request['job_id'], str) or not request['job_id']:
            raise ValueError('invalid job id')
        if not isinstance(idempotency_key, str) or not idempotency_key or len(idempotency_key) > 256:
            raise ValueError('invalid idempotency key')
        if 'content_bytes' in request:
            content = request['content_bytes']
            if not isinstance(content, bytes):
                raise ValueError('artifact bytes required')
        else:
            path = Path(request['uri'])
            if path.is_symlink() or not path.is_file():
                raise ValueError('artifact must be a regular, non-symlink file')
            content = path.read_bytes()
        if len(content) > MAX_ARTIFACT_BYTES:
            raise ValueError('artifact is too large')
        if hashlib.sha256(content).hexdigest() != request['sha256']:
            raise ValueError('artifact digest mismatch')
        if not isinstance(request['round'], int) or request['round'] < 1:
            raise ValueError('invalid round')
        payload = {
            'schema_version': '1.0',
            'job_id': request['job_id'],
            'round': request['round'],
            'artifact': {
                'uri': request['uri'],
                'sha256': request['sha256'],
                'media_type': 'text/html',
                'content_base64': base64.b64encode(content).decode('ascii'),
            },
            'rubric': request.get('rubric', {'id': 'scieval-equip-13', 'version': 'draft-1'}),
            'policy_version': request.get('policy_version', 'draft-1'),
        }
        response, _ = self._request('POST', '/v1/evaluations', payload, idempotency_key)
        evaluation_id = response.get('evaluation_id')
        if not isinstance(evaluation_id, str) or not evaluation_id:
            raise EvaluatorError('submission response has no evaluation_id')
        return evaluation_id

    def query(self, evaluation_id: str) -> dict[str, Any]:
        if not isinstance(evaluation_id, str) or not evaluation_id:
            raise ValueError('invalid evaluation id')
        status, _ = self._request('GET', f'/v1/evaluations/{quote(evaluation_id, safe="")}')
        if status.get('status') != 'succeeded':
            return status
        if 'report' in status:
            report = status['report']
            if not isinstance(report, dict):
                raise EvaluatorError('inline report must be an object')
            return report
        report_url = status.get('report_url')
        if not isinstance(report_url, str) or not report_url:
            raise EvaluatorError('successful evaluation has no report')
        absolute = urljoin(self.base_url, report_url)
        if (urlsplit(absolute).scheme, urlsplit(absolute).netloc) != self.origin:
            raise EvaluatorError('report URL leaves the evaluator origin')
        path = absolute[len(self.base_url):] if absolute.startswith(self.base_url) else absolute
        report, raw = self._request('GET', path)
        expected = status.get('report_sha256')
        if expected is not None and hashlib.sha256(raw).hexdigest() != expected:
            raise Conflict('report digest mismatch')
        return report
