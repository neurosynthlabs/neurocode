#!/usr/bin/env node
// NeuroCode full-stack test. It runs the real API on a throwaway database and the real web app in dev
// mode, proxying to it, then drives a headless browser to prove two things: what the operator does is
// written to the database, and what the server records streams into an open tab.
//
//   npm run e2e          needs uv; the API's dependencies are installed on the first run
import { spawn, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright-core';
import { findChrome } from './chrome.mjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const API_PORT = Number(process.env.E2E_API_PORT ?? 8797);
const WEB_PORT = Number(process.env.E2E_WEB_PORT ?? 5197);
const API = `http://127.0.0.1:${API_PORT}`;
const WEB = `http://127.0.0.1:${WEB_PORT}`;
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'nc-e2e-'));

const procs = [];
function run(name, cmd, args, env) {
  const p = spawn(cmd, args, { cwd: ROOT, env: { ...process.env, ...env }, stdio: ['ignore', 'pipe', 'pipe'] });
  let out = '';
  p.stdout.on('data', (d) => { out += d; });
  p.stderr.on('data', (d) => { out += d; });
  p.on('exit', (code) => { if (code && !p.killed) console.error(`\n${name} exited with ${code}:\n${out.slice(-1500)}`); });
  procs.push(p);
}
const cleanup = () => {
  for (const p of procs) p.kill();
  fs.rmSync(TMP, { recursive: true, force: true });
};
for (const sig of ['SIGINT', 'SIGTERM']) process.on(sig, () => { cleanup(); process.exit(130); });

async function waitFor(url, what, ms = 60000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    try { if ((await fetch(url)).ok) return; } catch { /* not up yet */ }
    await new Promise((r) => setTimeout(r, 300));
  }
  throw new Error(`${what} did not come up at ${url}`);
}
async function api(p, init) {
  const r = await fetch(API + p, init);
  if (!r.ok) throw new Error(`${init?.method ?? 'GET'} ${p} → ${r.status}`);
  return r.json();
}
const expect = (cond, msg) => { if (!cond) throw new Error(msg); };

const results = [];
async function step(name, fn) {
  const t0 = Date.now();
  try {
    await fn();
    results.push(`  ✓ ${name}  (${Date.now() - t0} ms)`);
  } catch (e) {
    results.push(`  ✗ ${name}\n      ${String(e.message).split('\n')[0]}`);
    process.exitCode = 1;
  }
}

let browser;
try {
  const sync = spawnSync('uv', ['sync', '--project', 'server', '--quiet'], { cwd: ROOT, stdio: 'inherit' });
  if (sync.error || sync.status !== 0) throw new Error('uv sync failed. Is uv installed? https://docs.astral.sh/uv/');

  // Both servers are spawned directly (not through uv run / npm) so killing them really stops them.
  run('api', path.join(ROOT, 'server/.venv/bin/python'),
    ['-m', 'uvicorn', 'app.main:create_app', '--factory', '--app-dir', 'server', '--host', '127.0.0.1', '--port', String(API_PORT)],
    { NEUROCODE_DB: path.join(TMP, 'e2e.db') });
  run('web', process.execPath,
    [path.join(ROOT, 'node_modules/vite/bin/vite.js'), '--port', String(WEB_PORT), '--strictPort', '--host', '127.0.0.1'],
    { NC_API_PORT: String(API_PORT) });
  await waitFor(`${API}/health`, 'API');
  await waitFor(WEB, 'web app');

  browser = await chromium.launch({ executablePath: findChrome() });
  const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(String(e.message).slice(0, 200)));
  // An open EventSource keeps the network busy forever, so never wait for networkidle here.
  const open = (route) => page.goto(WEB + route, { waitUntil: 'domcontentloaded' });

  await step('the app finds the local API and says so', async () => {
    await open('/');
    await page.getByText('saved locally').waitFor({ timeout: 20000 });
  });

  await step('approving in the UI is written to the database', async () => {
    const [first] = await api('/approvals?status=pending');
    await open('/permissions');
    await page.getByRole('button', { name: 'Approve', exact: true }).first().click();
    await page.waitForTimeout(500);
    const after = (await api('/approvals')).find((a) => a.ref === first.ref);
    expect(after.status === 'approved', `${first.ref} is "${after.status}" in the database`);
  });

  await step('a change made elsewhere streams into an open tab', async () => {
    await open('/activity');
    await page.getByText('Approved', { exact: true }).first().waitFor({ timeout: 10000 });
    const [next] = await api('/approvals?status=pending');
    await api(`/approvals/${next.ref}/deny`, { method: 'POST' });
    await page.getByText(`${next.ref} · ${next.title}`).first().waitFor({ timeout: 5000 });
  });

  await step('ticking a checklist item persists', async () => {
    const task = await api('/tasks/TASK-492');
    const item = task.checklist.find((c) => !c.done);
    expect(item, 'TASK-492 has no open checklist item to tick');
    await open('/tasks');
    await page.locator('button:has-text("TASK-492")').first().click();
    await page.locator('[data-slot="sheet-content"]').getByRole('checkbox', { name: item.label }).click();
    await page.waitForTimeout(500);
    const again = await api('/tasks/TASK-492');
    expect(again.checklist.find((c) => c.id === item.id).done, 'the item is still open in the database');
  });

  await step('memory search runs on FTS5 and a pin persists', async () => {
    const hits = (await api('/memory?q=TRANS')).filter((f) => f.projectId === 'erp' || f.projectId === 'global');
    expect(hits.length, 'FTS5 found nothing for "TRANS"');
    const top = hits[0];
    await open('/memory');
    await page.getByPlaceholder(/Search memory/).fill('TRANS');
    await page.getByText('FTS5 · ranked').waitFor({ timeout: 5000 });
    await page.getByRole('button', { name: top.pinned ? 'Unpin' : 'Pin', exact: true }).click();
    await page.waitForTimeout(500);
    const after = (await api('/memory?q=TRANS')).find((f) => f.ref === top.ref);
    expect(after.pinned === !top.pinned, `${top.ref} pinned is still ${after.pinned}`);
  });

  if (pageErrors.length) results.push(`  ✗ uncaught errors in the page\n      ${pageErrors.join('\n      ')}`), (process.exitCode = 1);
} catch (e) {
  results.push(`  ✗ setup: ${e.message}`);
  process.exitCode = 1;
} finally {
  await browser?.close();
  cleanup();
}

console.log(`e2e: API + web app + browser, on a throwaway database\n${results.join('\n')}`);
console.log(process.exitCode ? '✗ failed' : '✓ the stack works end to end');
