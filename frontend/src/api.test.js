import { afterEach, test } from 'node:test';
import assert from 'node:assert/strict';
import { citationUrl, getReport, request, uploadPdf } from './api.js';
const originalFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = originalFetch; });

test('report credentials travel in the bearer header, never the URL', async () => {
  globalThis.fetch = async (url, options) => {
    assert.equal(url, '/api/runs/run-1');
    assert.equal(options.headers.Authorization, 'Bearer private-report-token');
    assert.equal(options.cache, 'no-store');
    return new Response(JSON.stringify({ state: 'running' }));
  };
  assert.deepEqual(await getReport({ run_id: 'run-1', access_token: 'private-report-token' }), { state: 'running' });
});

test('uploads use the existing multipart file contract', async () => {
  globalThis.fetch = async (url, options) => {
    assert.equal(url, '/api/runs');
    assert.equal(options.method, 'POST');
    assert.equal(options.body.get('file').name, 'sample.pdf');
    assert.equal(await options.body.get('file').text(), '%PDF-fixture');
    return new Response(JSON.stringify({ run_id: 'run-1', access_token: 'token' }), { status: 202 });
  };
  assert.equal((await uploadPdf(new File(['%PDF-fixture'], 'sample.pdf'))).run_id, 'run-1');
});

test('unavailable and transient reports preserve HTTP error status for the poller', async () => {
  for (const status of [404, 503]) {
    globalThis.fetch = async () => new Response(JSON.stringify({ error: { message: 'Report unavailable' } }), { status });
    await assert.rejects(request('/api/runs/run-1'), error => error.status === status && error.message === 'Report unavailable');
  }
});

test('citations accept HTTP(S) and reject executable or malformed URLs', () => {
  assert.equal(citationUrl('https://example.org/report'), 'https://example.org/report');
  assert.equal(citationUrl('http://example.org/report'), 'http://example.org/report');
  for (const url of ['javascript:alert(1)', 'data:text/html,unsafe', '/relative', 'not a URL']) assert.equal(citationUrl(url), null);
});
