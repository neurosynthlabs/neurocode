#!/usr/bin/env node
// Layout lint — renders every route at phone and tablet widths and reports two kinds of breakage:
//   spill  an element pokes past the viewport without sitting inside a horizontal scroller
//   clip   an element is wider than an overflow:hidden ancestor, so part of it is silently cut off
// Only the outermost offender of each problem is reported.
//   node scripts/layout-lint.mjs            (uses dist/, run `npm run build` first)
//   LINT_WIDTHS=390,820,1440 node scripts/layout-lint.mjs
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright-core';
import { findChrome } from './chrome.mjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PORT = Number(process.env.LINT_PORT ?? 5193);
const BASE = `http://127.0.0.1:${PORT}`;
const WIDTHS = (process.env.LINT_WIDTHS ?? '390,820').split(',').map(Number);
const HEIGHTS = { 390: 844, 820: 1180 };


const nav = fs.readFileSync(path.join(ROOT, 'src/lib/nav.ts'), 'utf8');
const ROUTES = ['/login', ...[...nav.matchAll(/to: '(\/[^']*)'/g)].map((m) => m[1]), '/projects/erp'];

let exited = null;
const server = spawn(process.execPath, [path.join(ROOT, 'node_modules/vite/bin/vite.js'), 'preview', '--port', String(PORT), '--strictPort', '--host', '127.0.0.1'], { cwd: ROOT, stdio: 'ignore' });
server.on('exit', (c) => { exited = c; });
for (const sig of ['SIGINT', 'SIGTERM']) process.on(sig, () => { server.kill(); process.exit(130); });

const report = [];
let browser;
try {
  for (let t = 0; ; t++) {
    if (exited !== null) throw new Error(`vite preview exited (${exited}) — port ${PORT} busy?`);
    try { if ((await fetch(BASE)).ok) break; } catch { /* starting */ }
    if (t > 80) throw new Error('preview never came up');
    await new Promise((r) => setTimeout(r, 250));
  }
  browser = await chromium.launch({ executablePath: findChrome() });
  for (const w of WIDTHS) {
    const ctx = await browser.newContext({ viewport: { width: w, height: HEIGHTS[w] ?? 900 }, isMobile: w < 640, hasTouch: w < 1024 });
    for (const route of ROUTES) {
      const page = await ctx.newPage();
      await page.goto(BASE + route, { waitUntil: 'networkidle', timeout: 20000 }).catch(() => {});
      await page.waitForTimeout(200);
      const r = await page.evaluate(() => {
        const W = window.innerWidth;
        const inScroller = (el) => {
          for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
            if (/(auto|scroll)/.test(getComputedStyle(p).overflowX)) return true;
          }
          return false;
        };
        // nearest ancestor that clips horizontally — null if a scroller comes first (that is allowed)
        const clipper = (el) => {
          for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
            const ox = getComputedStyle(p).overflowX;
            if (/(auto|scroll)/.test(ox)) return null;
            if (/(hidden|clip)/.test(ox)) return p;
          }
          return null;
        };
        const bad = new Set();
        for (const el of document.querySelectorAll('body *')) {
          const b = el.getBoundingClientRect();
          if (!b.width || !b.height) continue;
          const cs = getComputedStyle(el);
          if (cs.position === 'fixed' || cs.position === 'absolute' || cs.visibility === 'hidden') continue;
          if (b.right > W + 1 || b.left < -1) {
            if (!inScroller(el)) bad.add(el);
            continue;
          }
          const c = clipper(el);
          if (c) {
            const cb = c.getBoundingClientRect();
            if (b.right > cb.right + 2 || b.left < cb.left - 2) bad.add(el);
          }
        }
        const outer = [...bad].filter((el) => !bad.has(el.parentElement));
        return {
          docOverflow: document.documentElement.scrollWidth - W,
          offenders: outer.slice(0, 6).map((el) => {
            const b = el.getBoundingClientRect();
            const cls = (typeof el.className === 'string' ? el.className : el.className?.baseVal ?? '').slice(0, 70);
            return `${el.tagName.toLowerCase()} [${Math.round(b.left)}→${Math.round(b.right)}] .${cls}`;
          }),
          count: outer.length,
        };
      });
      if (r.count || r.docOverflow > 1) report.push({ w, route, ...r });
      await page.close();
    }
    await ctx.close();
  }
} finally {
  await browser?.close();
  server.kill();
}

const byW = Object.fromEntries(WIDTHS.map((w) => [w, report.filter((x) => x.w === w)]));
for (const w of WIDTHS) {
  const rows = byW[w];
  console.log(`\n${w}px — ${rows.length} of ${ROUTES.length} routes spill`);
  for (const r of rows) {
    console.log(`  ${r.route.padEnd(16)} ${String(r.count).padStart(3)} offenders${r.docOverflow > 1 ? `, page scrolls sideways ${r.docOverflow}px` : ''}`);
    if (process.env.LINT_VERBOSE) for (const o of r.offenders) console.log(`      ${o}`);
  }
}
const total = report.length;
console.log(total ? `\n✗ ${total} route×width combinations spill` : '\n✓ nothing spills at any width');
process.exitCode = total ? 1 : 0;
