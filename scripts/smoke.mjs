#!/usr/bin/env node
// NeuroCode runtime smoke test.
//
// Renders every route of the production build in headless Chromium — once in a dark theme and once
// in a light one — against the real stack: a throwaway Postgres holding the tests' workspace, the API,
// and a stub model. Then it drives the ⌘K palette, the Appearance sheet, both wizards, a task sheet and
// an approval. It catches what tsc cannot: render-time throws, console errors, blank or stub screens,
// screens that never finish loading, and horizontal overflow.
//
// There is no sample workspace in the app to render instead, so a screen with nothing on it here is a
// screen that failed to read its data.
//
//   npm run smoke              build, then test
//   node scripts/smoke.mjs     test the existing dist/
//   SMOKE_SHOTS=/tmp/nc-shots node scripts/smoke.mjs   also save a screenshot per route (keep it outside the repo)
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from 'playwright-core';
import { findChrome } from './chrome.mjs';
import { ROOT, startStack } from './stack.mjs';
import { signedIn, settle } from './browser.mjs';

const API_PORT = Number(process.env.SMOKE_API_PORT ?? 8796);
const WEB_PORT = Number(process.env.SMOKE_PORT ?? 5196);
const SHOTS = process.env.SMOKE_SHOTS ? path.resolve(process.env.SMOKE_SHOTS) : null;

// Routes come from the nav config itself, so a new screen is covered the moment it is added; the two
// project pages are the fixture's projects.
const nav = fs.readFileSync(path.join(ROOT, 'src/lib/nav.ts'), 'utf8');
const ROUTES = [...[...nav.matchAll(/to: '(\/[^']*)'/g)].map((m) => m[1]), '/projects/erp', '/projects/hims'];

const THEMES = [
  { name: 'graphite-dark', ls: { preset: 'graphite', mode: 'dark', color: 'zinc', tone: 'dark-graphite' } },
  { name: 'paper-light', ls: { preset: 'paper', mode: 'light', color: 'blue', tone: 'light-default' } },
];

if (!fs.existsSync(path.join(ROOT, 'dist/index.html'))) throw new Error('dist/ is missing — run npm run build first');

const problems = [];
let interactions = 0;
let browser;
const stack = await startStack({ name: 'smoke', apiPort: API_PORT, webPort: WEB_PORT, web: 'preview', fixture: true, owner: true });
for (const sig of ['SIGINT', 'SIGTERM']) process.on(sig, () => { void stack.stop().finally(() => process.exit(130)); });
const BASE = stack.web;

/** Console errors and uncaught throws on one page, collected as they happen. */
function watch(page) {
  const errors = [];
  page.on('console', (m) => { if (m.type() === 'error') errors.push('console: ' + m.text().slice(0, 240)); });
  page.on('pageerror', (e) => errors.push('pageerror: ' + String(e.message).slice(0, 240)));
  return errors;
}

async function render(ctx, route, label) {
  const page = await ctx.newPage();
  const errors = watch(page);
  try {
    await page.goto(BASE + route, { waitUntil: 'domcontentloaded', timeout: 20000 });
    const p = await settle(page);
    if (p.stub) errors.push('stub page');
    if (p.crashed) errors.push('route error boundary tripped');
    if (p.busy) errors.push('still loading after 15 s');
    if (p.offline) errors.push('the app says it is not connected');
    if (p.text < 80) errors.push(`only ${p.text} chars of content`);
    if (p.overflowX > 2) errors.push(`horizontal overflow ${p.overflowX}px`);
    if (SHOTS) await page.screenshot({ path: path.join(SHOTS, `${label}${route.replaceAll('/', '_')}.png`) });
  } catch (e) {
    errors.push('navigation: ' + String(e.message).slice(0, 160));
  }
  if (errors.length) problems.push({ where: `[${label}] ${route}`, errors });
  await page.close();
}

try {
  browser = await chromium.launch({ executablePath: findChrome() });
  if (SHOTS) fs.mkdirSync(SHOTS, { recursive: true });

  // ── 1. every route, every theme ─────────────────────────────
  for (const theme of THEMES) {
    const ctx = await signedIn(browser, stack, { viewport: { width: 1440, height: 900 } });
    await ctx.addInitScript((ls) => { for (const [k, v] of Object.entries(ls)) localStorage.setItem(`aios.theme.${k}`, v); }, theme.ls);
    for (const route of ROUTES) await render(ctx, route, theme.name);
    await ctx.close();
  }
  // The sign-in screen, as someone with no session sees it.
  const anon = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await render(anon, '/login', 'signed-out');
  await anon.close();

  // ── 2. interactions ─────────────────────────────────────────
  const ctx = await signedIn(browser, stack, { viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  const ixErrors = [];
  page.on('pageerror', (e) => ixErrors.push(String(e.message).slice(0, 240)));
  const check = async (name, fn) => {
    interactions++;
    try { await fn(); } catch (e) { problems.push({ where: `interaction: ${name}`, errors: [String(e.message).split('\n')[0].slice(0, 200)] }); }
  };
  const expect = (cond, msg) => { if (!cond) throw new Error(msg); };
  const open = async (route) => { await page.goto(BASE + route, { waitUntil: 'domcontentloaded' }); await settle(page); };
  const api = (p, init = {}) => fetch(stack.api + p, {
    // The check's own reads sign in like a script would: a bearer token, not the browser's cookie.
    ...init, headers: { Authorization: `Bearer ${stack.token}`, 'Content-Type': 'application/json', ...init.headers },
  }).then((r) => r.json());

  await check('⌘K palette finds a task', async () => {
    await open('/');
    await page.keyboard.press('Meta+k');
    await page.waitForTimeout(250);
    if (!(await page.locator('[cmdk-input]').count())) { await page.keyboard.press('Control+k'); await page.waitForTimeout(250); }
    expect(await page.locator('[cmdk-input]').count(), 'palette input never appeared');
    await page.keyboard.type('TASK-492');
    await page.locator('[cmdk-item]').first().waitFor({ timeout: 3000 });
    await page.keyboard.press('Escape');
  });

  await check('Appearance sheet switches the theme', async () => {
    await open('/');
    await page.getByTitle(/Appearance/).click();
    await page.getByText(/palettes ·/).waitFor({ timeout: 3000 });
    await page.getByRole('button', { name: /Carbon/ }).first().click();
    await page.waitForTimeout(200);
    expect((await page.getAttribute('html', 'data-theme')) === 'carbon', 'data-theme did not change to carbon');
    await page.keyboard.press('Escape');
  });

  await check('Onboarding wizard onboards a folder on this machine', async () => {
    // A real folder, so the onboarding that starts is the real one and finishes without the network.
    const repo = path.join(stack.tmp, 'ledger-lite');
    fs.mkdirSync(path.join(repo, 'src'), { recursive: true });
    fs.writeFileSync(path.join(repo, 'src', 'ledger.py'), 'def total(rows):\n    return sum(r.amount for r in rows)\n');
    await open('/projects');
    await page.getByRole('button', { name: /New project/ }).click();
    const dlg = page.locator('[data-slot="dialog-content"]');
    await dlg.waitFor({ timeout: 3000 });
    const local = dlg.getByRole('button', { name: /folder|local/i }).first();
    if (await local.count()) await local.click();
    await dlg.locator('input').first().fill(repo);
    for (let n = 0; n < 8 && !(await dlg.getByRole('button', { name: /Start onboarding/ }).count()); n++) {
      await dlg.getByRole('button', { name: /^Next/ }).click();
    }
    await dlg.getByRole('button', { name: /Start onboarding/ }).click();
    await dlg.waitFor({ state: 'detached', timeout: 5000 });
    const t0 = Date.now();
    for (;;) {
      const projects = await api('/projects');
      const made = projects.find((p) => p.name?.toLowerCase().includes('ledger'));
      if (made && made.status !== 'onboarding') { expect(made.status === 'active', `onboarding ended as ${made.status}`); break; }
      expect(Date.now() - t0 < 30000, 'onboarding did not finish in 30 s');
      await new Promise((r) => setTimeout(r, 500));
    }
  });

  await check('MCP add-server wizard registers a server', async () => {
    await open('/mcp');
    await page.getByRole('button', { name: /Add server/ }).click();
    const dlg = page.locator('[data-slot="dialog-content"]');
    await dlg.waitFor({ timeout: 3000 });
    await dlg.getByRole('button', { name: /^Next/ }).click();
    await dlg.locator('input').first().fill('npx -y @modelcontextprotocol/server-postgres postgres://readonly@localhost/erp');
    await dlg.getByRole('button', { name: /^Next/ }).click();
    await dlg.getByRole('button', { name: /^Next/ }).click();
    await dlg.getByRole('button', { name: /Register server/ }).click();
    await dlg.waitFor({ state: 'detached', timeout: 5000 });
  });

  await check('Task detail sheet opens', async () => {
    // The board shows the project picked in the top bar, so open whichever task it shows first.
    await open('/tasks');
    await page.locator('button:has-text("TASK-")').first().click();
    await page.locator('[data-slot="sheet-content"]').waitFor({ timeout: 3000 });
  });

  await check('Approving updates the inbox, the badge and the log', async () => {
    const pending = (await api('/approvals')).filter((a) => a.status === 'pending');
    expect(pending.length, 'the fixture has no pending approval');
    await open('/permissions');
    const count = async () => Number((await page.getByTitle(/approvals waiting on you/).getAttribute('title')).match(/\d+/)[0]);
    const before = await count();
    await page.getByRole('button', { name: 'Approve', exact: true }).first().click();
    await page.waitForFunction((n) => {
      const el = document.querySelector('[title*="approvals waiting on you"]');
      return el && Number(el.getAttribute('title').match(/\d+/)[0]) === n;
    }, before - 1, { timeout: 5000 });
    const after = (await api('/approvals')).filter((a) => a.status === 'pending');
    expect(after.length === pending.length - 1, `the server still holds ${after.length} pending`);
    const decided = pending.find((a) => !after.some((b) => b.ref === a.ref));
    await page.getByRole('button', { name: 'Activity', exact: true }).click();
    await page.getByText(decided.ref).first().waitFor({ timeout: 5000 });
  });

  if (ixErrors.length) problems.push({ where: 'interactions (uncaught)', errors: ixErrors });
  await ctx.close();
} finally {
  await browser?.close();
  await stack.stop();
}

const renders = ROUTES.length * THEMES.length + 1;
console.log(`smoke: ${renders} renders (${ROUTES.length} routes × ${THEMES.length} themes, and sign-in) + ${interactions} interactions`);
if (!problems.length) {
  console.log('✓ no problems');
} else {
  console.log(`✗ ${problems.length} problem${problems.length > 1 ? 's' : ''}`);
  for (const p of problems) {
    console.log(`\n  ${p.where}`);
    for (const e of p.errors) console.log(`    ${e}`);
  }
  process.exitCode = 1;
}
