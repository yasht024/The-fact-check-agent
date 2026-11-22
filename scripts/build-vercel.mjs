// Emit static React assets and external API routes using Vercel Build Output API.
import { cp, mkdir, rm, writeFile } from 'node:fs/promises';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';

export function deploymentRoutes(value) {
  let origin;
  try { origin = new URL(value); } catch { throw new Error('Set RAILWAY_API_ORIGIN to the Railway API HTTPS origin.'); }
  if (origin.protocol !== 'https:' || origin.username || origin.password || origin.search || origin.hash || origin.pathname !== '/') {
    throw new Error('RAILWAY_API_ORIGIN must be an HTTPS origin without credentials, path, query, or fragment.');
  }
  if (['localhost', '127.0.0.1', 'example.com'].includes(origin.hostname) || origin.hostname.endsWith('.example.com')) {
    throw new Error('Set RAILWAY_API_ORIGIN to the actual deployed API, not a local/example address.');
  }
  return [
    { src: '^/api/(.*)$', dest: `${origin.origin}/api/$1`, headers: { 'Cache-Control': 'no-store' } },
    { src: '^/healthz$', dest: `${origin.origin}/healthz`, headers: { 'Cache-Control': 'no-store' } },
    { handle: 'filesystem' },
  ];
}

export async function buildOutput({
  root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..'),
  apiOrigin = process.env.RAILWAY_API_ORIGIN,
} = {}) {
  // Validate before replacing existing artifacts. The origin is public, not a secret.
  const routes = deploymentRoutes(apiOrigin);
  const output = path.join(root, '.vercel/output');
  await rm(output, { recursive: true, force: true });
  await mkdir(output, { recursive: true });
  await cp(path.join(root, 'frontend/dist'), path.join(output, 'static'), { recursive: true });
  await writeFile(path.join(output, 'config.json'), `${JSON.stringify({ version: 3, routes }, null, 2)}\n`);
  console.log('Vercel static assets and Railway API routes prepared.');
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  buildOutput().catch(error => { console.error(error.message); process.exitCode = 1; });
}
