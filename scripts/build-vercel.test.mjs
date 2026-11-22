import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildOutput, deploymentRoutes } from './build-vercel.mjs';

test('API and health routes point to the configured HTTPS backend before static files', () => {
  const routes = deploymentRoutes('https://proofline-api.up.railway.app/');
  assert.equal(routes[0].dest, 'https://proofline-api.up.railway.app/api/$1');
  assert.equal(routes[0].headers['Cache-Control'], 'no-store');
  assert.equal(routes[1].dest, 'https://proofline-api.up.railway.app/healthz');
  assert.deepEqual(routes[2], { handle: 'filesystem' });
});

test('deployment fails without a valid public API origin, including embedded secrets', () => {
  for (const value of [undefined, '', 'http://localhost:8000', 'https://example.com',
    'https://user:secret@api.up.railway.app', 'https://api.up.railway.app/path',
    'https://api.up.railway.app?token=secret', 'https://api.up.railway.app#secret']) {
    assert.throws(() => deploymentRoutes(value), error => !error.message.includes('secret'));
  }
});

// Check the emitted artifact, not just the route builder.
test('static deployment output contains assets and routes, with no server environment files', async () => {
  const { mkdtemp, mkdir, writeFile, readFile, readdir, rm } = await import('node:fs/promises');
  const { tmpdir } = await import('node:os');
  const path = await import('node:path');
  const root = await mkdtemp(path.join(tmpdir(), 'proofline-vercel-'));
  try {
    await mkdir(path.join(root, 'frontend/dist'), { recursive: true });
    await writeFile(path.join(root, 'frontend/dist/index.html'), '<h1>Proofline</h1>');
    await writeFile(path.join(root, '.env'), 'fixture-only');
    await buildOutput({ root, apiOrigin: 'https://api.fixture.invalid' });
    assert.deepEqual(await readdir(path.join(root, '.vercel/output/static')), ['index.html']);
    const config = JSON.parse(await readFile(path.join(root, '.vercel/output/config.json'), 'utf8'));
    assert.equal(config.version, 3);
    assert.equal(config.routes[0].dest, 'https://api.fixture.invalid/api/$1');
    assert.equal(await readFile(path.join(root, '.vercel/output/static/index.html'), 'utf8'), '<h1>Proofline</h1>');
  } finally { await rm(root, { recursive: true, force: true }); }
});
