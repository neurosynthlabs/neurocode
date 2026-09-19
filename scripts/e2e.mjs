#!/usr/bin/env node
// NeuroCode full-stack test. The real API on a throwaway database, the real web app in dev mode proxying
// to it, and a stub model on a lane (scripts/stub-model.mjs), driven by a headless browser from first-run
// setup onwards.
//
// The workspace starts empty, as a new one really does, and everything the run looks at it makes itself:
// a repository it onboards, a plan it compiles, a run in a worktree, facts it writes. Nothing is read off a
// sample, so a step can only pass because the thing it checks actually happened. It proves that what people
// do is written to the database, that what the server records streams into an open tab, that the model
// paths go through the gateway and its ledger, and that roles decide who may change what.
//
//   npm run e2e          needs uv, Postgres and the API's dependencies (uv sync --project server)
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from 'playwright-core';
import { findChrome } from './chrome.mjs';
import { ROOT, startStack } from './stack.mjs';
import { settle } from './browser.mjs';

const API_PORT = Number(process.env.E2E_API_PORT ?? 8798);
const WEB_PORT = Number(process.env.E2E_WEB_PORT ?? 5198);

const OWNER = { workspace: 'E2E Works', name: 'Asha Rao', email: 'asha@e2e.test', password: 'e2e-owner-password' };
const VIEWER = { name: 'Vik Viewer', email: 'vik@e2e.test', password: 'e2e-viewer-password' };

const results = [];
// E2E_SHOTS=/some/dir saves what the browser showed when a step failed.
const SHOTS = process.env.E2E_SHOTS;
let shown = null;
async function step(name, fn) {
  const t0 = Date.now();
  try {
    await fn();
    results.push(`  ✓ ${name}  (${Date.now() - t0} ms)`);
  } catch (e) {
    results.push(`  ✗ ${name}\n      ${String(e.message).split('\n').slice(0, 3).join(' · ')}`);
    process.exitCode = 1;
    if (SHOTS && shown) {
      fs.mkdirSync(SHOTS, { recursive: true });
      await shown.screenshot({ path: path.join(SHOTS, `${name.replace(/\W+/g, '-').slice(0, 60)}.png`) }).catch(() => {});
    }
  }
}
const expect = (cond, msg) => { if (!cond) throw new Error(msg); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
/** Polls until `get` returns something truthy, or fails with what it last saw. */
async function until(get, what, ms = 20000) {
  let last;
  for (const t0 = Date.now(); Date.now() - t0 < ms; await sleep(300)) {
    last = await get();
    if (last) return last;
  }
  throw new Error(`${what} did not happen within ${ms / 1000} s`);
}

let stack;
let browser;
try {
  stack = await startStack({ name: 'e2e', apiPort: API_PORT, webPort: WEB_PORT, web: 'dev' });
  for (const sig of ['SIGINT', 'SIGTERM']) process.on(sig, () => { void stack.stop().finally(() => process.exit(130)); });
  const { api: API, web: WEB } = stack;

  // The test's own calls sign in like a script would: a bearer token, no cookie.
  let TOKEN = '';
  const call = async (p, init = {}, token = TOKEN) => {
    const r = await fetch(API + p, { ...init, headers: { ...init.headers, ...(token ? { Authorization: `Bearer ${token}` } : {}) } });
    if (!r.ok) throw new Error(`${init.method ?? 'GET'} ${p} → ${r.status} ${(await r.text()).slice(0, 160)}`);
    return r.json();
  };
  const post = (p, body, headers = {}) => call(p, {
    method: 'POST',
    headers: { ...(body === undefined ? {} : { 'Content-Type': 'application/json' }), ...headers },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const tokenFor = async (email, password) => {
    const r = await fetch(`${API}/auth/login`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email, password }),
    });
    if (!r.ok) throw new Error(`signing in ${email} → ${r.status}`);
    return /nc_session=([^;]+)/.exec(r.headers.get('set-cookie') ?? '')?.[1] ?? '';
  };

  browser = await chromium.launch({ executablePath: findChrome() });
  const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
  shown = page;
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(String(e.message).slice(0, 200)));
  const open = async (route) => { await page.goto(WEB + route, { waitUntil: 'domcontentloaded' }); await settle(page); };

  // A repository of its own: two modules, one importing the other, and a test command that passes.
  const repo = path.join(stack.tmp, 'ledger');
  fs.mkdirSync(path.join(repo, 'pkg'), { recursive: true });
  fs.writeFileSync(path.join(repo, 'Makefile'), 'test:\n\t@echo "2 passed"\n');
  fs.writeFileSync(path.join(repo, 'pkg', '__init__.py'), '');
  fs.writeFileSync(path.join(repo, 'pkg', 'core.py'), 'def total(rows):\n    return sum(rows)\n');
  fs.writeFileSync(path.join(repo, 'pkg', 'api.py'), 'from pkg.core import total\n\n\ndef invoice_total(lines):\n    return total(lines)\n');
  for (const args of [['init', '-q', '-b', 'main'], ['config', 'user.email', 'e2e@test'], ['config', 'user.name', 'E2E'],
    ['add', '-A'], ['-c', 'commit.gpgsign=false', 'commit', '-qm', 'start']]) {
    spawnSync('git', args, { cwd: repo });
  }
  let project;
  let plan;
  let run;

  await step('first run: the setup wizard makes the Owner and opens an empty workspace', async () => {
    await page.goto(WEB + '/', { waitUntil: 'domcontentloaded' });
    await page.getByText('Welcome to NeuroCode').waitFor({ timeout: 30000 });
    await page.getByLabel('Workspace name').fill(OWNER.workspace);
    await page.getByRole('button', { name: /Continue/ }).click();
    await page.getByLabel('Your name').fill(OWNER.name);
    await page.getByLabel('Email', { exact: true }).fill(OWNER.email);
    await page.getByLabel('Password', { exact: true }).fill(OWNER.password);
    await page.getByLabel('Password, again').fill(OWNER.password);
    await page.getByRole('button', { name: /Create workspace/ }).click();
    await page.getByRole('button', { name: 'Skip for now' }).click();
    await page.getByText(/Onboard a repository/).first().waitFor({ timeout: 20000 });
    TOKEN = await tokenFor(OWNER.email, OWNER.password);
    const me = await call('/auth/me');
    expect(me.user.roles.includes('owner') && me.workspace.name === OWNER.workspace, `signed in with roles ${me.user.roles}`);
  });

  await step('a new workspace holds the catalogue and nothing else', async () => {
    const [projects, tasks, memory, activity, catalogue] = await Promise.all(
      ['/projects', '/tasks', '/memory', '/activity', '/auth/catalogue'].map((p) => call(p)));
    expect(!projects.length && !tasks.length && !memory.length, `${projects.length} projects, ${tasks.length} tasks, ${memory.length} facts`);
    expect(!activity.some((a) => a.projectId), 'the log holds work nobody did');
    expect(catalogue.roles.some((r) => r.id === 'owner') && catalogue.agents.length >= 10 && catalogue.permissions.some((p) => p.id === 'plans:compile'),
      `catalogue: ${catalogue.roles.length} roles, ${catalogue.agents.length} agents, ${catalogue.permissions.length} permissions`);
  });

  await step('onboarding a local folder measures it for real', async () => {
    await open('/projects');
    await page.getByRole('button', { name: /New project/ }).first().click();
    const dlg = page.locator('[data-slot="dialog-content"]');
    await dlg.getByRole('button', { name: /Open a folder on this machine/ }).click();
    await dlg.getByLabel('Absolute path').fill(repo);
    // Through however many steps the wizard has, to the one that starts it.
    for (let n = 0; n < 8 && !(await dlg.getByRole('button', { name: /Start onboarding/ }).count()); n++) {
      await dlg.getByRole('button', { name: /^Next/ }).click();
    }
    await dlg.getByRole('button', { name: /Start onboarding/ }).click();
    project = await until(async () => (await call('/projects')).find((x) => x.status === 'active'), 'onboarding');
    expect(project.stack.includes('Python') && project.files >= 3, `measured ${project.files} files, stack ${project.stack}`);
  });

  await step('the onboarded project is indexed: search a symbol, open its file and its blast radius', async () => {
    const s = await until(async () => { const x = await call(`/projects/${project.id}/code`); return x.indexed && x; }, 'indexing');
    expect(s.run.parsers.Python === 'python-ast' && s.run.symbols >= 2, `index: ${JSON.stringify(s.run)}`);
    await open('/code');
    await page.getByLabel('Search the code').fill('total');
    await page.getByRole('button', { name: /total/ }).first().click();
    await page.getByText('If core.py changes…').waitFor({ timeout: 10000 });
    await page.getByRole('button', { name: /Show in Architecture/ }).click();
    await page.getByText('Module graph').first().waitFor({ timeout: 10000 });
  });

  await step('a requirement compiles through the model into a stored plan and its task', async () => {
    await open('/');
    await page.getByLabel('Requirement').fill('Round invoice totals in pkg/core.py at the invoice level.');
    await page.getByRole('button', { name: /Compile Plan/ }).click();
    await page.waitForURL('**/plans**', { timeout: 20000 });
    [plan] = await call('/plans');
    expect(plan.compiler && plan.compiler.provider !== 'rules', `the newest plan came from ${plan.compiler?.provider}`);
    expect(stack.model.seen.includes('compile'), 'the model was never asked');
    expect(plan.confidence === 72, `confidence ${plan.confidence} is not the model's`);
    await page.getByText(plan.ref).first().waitFor({ timeout: 5000 });
    const task = await call(`/tasks/${plan.taskRef}`);
    expect(task.status === 'planning' && task.checklist.length > 0, `its task is ${task.status} with ${task.checklist.length} items`);
  });

  await step('⌘K finds the plan compiled a moment ago and opens it', async () => {
    await page.keyboard.press('Control+k');
    await page.locator('[cmdk-input]').fill(plan.ref);
    await page.locator('[cmdk-item]', { hasText: plan.ref }).first().click();
    await page.waitForURL(`**/plans?ref=${plan.ref}`, { timeout: 5000 });
  });

  await step('answering and deferring the questions lets the plan dispatch a run', async () => {
    await settle(page);
    await page.getByRole('button', { name: 'Answer', exact: true }).first().click();
    await page.getByPlaceholder(/business rule/).fill('Round at invoice level.');
    await page.getByRole('button', { name: /Save answer/ }).click();
    await sleep(400);
    for (let n = 0; n < 8 && (await page.getByRole('button', { name: 'Defer', exact: true }).count()); n++) {
      await page.getByRole('button', { name: 'Defer', exact: true }).first().click();
      await sleep(300);
    }
    await page.getByRole('button', { name: /Dispatch plan/ }).click();
    await page.waitForURL('**/tasks', { timeout: 10000 });
    expect((await call(`/tasks/${plan.taskRef}`)).status === 'in_progress', 'the task did not start');
    expect((await call('/memory?q=invoice')).some((f) => f.body === 'Round at invoice level.'), 'the answer is not in memory');
    plan = await call(`/plans/${plan.ref}`);
    const runs = await call('/runs');
    run = runs.find((r) => r.planRef === plan.ref && r.role !== 'agent') ?? runs[0];
    expect(run, 'dispatching started no run');
  });

  await step('ticking a checklist item persists', async () => {
    const task = await call(`/tasks/${plan.taskRef}`);
    const item = task.checklist.find((c) => !c.done);
    await open('/tasks');
    await page.locator(`button:has-text("${plan.taskRef}")`).first().click();
    await page.locator('[data-slot="sheet-content"]').getByRole('checkbox', { name: item.label }).click();
    await until(async () => (await call(`/tasks/${plan.taskRef}`)).checklist.find((c) => c.id === item.id).done, 'the tick reaching the database', 5000);
  });

  await step('the run works in a worktree of its own and stops at the first test run', async () => {
    run = await until(async () => { const r = await call(`/runs/${run.ref}`); return r.status === 'waiting' && r; }, 'the run reaching its first test');
    expect(fs.existsSync(path.join(run.worktree, 'Makefile')), 'the worktree has no checkout');
    expect(!spawnSync('git', ['status', '--porcelain'], { cwd: repo, encoding: 'utf8' }).stdout.trim(), 'the working tree was touched');
    expect(stack.model.seen.includes('edit'), 'no agent step asked the model for a change');
  });

  await step('approving in the inbox is written to the database, and the run streams on in an open tab', async () => {
    await open('/permissions');
    // A test gate's yes says what it does: the answer is kept for the project.
    await page.getByRole('button', { name: /^Allow — remembered for this project/ }).first().click();
    await until(async () => (await call('/approvals')).find((a) => a.ref === run.waitingOn)?.status === 'approved', 'the approval reaching the database', 5000);
    await open(`/runs?ref=${run.ref}`);
    await page.getByText('passed').first().waitFor({ timeout: 30000 });
    run = await until(async () => { const r = await call(`/runs/${run.ref}`); return r.review?.by && !['queued', 'running'].includes(r.status) && r; }, 'the run being tested and reviewed', 30000);
    expect(run.tests.status === 'passed', `tests ${run.tests.status}, run ${run.status}`);
  });

  await step('the test command a person allowed is a standing rule, with who decided it', async () => {
    const [rule] = await call('/permissions/rules');
    expect(rule?.answer === 'allowed' && rule.decidedBy === OWNER.name, `rule: ${JSON.stringify(rule)}`);
    await open('/permissions');
    await page.getByRole('button', { name: /Rules/ }).first().click();
    await page.getByText(project.name).first().waitFor({ timeout: 5000 });
  });

  await step('sending a run back from Review starts a new run carrying the notes', async () => {
    await open(`/review?ref=${run.ref}`);
    await page.getByRole('button', { name: /Request changes/ }).first().click();
    await page.getByLabel('What should change').fill('Keep the old total() for callers outside pkg.');
    await page.getByRole('button', { name: /Send back/ }).click();
    const old = await until(async () => { const r = await call(`/runs/${run.ref}`); return r.review?.reworkedAs && r; }, 'the run being sent back', 10000);
    expect(old.status === 'cancelled', `the old run is ${old.status}`);
    const next = await call(`/runs/${old.review.reworkedAs}`);
    const lines = next.logs.map((l) => l.line).join('\n');
    expect(lines.includes('Keep the old total()'), 'the new run was not told what to change');
    // Its tests were allowed once already, so it runs straight through to the reviewer and your signature.
    run = await until(async () => { const r = await call(`/runs/${next.ref}`); return r.status === 'waiting' && r.review?.by && r; }, 'the new run reaching your signature', 30000);
    await open(`/review?ref=${run.ref}`);
    await page.getByRole('button', { name: 'Accept', exact: true }).click();
    run = await until(async () => { const r = await call(`/runs/${run.ref}`); return r.status === 'done' && r; }, 'the signed run finishing', 10000);
    await open(`/runs?ref=${run.ref}`);
    await page.getByRole('button', { name: /Discard worktree/ }).click();
    await until(async () => !fs.existsSync(run.worktree), 'the worktree going away', 5000);
  });

  await step('the Workbench opens a file of the project, and a save lands only on what was opened', async () => {
    await open(`/workbench?project=${project.id}&path=pkg/core.py`);
    await page.locator('.cm-content', { hasText: 'def total' }).first().waitFor({ timeout: 15000 });
    const file = path.join(repo, 'pkg', 'core.py');
    const opened = await call(`/machine/file?path=${encodeURIComponent(file)}`);
    const text = `${opened.text}\n\ndef grand_total(rows):\n    return total(rows)\n`;
    const saved = await call('/machine/file', { method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: file, text, expectSha1: opened.sha1 }) });
    expect(saved.sha1 !== opened.sha1 && fs.readFileSync(file, 'utf8').includes('grand_total'), 'the save did not reach the disk');
    const stale = await fetch(`${API}/machine/file`, { method: 'PUT',
      headers: { Authorization: `Bearer ${TOKEN}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: file, text: 'overwritten', expectSha1: opened.sha1 }) });
    expect(stale.status === 409 && fs.readFileSync(file, 'utf8').includes('grand_total'), `a stale save got ${stale.status}`);
  });

  await step('a terminal in the Workbench runs a command on this machine', async () => {
    await page.getByRole('tab', { name: 'Terminal', exact: true }).click();
    await page.getByRole('button', { name: 'New terminal' }).first().click();
    await page.locator('.xterm').first().waitFor({ timeout: 10000 });
    await page.locator('.xterm').first().click();
    await sleep(600);
    await page.keyboard.type('echo neurocode-$((20+22))');
    await page.keyboard.press('Enter');
    await page.locator('.xterm-rows', { hasText: 'neurocode-42' }).first().waitFor({ timeout: 10000 });
    const [open] = (await call('/machine/terminals')).filter((t) => t.status !== 'exited');
    expect(open, 'no terminal is listed as open');
    await call(`/machine/terminals/${open.id}`, { method: 'DELETE' });
  });

  await step('a run configuration runs its command to the end', async () => {
    const made = await post(`/projects/${project.id}/run-configs`,
      { name: 'Say it works', kind: 'run', language: 'shell', command: 'echo run-ok', args: [], cwd: '', env: {} });
    const started = await post(`/run-configs/${made.id}/start`);
    const done = await until(async () => { const t = await call(`/machine/terminals/${started.id}`); return t.status === 'exited' && t; },
      'the run finishing', 15000);
    expect(done.exitCode === 0, `the run exited with ${done.exitCode}`);
  });

  await step('a project holds a second source, indexed under its label, and search spans both', async () => {
    const api = path.join(stack.tmp, 'ledger-api');
    fs.mkdirSync(api, { recursive: true });
    fs.writeFileSync(path.join(api, 'billing.py'), 'def charge_customer(amount):\n    return round(amount, 2)\n');
    spawnSync('git', ['init', '-q', '-b', 'main'], { cwd: api });
    await post(`/projects/${project.id}/sources`, { label: 'api', kind: 'local', repo: api, branch: '' });
    await until(async () => (await call(`/projects/${project.id}/sources`)).find((x) => x.label === 'api' && x.status === 'active'),
      'the second source onboarding', 30000);
    const found = await until(async () => {
      const hits = JSON.stringify(await call(`/projects/${project.id}/code/search?q=charge_customer`));
      return hits.includes('api/billing.py') && hits;
    }, 'the second source reaching the index', 20000);
    expect(found, 'search did not reach the second source');
  });

  await step('a check runs the project\'s own TypeScript compiler from the Problems panel, and names the line', async () => {
    // A TypeScript corner of the project, with its own compiler in node_modules/.bin as a real one has.
    fs.mkdirSync(path.join(repo, 'web'), { recursive: true });
    fs.mkdirSync(path.join(repo, 'node_modules', '.bin'), { recursive: true });
    fs.writeFileSync(path.join(repo, 'tsconfig.json'), JSON.stringify({ compilerOptions: { strict: true, noEmit: true }, include: ['web/*.ts'] }));
    fs.writeFileSync(path.join(repo, 'web', 'total.ts'), "export const total: number = 'not a number';\n");
    fs.symlinkSync(path.join(ROOT, 'node_modules', 'typescript', 'bin', 'tsc'), path.join(repo, 'node_modules', '.bin', 'tsc'));
    await open(`/workbench?project=${project.id}`);
    await page.getByRole('tab', { name: 'Problems', exact: true }).click();
    await page.getByRole('button', { name: 'Check', exact: true }).first().click();
    await page.getByText(/not assignable to type 'number'/).first().waitFor({ timeout: 60000 });
    const [last] = await call(`/diagnostics/checks?projectId=${project.id}`).then((r) => r.items ?? r);
    const found = (await call(`/diagnostics/checks/${last.id}`)).problems.find((x) => x.file.endsWith('web/total.ts'));
    expect(found?.line === 1 && found.severity === 'error' && found.tool === 'tsc', `problem: ${JSON.stringify(found)}`);
  });

  await step('a CSV opens in the Workbench as data, measured by the server, and its SQL box reads nothing else', async () => {
    fs.mkdirSync(path.join(repo, 'data'), { recursive: true });
    const csv = path.join(repo, 'data', 'sales.csv');
    fs.writeFileSync(csv, 'region,amount\nnorth,120\nsouth,80\nnorth,40\nwest,\n');
    await open(`/workbench?project=${project.id}&path=data/sales.csv`);
    await page.getByText('north').first().waitFor({ timeout: 15000 });
    const stats = await call(`/data/stats?path=${encodeURIComponent(csv)}`);
    const amount = stats.columns.find((c) => c.name === 'amount');
    expect(stats.rows === 4 && amount.nulls === 1 && Number(amount.max) === 120, `stats: ${JSON.stringify(stats).slice(0, 200)}`);
    const sum = await post('/data/query', { path: csv, sql: "SELECT sum(amount) AS total FROM data WHERE region = 'north'" });
    expect(Number(sum.rows[0][0]) === 160, `sum: ${JSON.stringify(sum.rows)}`);
    const sneaky = await fetch(`${API}/data/query`, { method: 'POST', headers: { Authorization: `Bearer ${TOKEN}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: csv, sql: "SELECT * FROM read_csv('/etc/passwd')" }) });
    expect(sneaky.status === 403, `a query reading another file got ${sneaky.status}`);
  });

  await step('a notebook in the project runs its cell through a real kernel', async () => {
    const nb = path.join(repo, 'explore.ipynb');
    fs.writeFileSync(nb, JSON.stringify({
      cells: [{ cell_type: 'code', execution_count: null, id: 'c1', metadata: {}, outputs: [], source: ['print(6 * 7)'] }],
      metadata: { kernelspec: { name: 'python3', display_name: 'Python 3', language: 'python' }, language_info: { name: 'python' } },
      nbformat: 4, nbformat_minor: 5,
    }));
    // The kernel runs in the project's own environment, never the API's: give the project one that has ipykernel.
    fs.symlinkSync(path.join(ROOT, 'server', '.venv'), path.join(repo, '.venv'));
    await open(`/workbench?project=${project.id}&path=explore.ipynb`);
    await page.getByRole('button', { name: /Run all/ }).first().click();
    await page.getByText('42', { exact: true }).first().waitFor({ timeout: 60000 });
  });

  await step('pasted notes become memory facts', async () => {
    await open('/memory');
    await page.getByRole('button', { name: /Add from text/ }).first().click();
    const dlg = page.locator('[data-slot="dialog-content"]');
    await dlg.getByLabel('Text to read').fill('Billing review. Credit notes must always reference the original invoice number. Lunch was fine.');
    await dlg.getByRole('button', { name: /Find facts/ }).click();
    await dlg.getByRole('button', { name: 'Add 1 fact' }).click();
    await until(async () => (await call('/memory?q=credit%20notes%20original')).some((f) => f.body.startsWith('Credit notes must always')), 'the fact reaching memory', 5000);
  });

  await step('memory search is ranked full text, and a pin persists', async () => {
    const [top] = await call('/memory?q=credit%20notes');
    await open('/memory');
    await page.getByPlaceholder(/Search memory/).fill('credit notes');
    await page.getByText('Full text · ranked').waitFor({ timeout: 5000 });
    await page.getByRole('button', { name: top.pinned ? 'Unpin' : 'Pin', exact: true }).first().click();
    await until(async () => (await call('/memory?q=credit%20notes')).find((f) => f.ref === top.ref).pinned === !top.pinned, 'the pin reaching the database', 5000);
  });

  await step('a contradiction is recorded, and keeping one side archives the other', async () => {
    const [kept] = await call('/memory?q=credit%20notes');
    const [other] = await post('/memory/facts', { facts: [{ title: 'Credit notes stand alone', body: 'Credit notes never reference an invoice.' }] });
    const conflict = await post('/memory/conflicts', { a: kept.ref, b: other.ref, topic: 'What a credit note references', severity: 'high' });
    await open('/memory');
    await page.getByRole('button', { name: /^Conflicts \(/ }).click();
    await page.getByRole('button', { name: 'Keep A', exact: true }).first().click();
    await until(async () => !(await call('/memory/conflicts')).some((x) => x.id === conflict.id), 'the conflict closing', 5000);
    const archived = (await call('/memory?include_archived=true')).find((f) => f.ref === other.ref);
    expect(archived?.archived && archived.archivedAt, `${other.ref} is not archived`);
  });

  await step('asking memory answers through the model, cites its facts and records the recall', async () => {
    await open('/');
    await page.getByRole('radio', { name: 'Ask' }).click();
    await page.getByLabel('Requirement').fill('What must a credit note reference?');
    await page.getByRole('button', { name: 'Ask memory' }).click();
    await page.locator('a[href^="/memory?ref="]').first().waitFor({ timeout: 15000 });
    expect((await call('/memory/hits')).some((h) => h.feature === 'ask'), 'the recall was not recorded');
  });

  await step('a brainstorm becomes a stored brief', async () => {
    await page.getByRole('radio', { name: 'Brainstorm' }).click();
    await page.getByLabel('Requirement').fill('A vendor portal where suppliers raise invoice disputes themselves.');
    await page.getByRole('button', { name: 'Brainstorm', exact: true }).click();
    await page.waitForURL('**/brainstorm?ref=IDEA-1', { timeout: 15000 });
    await page.getByText('Plan the MVP').waitFor({ timeout: 5000 });
    const [doc] = await call('/ai/brainstorms');
    expect(doc?.ref === 'IDEA-1' && doc.compiler.provider !== 'rules', `stored ${doc?.ref} from ${doc?.compiler?.provider}`);
  });

  await step('a session answers through the model and keeps every turn', async () => {
    const session = await post('/sessions', { projectId: project.id });
    await open(`/sessions?ref=${session.ref}`);
    await page.getByPlaceholder(/Ask about/).fill('where are invoice totals added up?');
    await page.getByRole('button', { name: 'Ask', exact: true }).click();
    await page.getByText('The stub model answers without reading anything.').waitFor({ timeout: 20000 });
    const doc = await call(`/sessions/${session.ref}`);
    const turns = doc.messages.filter((m) => m.tool !== 'grounding');
    expect(turns[0]?.text.startsWith('where are invoice totals') && turns.some((m) => m.role !== 'you'), `turns: ${doc.messages.map((m) => m.role)}`);
    const forked = await post(`/sessions/${session.ref}/fork`, { at: turns[turns.length - 1].id });
    const copy = await call(`/sessions/${forked.ref}`);
    expect(copy.messages.some((m) => m.text?.startsWith('where are invoice totals')), 'the fork lost the question');
    const md = await call(`/sessions/${session.ref}/export?format=md`);
    expect(md.text.includes('where are invoice totals') && md.filename.endsWith('.md'), 'the export is missing the conversation');
  });

  await step('a custom agent answers a session asked through it, and may own a plan step', async () => {
    const agent = await post('/agents/custom', { name: 'Ledger Auditor', role: 'Checks money code for rounding',
      prompt: 'Round every money value with round(x, 2).', lane: null, tools: ['read_file', 'search_code', 'edit'],
      maxSteps: 3, mode: 'subagent', projectId: project.id });
    await open('/agents');
    await page.getByRole('button', { name: 'Custom', exact: true }).click();
    await page.getByRole('button', { name: 'Ask Ledger Auditor' }).first().click();
    await page.waitForURL(/\/sessions\?ref=/, { timeout: 10000 });
    await settle(page);
    const ref = new URL(page.url()).searchParams.get('ref');
    expect((await call(`/sessions/${ref}`)).agent === `custom:${agent.id}`, 'the session is not answered by the agent');
    await page.getByPlaceholder(/Ask/).first().fill('is the invoice total rounded?');
    await page.getByRole('button', { name: 'Ask', exact: true }).click();
    await page.getByText('The stub model answers without reading anything.').first().waitFor({ timeout: 20000 });
    const writers = (await call('/workflows/overview')).writers;
    expect(!writers.includes('Ledger Auditor'), 'a project agent was offered to workflows of every project');
  });

  await step('a review of a branch, asked for on demand, is stored with the reviewer\'s verdict', async () => {
    for (const args of [['checkout', '-qb', 'rounding'], ['-c', 'commit.gpgsign=false', 'commit', '-qam', 'Round the totals'],
      ['checkout', '-q', 'main']]) {
      if (args[0] === '-c') fs.appendFileSync(path.join(repo, 'pkg', 'api.py'), '\n\ndef rounded(lines):\n    return round(total(lines), 2)\n');
      spawnSync('git', args, { cwd: repo });
    }
    const asked = await post(`/projects/${project.id}/review`, { target: 'branch', base: 'main', head: 'rounding' });
    const done = await until(async () => { const r = await call(`/reviews/${asked.ref}`); return r.status !== 'running' && r; },
      'the review finishing', 30000);
    expect(done.status === 'done' && done.stats.files >= 1, `review ${done.status}: ${JSON.stringify(done.stats)}`);
    expect(done.verdict.startsWith('The change is small'), `verdict: ${done.verdict}`);
    const listed = await call(`/projects/${project.id}/reviews?limit=50&offset=0`);
    expect(listed.reviews.some((x) => x.ref === done.ref), 'the review is not in the project\'s list');
    await open('/review');
    await page.getByRole('button', { name: 'On demand', exact: true }).click();
    await page.getByText(done.verdict).first().waitFor({ timeout: 10000 });
  });

  await step('a routine fires a requirement on Run now, and the fire is kept with its plan', async () => {
    const routine = await post('/schedules', { name: 'Nightly rounding check', projectId: project.id,
      requirement: 'Check rounding in pkg/api.py', cadence: '0 2 * * *', enabled: true });
    expect(routine.nextAt && routine.cadenceLabel, `routine: ${JSON.stringify(routine)}`);
    await open('/routines');
    await page.getByText('Nightly rounding check').first().click();
    await page.getByRole('button', { name: /Run now/ }).first().click();
    const fire = await until(async () => {
      const [last] = (await call(`/schedules/${routine.id}/fires`)).items;
      return last && last.outcome !== 'firing' && last;
    }, 'the fire ending', 30000);
    expect(fire.trigger === 'manual' && fire.planRef, `fire: ${JSON.stringify(fire)}`);
    const made = (await call('/plans')).find((x) => x.ref === fire.planRef);
    expect(made?.rawRequirement.includes('rounding'), `the fire's plan ${fire.planRef} is not the routine's`);
  });

  await step('the inbox says what needs you and what finished, and marking it seen moves the line', async () => {
    const inbox = await call('/inbox');
    expect(inbox.counts && Array.isArray(inbox.needsYou) && Array.isArray(inbox.doneSince), `inbox: ${JSON.stringify(inbox).slice(0, 160)}`);
    await open('/');
    await page.getByText('Needs you', { exact: true }).first().waitFor({ timeout: 10000 });
    const seen = await post('/inbox/seen');
    expect(seen.since && (await call('/inbox')).sinceVisit === true, 'marking the inbox seen did not stick');
  });

  await step('a personal access token made in Settings signs a script in, and revoking it shuts it out', async () => {
    await open('/settings');
    await page.getByRole('button', { name: /New token/ }).first().click();
    const dlg = page.locator('[data-slot="dialog-content"]');
    await dlg.getByRole('textbox', { name: 'Name' }).fill('e2e script');
    await dlg.getByRole('button', { name: /Make token/ }).click();
    const secret = (await dlg.locator('code').first().innerText()).trim();
    expect(secret.startsWith('nc_pat_'), `the token shown is ${secret.slice(0, 10)}…`);
    const as = (p) => fetch(API + p, { headers: { Authorization: `Bearer ${secret}` } });
    expect((await as('/auth/me')).status === 200, 'the token does not sign in');
    // Everything its person holds except a shell on this machine, which a token must name.
    expect((await as('/machine/terminals')).status === 403, 'a token that did not name machine access reached the machine');
    await dlg.getByRole('button', { name: 'Done', exact: true }).click();
    await page.getByRole('button', { name: 'Revoke', exact: true }).first().click();
    await page.getByRole('button', { name: /Click again to revoke/ }).first().click();
    await until(async () => (await as('/auth/me')).status === 401, 'the revoked token being refused', 5000);
  });

  await step('registering an MCP server persists it, untrusted', async () => {
    await open('/mcp');
    await page.getByRole('button', { name: /Add server/ }).first().click();
    const dlg = page.locator('[data-slot="dialog-content"]');
    await dlg.getByRole('button', { name: /^Next/ }).click();
    await dlg.locator('input').first().fill('npx -y @acme/server-ledger');
    await dlg.getByRole('button', { name: /^Next/ }).click();
    await dlg.getByRole('button', { name: /^Next/ }).click();
    await dlg.getByRole('button', { name: /Register server/ }).click();
    await until(async () => (await call('/mcp/servers')).some((s) => s.id === 'ledger' && s.untrusted), 'the server reaching the database', 5000);
  });

  await step('a switched-off skill stays off after a reload', async () => {
    await open('/skills');
    const before = await page.getByRole('switch').first().getAttribute('aria-checked');
    await page.getByRole('switch').first().click();
    await sleep(500);
    await open('/skills');
    const after = await page.getByRole('switch').first().getAttribute('aria-checked');
    expect(after !== before, `the switch is back to ${after} after a reload`);
    expect((await call('/prefs')).some((p) => p.id === 'skills.enabled'), 'skills.enabled is not in the database');
  });

  await step('every model call is in the usage ledger, by feature and by project', async () => {
    const u = await call('/usage');
    const features = new Set(u.byFeature.map((f) => f.feature));
    expect(['compile', 'ask', 'brainstorm', 'extract'].every((f) => features.has(f)), `features in the ledger: ${[...features]}`);
    expect(u.byProject.some((p) => p.projectId === project.id), 'no spend is filed under the project');
    await open('/cost');
    await page.getByText(/By agent/i).first().waitFor({ timeout: 5000 });
  });

  await step('a database backup is made from Admin → Database', async () => {
    await open('/admin/database');
    await page.getByRole('button', { name: /Back up now/ }).click();
    await page.getByText(/neurocode.*-manual\.dump/).first().waitFor({ timeout: 30000 });
    const db = await call('/admin/database');
    expect(db.backups.some((b) => b.name.endsWith('-manual.dump')), 'no manual backup is listed');
    const scripts = fs.readdirSync(path.join(ROOT, 'server/alembic/versions')).filter((f) => f.endsWith('.py')).length;
    expect(db.migrations.length === scripts, `${db.migrations.length} of ${scripts} migrations applied`);
  });

  await step('a model key is saved masked, never logged, and can be removed', async () => {
    await open('/admin/ai');
    await page.getByLabel('API key').fill('sk-e2e-test-0000abcd');
    await page.getByRole('button', { name: 'Save key' }).click();
    await page.getByText('••••abcd').first().waitFor({ timeout: 5000 });
    const cfg = await call('/admin/ai');
    expect(cfg.deepseek.hasKey && cfg.deepseek.keyMask === '••••abcd', `the key reads ${cfg.deepseek.keyMask}`);
    expect(!JSON.stringify(await call('/admin/audit')).includes('0000abcd'), 'the key leaked into the audit log');
    await page.getByRole('button', { name: 'Remove key' }).click();
    await page.getByText('Not set').first().waitFor({ timeout: 5000 });
  });

  let viewer = '';
  await step('an admin adds a Viewer, and the API refuses the Viewer a decision', async () => {
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
    viewer = await tokenFor(VIEWER.email, VIEWER.password);
    const r = await fetch(`${API}/memory/facts`, {
      method: 'POST', headers: { Authorization: `Bearer ${viewer}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ facts: [{ title: 'Viewer fact', body: 'A viewer wrote this.' }] }),
    });
    expect(r.status === 403, `the Viewer's write got ${r.status}`);
  });

  await step('signed in as the Viewer, Admin is hidden and changes are refused', async () => {
    await page.getByRole('button', { name: /^Account:/ }).click();
    await page.getByRole('button', { name: 'Sign out' }).click();
    await page.getByLabel('Email').fill(VIEWER.email);
    await page.getByLabel('Password').fill(VIEWER.password);
    await page.getByRole('button', { name: 'Sign in', exact: true }).click();
    await page.getByRole('button', { name: /^Account:/ }).waitFor({ timeout: 20000 });
    expect(await page.getByRole('link', { name: 'People', exact: true }).count() === 0, 'a Viewer sees Admin → People');
    await open('/memory');
    await page.getByRole('button', { name: /^(Pin|Unpin)$/ }).first().click();
    await page.getByText('Your role cannot do that').waitFor({ timeout: 5000 });
  });

  await step('a reset empties the work and keeps the people, after a backup', async () => {
    const done = await post('/admin/reset', undefined, { 'X-Confirm': 'reset' });
    expect(done.emptied && done.backup?.endsWith('.dump'), `reset answered ${JSON.stringify(done).slice(0, 160)}`);
    const [projects, memory, users] = await Promise.all(['/projects', '/memory', '/admin/users'].map((p) => call(p)));
    expect(!projects.length && !memory.length && users.length === 2, `${projects.length} projects, ${memory.length} facts, ${users.length} people left`);
  });

  if (pageErrors.length) results.push(`  ✗ uncaught errors in the page\n      ${pageErrors.join('\n      ')}`), (process.exitCode = 1);
} catch (e) {
  results.push(`  ✗ setup: ${e.message}`);
  process.exitCode = 1;
} finally {
  await browser?.close();
  await stack?.stop();
}

console.log(`e2e: API + web app + stub model + browser, on a throwaway database\n${results.join('\n')}`);
console.log(process.exitCode ? '✗ failed' : '✓ the stack works end to end');
