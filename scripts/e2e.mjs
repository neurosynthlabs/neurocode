#!/usr/bin/env node
// NeuroCode full-stack test. It runs the real API on a throwaway database and the real web app in dev
// mode, proxying to it, then drives a headless browser through first-run setup and proves that what
// people do is written to the database, that what the server records streams into an open tab, that
// the AI features answer with no key, and that roles decide who may change what.
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
const OWNER = { workspace: 'E2E Works', name: 'Asha Rao', email: 'asha@e2e.test', password: 'e2e-owner-password' };
const VIEWER = { name: 'Vik Viewer', email: 'vik@e2e.test', password: 'e2e-viewer-password' };
// The test's own calls sign in like a script would: a bearer token, no cookie.
let TOKEN = '';
async function api(p, init = {}) {
  const r = await fetch(API + p, { ...init, headers: { ...init.headers, ...(TOKEN ? { Authorization: `Bearer ${TOKEN}` } : {}) } });
  if (!r.ok) throw new Error(`${init.method ?? 'GET'} ${p} → ${r.status}`);
  return r.json();
}
const post = (p, body) => api(p, {
  method: 'POST',
  ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
});
async function tokenFor(email, password) {
  const r = await fetch(`${API}/auth/login`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email, password }),
  });
  if (!r.ok) throw new Error(`signing in ${email} → ${r.status}`);
  return /nc_session=([^;]+)/.exec(r.headers.get('set-cookie') ?? '')?.[1] ?? '';
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
    // No server/.env and no key from this shell: the run must not depend on, or reach, a real model.
    { NEUROCODE_DB: path.join(TMP, 'e2e.db'), NEUROCODE_COMPILER: 'rules', NEUROCODE_ENV_FILE: '', DEEPSEEK_API_KEY: '' });
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

  await step('first run: the setup wizard makes the Owner and opens the workspace', async () => {
    await open('/');
    await page.getByText('Welcome to NeuroCode').waitFor({ timeout: 20000 });
    await page.getByLabel('Workspace name').fill(OWNER.workspace);
    await page.getByRole('button', { name: /Continue/ }).click();
    await page.getByLabel('Your name').fill(OWNER.name);
    await page.getByLabel('Email', { exact: true }).fill(OWNER.email);
    await page.getByLabel('Password', { exact: true }).fill(OWNER.password);
    await page.getByLabel('Password, again').fill(OWNER.password);
    await page.getByRole('button', { name: /Create workspace/ }).click();
    await page.getByRole('button', { name: 'Skip for now' }).click();
    await page.getByText('saved locally').waitFor({ timeout: 20000 });
    TOKEN = await tokenFor(OWNER.email, OWNER.password);
    const me = await api('/auth/me');
    expect(me.user.roles.includes('owner') && me.workspace.name === OWNER.workspace, `signed in with roles ${me.user.roles}`);
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

  await step('a requirement compiles into a stored plan and its task', async () => {
    await open('/');
    await page.getByText('saved locally').waitFor({ timeout: 20000 });
    await page.getByLabel('Requirement').fill('Invoice mein tax galat aa raha hai, CGST/SGST interstate pe reverse. Fix karo.');
    await page.getByRole('button', { name: /Compile Plan/ }).click();
    await page.waitForURL('**/plans', { timeout: 15000 });
    const [newest] = await api('/plans');
    expect(newest.compiler?.provider === 'rules', `the newest plan came from ${newest.compiler?.provider}`);
    await page.getByText(newest.ref).first().waitFor({ timeout: 5000 });
    const task = await api(`/tasks/${newest.taskRef}`);
    expect(task.status === 'planning', `its task is ${task.status}`);
  });

  await step('⌘K finds the plan compiled a moment ago and opens it', async () => {
    const [newest] = await api('/plans');
    await page.keyboard.press('Control+k');
    await page.locator('[cmdk-input]').fill(newest.ref);
    await page.locator('[cmdk-item]', { hasText: newest.ref }).first().click();
    await page.waitForURL(`**/plans?ref=${newest.ref}`, { timeout: 5000 });
  });

  await step('answering and deferring the questions lets the plan dispatch', async () => {
    const [plan] = await api('/plans');
    await page.getByRole('button', { name: 'Answer', exact: true }).first().click();
    await page.getByPlaceholder(/business rule/).fill('Round at invoice level.');
    await page.getByRole('button', { name: /Save answer/ }).click();
    await page.waitForTimeout(400);
    for (let n = 0; n < 8 && (await page.getByRole('button', { name: 'Defer', exact: true }).count()); n++) {
      await page.getByRole('button', { name: 'Defer', exact: true }).first().click();
      await page.waitForTimeout(300);
    }
    await page.getByRole('button', { name: /Dispatch plan/ }).click();
    await page.waitForURL('**/tasks', { timeout: 5000 });
    expect((await api(`/tasks/${plan.taskRef}`)).status === 'in_progress', 'the task did not start');
    expect((await api('/memory?q=invoice')).some((f) => f.body === 'Round at invoice level.'), 'the answer is not in memory');
  });

  await step('onboarding a local folder measures it for real', async () => {
    await open('/projects');
    await page.getByRole('button', { name: /New project/ }).click();
    const dlg = page.locator('[data-slot="dialog-content"]');
    await dlg.getByRole('button', { name: 'Local path', exact: true }).click();
    await dlg.locator('input').first().fill(path.join(ROOT, 'server'));
    for (let n = 0; n < 3; n++) await dlg.getByRole('button', { name: /^Next/ }).click();
    await dlg.getByRole('button', { name: /Start onboarding/ }).click();
    let p;
    for (const t0 = Date.now(); Date.now() - t0 < 15000; await new Promise((r) => setTimeout(r, 300))) {
      p = (await api('/projects')).find((x) => x.id === 'server');
      if (p?.status === 'active') break;
    }
    expect(p?.status === 'active', `the project is ${p?.status ?? 'missing'}`);
    expect(p.stack.includes('Python') && p.files > 3, `measured ${p.files} files, stack ${p.stack}`);
  });

  await step('registering an MCP server persists it, untrusted', async () => {
    await open('/mcp');
    await page.getByRole('button', { name: /Add server/ }).click();
    const dlg = page.locator('[data-slot="dialog-content"]');
    await dlg.getByRole('button', { name: /^Next/ }).click();
    await dlg.locator('input').first().fill('npx -y @acme/server-ledger');
    await dlg.getByRole('button', { name: /^Next/ }).click();
    await dlg.getByRole('button', { name: /^Next/ }).click();
    await dlg.getByRole('button', { name: /Register server/ }).click();
    await page.waitForTimeout(500);
    expect((await api('/mcp/servers')).some((s) => s.id === 'ledger' && s.untrusted), 'the server is not in the database');
  });

  await step('keeping one side of a memory conflict archives the other', async () => {
    const [c] = await api('/memory/conflicts');
    await open('/memory');
    await page.getByRole('button', { name: /^Conflicts \(/ }).click();
    await page.getByRole('button', { name: 'Keep A', exact: true }).first().click();
    await page.waitForTimeout(500);
    expect(!(await api('/memory/conflicts')).some((x) => x.id === c.id), 'the conflict is still open');
  });

  await step('a switched-off skill stays off after a reload', async () => {
    await open('/skills');
    await page.getByText('saved locally').waitFor({ timeout: 20000 });
    const before = await page.getByRole('switch').first().getAttribute('aria-checked');
    await page.getByRole('switch').first().click();
    await page.waitForTimeout(400);
    await open('/skills');
    await page.getByText('saved locally').waitFor({ timeout: 20000 });
    const after = await page.getByRole('switch').first().getAttribute('aria-checked');
    expect(after !== before, `the switch is back to ${after} after a reload`);
    expect((await api('/prefs')).some((p) => p.id === 'skills.enabled'), 'skills.enabled is not in the database');
  });

  await step('asking memory answers with no key, and cites its facts', async () => {
    await open('/');
    await page.getByText('saved locally').waitFor({ timeout: 20000 });
    await page.getByRole('radio', { name: 'Ask' }).click();
    await page.getByLabel('Requirement').fill('How is CGST and SGST split on interstate invoices?');
    await page.getByRole('button', { name: 'Ask memory' }).click();
    await page.getByText(/Memory search, no model/).waitFor({ timeout: 10000 });
    expect(await page.locator('a[href^="/memory?ref="]').count() > 0, 'the answer cites no fact');
  });

  await step('a brainstorm becomes a stored brief', async () => {
    await page.getByRole('radio', { name: 'Brainstorm' }).click();
    await page.getByLabel('Requirement').fill('A vendor portal where suppliers raise invoice disputes themselves.');
    await page.getByRole('button', { name: 'Brainstorm', exact: true }).click();
    await page.waitForURL('**/brainstorm?ref=IDEA-1', { timeout: 10000 });
    await page.getByText('Plan the MVP').waitFor({ timeout: 5000 });
    const [doc] = await api('/ai/brainstorms');
    expect(doc?.ref === 'IDEA-1' && doc.compiler.provider === 'rules', `stored ${doc?.ref} from ${doc?.compiler?.provider}`);
  });

  await step('pasted notes become memory facts', async () => {
    await open('/memory');
    await page.getByRole('button', { name: /Add from text/ }).click();
    const dlg = page.locator('[data-slot="dialog-content"]');
    await dlg.getByLabel('Text to read').fill('Billing review. Credit notes must always reference the original invoice number. Lunch was fine.');
    await dlg.getByRole('button', { name: /Find facts/ }).click();
    await dlg.getByRole('button', { name: 'Add 1 fact' }).click();
    await page.waitForTimeout(500);
    const hits = await api('/memory?q=credit%20notes%20original');
    expect(hits.some((f) => f.body.startsWith('Credit notes must always')), 'the fact is not in memory');
  });

  await step('every AI call so far is in the usage ledger', async () => {
    const u = await api('/usage');
    const features = new Set(u.byFeature.map((f) => f.feature));
    expect(u.totals.calls >= 4, `the ledger holds ${u.totals.calls} calls`);
    expect(['compile', 'ask', 'brainstorm', 'extract'].every((f) => features.has(f)), `features in the ledger: ${[...features]}`);
  });

  await step('the onboarded project is indexed: search a symbol, open its file and its blast radius', async () => {
    let s;
    for (const t0 = Date.now(); Date.now() - t0 < 20000; await new Promise((r) => setTimeout(r, 300))) {
      s = await api('/projects/server/code');
      if (s.indexed) break;
    }
    expect(s?.indexed && s.run.parsers.Python === 'python-ast' && s.run.symbols > 50, `index: ${JSON.stringify(s?.run ?? s)}`);
    await open('/');
    await page.getByText('saved locally').waitFor({ timeout: 20000 });
    await page.locator('header [data-slot="popover-trigger"]').first().click();
    await page.locator('[data-slot="popover-content"]').getByRole('button', { name: /^Server/ }).click();
    await page.getByRole('link', { name: 'Code Intelligence', exact: true }).click();
    await page.getByText('Python · python-ast').waitFor({ timeout: 10000 });
    await page.getByLabel('Search the code').fill('create_app');
    await page.getByRole('button', { name: /create_app/ }).first().click();
    await page.getByText('If main.py changes…').waitFor({ timeout: 10000 });
    await page.getByRole('button', { name: /Show in Architecture/ }).click();
    await page.getByText('Module graph').waitFor({ timeout: 10000 });
    await page.getByText('If main.py changes…').waitFor({ timeout: 10000 });
    await page.getByRole('button', { name: 'app', exact: true }).first().click();
    await page.getByText('If app changes…').waitFor({ timeout: 10000 });
  });

  await step('a database backup is made from Admin → Database', async () => {
    await open('/admin/database');
    await page.getByRole('button', { name: /Back up now/ }).click();
    await page.getByText(/neurocode-\d{8}-\d{6}-manual\.db/).first().waitFor({ timeout: 10000 });
    const db = await api('/admin/database');
    expect(db.backups.some((b) => b.name.endsWith('-manual.db')), 'no manual backup is listed');
    expect(db.migrations.length === 6, `${db.migrations.length} migrations applied`);
  });

  await step('an agent run works in a worktree of its own and stops at your signature', async () => {
    const repo = path.join(TMP, 'tiny');
    fs.mkdirSync(path.join(repo, 'pkg'), { recursive: true });
    fs.writeFileSync(path.join(repo, 'Makefile'), 'test:\n\t@echo "2 passed"\n');
    fs.writeFileSync(path.join(repo, 'pkg', 'core.py'), 'def total(x):\n    return x\n');
    for (const args of [['init', '-q', '-b', 'main'], ['config', 'user.email', 'e2e@test'], ['config', 'user.name', 'E2E'],
      ['add', '-A'], ['-c', 'commit.gpgsign=false', 'commit', '-qm', 'start']]) {
      spawnSync('git', args, { cwd: repo });
    }
    const project = await post('/projects', { source: 'local', repo });
    const plan = await post('/plans/compile', { requirement: 'Round invoice totals in one place.', projectId: project.id });
    for (let i = 0; i < plan.openQuestions.length; i++) await post(`/plans/${plan.ref}/questions/0`, { defer: true });
    const dispatched = await post(`/plans/${plan.ref}/dispatch`);
    expect(dispatched.runRef, 'the onboarded repository started no run');

    let run;
    for (const t0 = Date.now(); Date.now() - t0 < 20000; await new Promise((r) => setTimeout(r, 300))) {
      run = await api(`/runs/${dispatched.runRef}`);
      if (run.status === 'waiting') break;
    }
    expect(run?.status === 'waiting', `the run is ${run?.status}`);   // the first test run needs a person
    expect(fs.existsSync(path.join(run.worktree, 'Makefile')), 'the worktree has no checkout');
    if (run.role === 'integration') {   // several agents: a worktree and a branch each, merged for you
      const agents = await Promise.all(run.children.map((r) => api(`/runs/${r}`)));
      expect(agents.length >= 2 && new Set(agents.map((a) => a.worktree)).size === agents.length,
        `${agents.length} agents shared ${new Set(agents.map((a) => a.worktree)).size} worktrees`);
      expect(agents.every((a) => fs.existsSync(a.worktree)), 'an agent has no worktree');
    }
    expect(!spawnSync('git', ['status', '--porcelain'], { cwd: repo, encoding: 'utf8' }).stdout.trim(), 'the working tree was touched');

    await open(`/runs?ref=${run.ref}`);
    await page.getByText(`Waiting for you · ${run.waitingOn}`).first().waitFor({ timeout: 20000 });
    await page.getByText('worktree ready').first().waitFor({ timeout: 10000 });

    await post(`/approvals/${run.waitingOn}/approve`);
    let done;
    for (const t0 = Date.now(); Date.now() - t0 < 30000; await new Promise((r) => setTimeout(r, 300))) {
      done = await api(`/runs/${run.ref}`);
      if (['done', 'failed', 'cancelled'].includes(done.status)) break;
    }
    expect(done.status === 'done' && done.tests.status === 'passed', `run ${done?.status}, tests ${done?.tests?.status}`);
    await open(`/runs?ref=${run.ref}`);
    await page.getByText('2 passed').first().waitFor({ timeout: 15000 });
    await page.getByRole('button', { name: /Discard worktree/ }).click();
    await page.waitForTimeout(600);
    expect(!fs.existsSync(run.worktree), 'the worktree is still there after discarding it');
  });

  await step('a model key is saved masked, never logged, and can be removed', async () => {
    await open('/admin/ai');
    await page.getByLabel('API key').fill('sk-e2e-test-0000abcd');
    await page.getByRole('button', { name: 'Save key' }).click();
    await page.getByText('••••abcd').waitFor({ timeout: 5000 });
    const cfg = await api('/admin/ai');
    expect(cfg.deepseek.hasKey && cfg.deepseek.keyMask === '••••abcd', `the key reads ${cfg.deepseek.keyMask}`);
    expect(!JSON.stringify(await api('/admin/audit')).includes('0000abcd'), 'the key leaked into the audit log');
    await page.getByRole('button', { name: 'Remove key' }).click();
    await page.getByText('Not set').waitFor({ timeout: 5000 });
  });

  await step('an admin adds a Viewer, and the API refuses the Viewer an approval', async () => {
    await open('/admin/users');
    await page.getByRole('button', { name: /Add person/ }).first().click();
    const dlg = page.locator('[data-slot="dialog-content"]');
    await dlg.getByLabel('Name', { exact: true }).fill(VIEWER.name);
    await dlg.getByLabel('Email', { exact: true }).fill(VIEWER.email);
    await dlg.getByLabel('Temporary password').fill(VIEWER.password);
    await dlg.getByRole('checkbox', { name: /^Engineer/ }).uncheck();
    await dlg.getByRole('checkbox', { name: /^Viewer/ }).check();
    await dlg.getByRole('button', { name: 'Add person' }).click();
    await dlg.getByText(`${VIEWER.name} can sign in now`).waitFor({ timeout: 5000 });
    await dlg.getByRole('button', { name: 'Done' }).click();
    const viewer = await tokenFor(VIEWER.email, VIEWER.password);
    const [a] = await api('/approvals?status=pending');
    const r = await fetch(`${API}/approvals/${a.ref}/approve`, { method: 'POST', headers: { Authorization: `Bearer ${viewer}` } });
    expect(r.status === 403, `the Viewer's approval got ${r.status}`);
  });

  await step('signed in as the Viewer, Admin is hidden and changes are refused', async () => {
    await page.getByRole('button', { name: /^Account:/ }).click();
    await page.getByRole('button', { name: 'Sign out' }).click();
    await page.getByLabel('Email').fill(VIEWER.email);
    await page.getByLabel('Password').fill(VIEWER.password);
    await page.getByRole('button', { name: 'Sign in', exact: true }).click();
    await page.getByText('saved locally').waitFor({ timeout: 20000 });
    expect(await page.getByRole('link', { name: 'People', exact: true }).count() === 0, 'a Viewer sees Admin → People');
    await open('/permissions');
    await page.getByText('saved locally').waitFor({ timeout: 20000 });
    await page.getByRole('button', { name: 'Approve', exact: true }).first().click();
    await page.getByText('Your role cannot do that').waitFor({ timeout: 5000 });
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
