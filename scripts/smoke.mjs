#!/usr/bin/env node
// NeuroCode runtime smoke test.
//
// Renders every route of the production build in headless Chromium — once in a dark theme and once
// in a light one — then drives the ⌘K palette, the Appearance sheet, both wizards and a task sheet.
// It catches what tsc cannot: render-time throws, console errors, blank or stub screens, and
// horizontal overflow.
//
//   npm run smoke              build, then test
//   node scripts/smoke.mjs     test the existing dist/
//   SMOKE_SHOTS=/tmp/nc-shots node scripts/smoke.mjs   also save a screenshot per route (keep it outside the repo)
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright-core';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PORT = Number(process.env.SMOKE_PORT ?? 5192);
const BASE = `http://127.0.0.1:${PORT}`;
const SHOTS = process.env.SMOKE_SHOTS ? path.resolve(process.env.SMOKE_SHOTS) : null;

function findChrome() {
  if (process.env.CHROME_PATH) return process.env.CHROME_PATH;
  const roots = [
    path.join(os.homedir(), 'Library/Caches/ms-playwright'),
    path.join(os.homedir(), '.cache/ms-playwright'),
  ];
  for (const root of roots) {
    if (!fs.existsSync(root)) continue;
    const dirs = fs.readdirSync(root).filter((d) => d.startsWith('chromium_headless_shell-')).sort().reverse();
    for (const dir of dirs) {
      for (const sub of fs.readdirSync(path.join(root, dir))) {
        const exe = path.join(root, dir, sub, 'chrome-headless-shell');
        if (fs.existsSync(exe)) return exe;
      }
    }
  }
  throw new Error('No headless Chromium found. Set CHROME_PATH, or run: npx playwright install chromium-headless-shell');
}

// Routes come from the nav config itself, so a new screen is covered the moment it is added.
const nav = fs.readFileSync(path.join(ROOT, 'src/lib/nav.ts'), 'utf8');
const ROUTES = ['/login', ...[...nav.matchAll(/to: '(\/[^']*)'/g)].map((m) => m[1]), '/projects/erp', '/projects/hims'];

const THEMES = [
  { name: 'graphite-dark', ls: { preset: 'graphite', mode: 'dark', color: 'zinc', tone: 'dark-graphite' } },
  { name: 'paper-light', ls: { preset: 'paper', mode: 'light', color: 'blue', tone: 'light-default' } },
];

let serverExit = null;

async function waitForServer(ms = 20000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    if (serverExit !== null) throw new Error(`vite preview exited (code ${serverExit}) — is port ${PORT} already in use? Try SMOKE_PORT=5199`);
    try { if ((await fetch(BASE)).ok) return; } catch { /* not up yet */ }
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error(`preview server did not start on ${BASE}`);
}

if (!fs.existsSync(path.join(ROOT, 'dist/index.html'))) throw new Error('dist/ is missing — run npm run build first');
const viteBin = path.join(ROOT, 'node_modules/vite/bin/vite.js');
const previewArgs = ['preview', '--port', String(PORT), '--strictPort', '--host', '127.0.0.1'];
const server = fs.existsSync(viteBin)
  ? spawn(process.execPath, [viteBin, ...previewArgs], { cwd: ROOT, stdio: 'ignore' })
  : spawn('npx', ['vite', ...previewArgs], { cwd: ROOT, stdio: 'ignore' });
server.on('exit', (code) => { serverExit = code ?? -1; });
for (const sig of ['SIGINT', 'SIGTERM']) process.on(sig, () => { server.kill(); process.exit(130); });
const problems = [];
let browser;

try {
  await waitForServer();
  browser = await chromium.launch({ executablePath: findChrome() });
  if (SHOTS) fs.mkdirSync(SHOTS, { recursive: true });

  // ── 1. every route, every theme ─────────────────────────────
  for (const theme of THEMES) {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    await ctx.addInitScript((ls) => { for (const [k, v] of Object.entries(ls)) localStorage.setItem(`aios.theme.${k}`, v); }, theme.ls);
    for (const route of ROUTES) {
      const page = await ctx.newPage();
      const errors = [];
      page.on('console', (m) => { if (m.type() === 'error') errors.push('console: ' + m.text().slice(0, 240)); });
      page.on('pageerror', (e) => errors.push('pageerror: ' + String(e.message).slice(0, 240)));
      try {
        await page.goto(BASE + route, { waitUntil: 'networkidle', timeout: 20000 });
        await page.waitForTimeout(250);
        const p = await page.evaluate(() => {
          const main = document.querySelector('main') ?? document.body;
          const text = (main.innerText || '').trim();
          return {
            text: text.length,
            stub: /Pending build|has not been implemented/i.test(text),
            crashed: /This screen crashed/.test(text),
            skeleton: !!document.querySelector('[aria-busy="true"]'),
            overflowX: document.documentElement.scrollWidth - window.innerWidth,
          };
        });
        if (p.stub) errors.push('stub page');
        if (p.crashed) errors.push('route error boundary tripped');
        if (p.skeleton) errors.push('still on the loading skeleton after networkidle');
        if (p.text < 200) errors.push(`only ${p.text} chars of content`);
        if (p.overflowX > 2) errors.push(`horizontal overflow ${p.overflowX}px`);
        if (SHOTS) await page.screenshot({ path: path.join(SHOTS, `${theme.name}${route.replaceAll('/', '_')}.png`) });
      } catch (e) {
        errors.push('navigation: ' + String(e.message).slice(0, 160));
      }
      if (errors.length) problems.push({ where: `[${theme.name}] ${route}`, errors });
      await page.close();
    }
    await ctx.close();
  }

  // ── 2. interactions ─────────────────────────────────────────
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  const ixErrors = [];
  page.on('pageerror', (e) => ixErrors.push(String(e.message).slice(0, 240)));
  const check = async (name, fn) => {
    try { await fn(); } catch (e) { problems.push({ where: `interaction: ${name}`, errors: [String(e.message).split('\n')[0].slice(0, 200)] }); }
  };
  const expect = (cond, msg) => { if (!cond) throw new Error(msg); };

  await check('⌘K palette opens and searches', async () => {
    await page.goto(BASE + '/', { waitUntil: 'networkidle' });
    await page.keyboard.press('Meta+k');
    await page.waitForTimeout(250);
    if (!(await page.locator('[cmdk-input]').count())) { await page.keyboard.press('Control+k'); await page.waitForTimeout(250); }
    expect(await page.locator('[cmdk-input]').count(), 'palette input never appeared');
    await page.keyboard.type('TaxService');
    await page.waitForTimeout(250);
    expect(await page.locator('[cmdk-item]').count(), 'no results for "TaxService"');
    await page.keyboard.press('Escape');
  });

  await check('Appearance sheet switches the theme', async () => {
    await page.goto(BASE + '/', { waitUntil: 'networkidle' });
    await page.getByTitle(/Appearance/).click();
    await page.getByText(/palettes ·/).waitFor({ timeout: 3000 });
    await page.getByRole('button', { name: /Carbon/ }).first().click();
    await page.waitForTimeout(200);
    expect((await page.getAttribute('html', 'data-theme')) === 'carbon', 'data-theme did not change to carbon');
    await page.keyboard.press('Escape');
  });

  await check('Onboarding wizard walks all four steps', async () => {
    await page.goto(BASE + '/projects', { waitUntil: 'networkidle' });
    await page.getByRole('button', { name: /New project/ }).click();
    const dlg = page.locator('[data-slot="dialog-content"]');
    await dlg.waitFor({ timeout: 3000 });
    expect(await dlg.getByRole('button', { name: /^Next/ }).isDisabled(), 'Next should be disabled before a repository is entered');
    await dlg.locator('input').first().fill('git@github.com:sofscript/careworks-erp.git');
    for (let n = 0; n < 3; n++) await dlg.getByRole('button', { name: /^Next/ }).click();
    await dlg.getByRole('button', { name: /Start onboarding/ }).click();
    await page.waitForTimeout(300);
    expect(!(await dlg.count()), 'wizard did not close after finishing');
  });

  await check('MCP add-server wizard registers a server', async () => {
    await page.goto(BASE + '/mcp', { waitUntil: 'networkidle' });
    await page.getByRole('button', { name: /Add server/ }).click();
    const dlg = page.locator('[data-slot="dialog-content"]');
    await dlg.waitFor({ timeout: 3000 });
    await dlg.getByRole('button', { name: /^Next/ }).click();
    await dlg.locator('input').first().fill('npx -y @modelcontextprotocol/server-postgres postgres://readonly@localhost/erp');
    await dlg.getByRole('button', { name: /^Next/ }).click();
    await dlg.getByRole('button', { name: /^Next/ }).click();
    await dlg.getByRole('button', { name: /Register server/ }).click();
    await page.waitForTimeout(300);
    expect(!(await dlg.count()), 'wizard did not close after registering');
  });

  await check('Task detail sheet opens', async () => {
    await page.goto(BASE + '/tasks', { waitUntil: 'networkidle' });
    await page.locator('button:has-text("TASK-492")').first().click();
    await page.locator('[data-slot="sheet-content"]').waitFor({ timeout: 3000 });
  });

  if (ixErrors.length) problems.push({ where: 'interactions (uncaught)', errors: ixErrors });
  await ctx.close();
} finally {
  await browser?.close();
  server.kill();
}

const renders = ROUTES.length * THEMES.length;
console.log(`smoke: ${renders} renders (${ROUTES.length} routes × ${THEMES.length} themes) + 5 interactions`);
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
