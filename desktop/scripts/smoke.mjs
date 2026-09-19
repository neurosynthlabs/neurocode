// Launches the desktop app for real and checks that it comes up: it starts its own API (never the one already
// running on this machine) on the database named by NEUROCODE_SMOKE_DATABASE_URL, serves the built web app with
// /api proxied beside it, loads it in a window that is never shown, waits until the page has rendered and reached
// the API through the proxy with the bridge present, then quits — and checks the API it started stopped with it.
//   NEUROCODE_SMOKE_DATABASE_URL=postgresql+asyncpg://…/neurocode_test_4 node desktop/scripts/smoke.mjs [--packed]
// --packed runs the app desktop:build made (desktop/dist/mac-arm64/NeuroCode.app) instead of the sources.
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { DESKTOP, ROOT, prepare } from './prepare.mjs';

const db = process.env.NEUROCODE_SMOKE_DATABASE_URL;
if (!db) {
  console.error('✗ Set NEUROCODE_SMOKE_DATABASE_URL to a database the smoke run may use (never the one in daily use).');
  process.exit(2);
}
if (/\/neurocode$/.test(db)) {
  console.error('✗ That is the workspace database in daily use; the smoke run starts an API on it and refuses to.');
  process.exit(2);
}
if (!fs.existsSync(path.join(ROOT, 'dist/index.html'))) {
  console.error('✗ dist/index.html is missing: run npm run build first.');
  process.exit(2);
}

const packed = process.argv.includes('--packed');
const binary = path.join(DESKTOP, 'dist/mac-arm64/NeuroCode.app/Contents/MacOS/NeuroCode');
if (packed && !fs.existsSync(binary)) {
  console.error(`✗ ${binary} is missing: run npm run desktop:build first.`);
  process.exit(2);
}
if (!packed) prepare();
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'nc-desktop-smoke-'));
// Every model key is taken out: loading the app never calls a model, and a smoke run must not be able to.
const env = { ...process.env };
for (const k of Object.keys(env)) if (/_API_KEY$|^GITHUB(_MODELS)?_TOKEN$/.test(k)) delete env[k];
Object.assign(env, {
  NEUROCODE_DESKTOP_SMOKE: '1',
  NEUROCODE_DESKTOP_FIND: '0',
  NEUROCODE_DESKTOP_PROFILE: profile,
  NEUROCODE_DATABASE_URL: db,
});
delete env.ELECTRON_RUN_AS_NODE;

const electron = path.join(ROOT, 'node_modules/.bin/electron');
const t0 = Date.now();
const child = packed
  ? spawn(binary, [], { env, stdio: ['ignore', 'pipe', 'pipe'] })
  : spawn(electron, [path.join(DESKTOP, 'app')], { env, stdio: ['ignore', 'pipe', 'pipe'] });
let out = '';
child.stdout.on('data', (d) => { out += d; });
child.stderr.on('data', (d) => { out += d; });
const timer = setTimeout(() => { console.error('✗ no answer within 4 minutes'); child.kill('SIGKILL'); }, 240_000);
const code = await new Promise((r) => child.on('close', r));
clearTimeout(timer);

const line = out.split('\n').find((l) => l.startsWith('NC_SMOKE '));
const result = line ? JSON.parse(line.slice('NC_SMOKE '.length)) : { ok: false, reason: 'no result line' };
let stopped = true;
if (result.api && result.how === 'started') {
  stopped = await fetch(`${result.api}/health`, { signal: AbortSignal.timeout(1500) }).then(() => false, () => true);
}
fs.rmSync(profile, { recursive: true, force: true });
const secs = ((Date.now() - t0) / 1000).toFixed(1);
if (result.ok && code === 0 && stopped) {
  console.log(`✓ desktop smoke${packed ? ' (packed app)' : ''} · ${secs}s · page ${result.origin} · API ${result.how} at ${result.api} (stopped on quit) · bridge present`);
  console.log(`  rendered: "${result.text.slice(0, 120)}"`);
  process.exit(0);
}
console.error(`✗ desktop smoke failed (exit ${code}, API stopped: ${stopped}):`, JSON.stringify(result));
console.error(out.split('\n').slice(-30).join('\n'));
process.exit(1);
