#!/usr/bin/env node
// Layout lint — renders every route at phone and tablet widths and reports two kinds of breakage:
//   spill  an element pokes past the viewport without sitting inside a horizontal scroller
//   clip   an element is wider than an overflow:hidden ancestor, so part of it is silently cut off
// Only the outermost offender of each problem is reported. It runs on the real stack with the tests'
// workspace loaded (see scripts/stack.mjs), because an empty screen cannot spill.
//   node scripts/layout-lint.mjs            (uses dist/, run `npm run build` first)
//   LINT_WIDTHS=390,820,1440 node scripts/layout-lint.mjs
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from 'playwright-core';
import { findChrome } from './chrome.mjs';
import { ROOT, startStack } from './stack.mjs';
import { signedIn, settle } from './browser.mjs';

const API_PORT = Number(process.env.LINT_API_PORT ?? 8797);
const WEB_PORT = Number(process.env.LINT_PORT ?? 5197);
const WIDTHS = (process.env.LINT_WIDTHS ?? '390,820').split(',').map(Number);
const HEIGHTS = { 390: 844, 820: 1180 };

const nav = fs.readFileSync(path.join(ROOT, 'src/lib/nav.ts'), 'utf8');
const ROUTES = ['/login', ...[...nav.matchAll(/to: '(\/[^']*)'/g)].map((m) => m[1]), '/projects/erp'];

if (!fs.existsSync(path.join(ROOT, 'dist/index.html'))) throw new Error('dist/ is missing — run npm run build first');
const stack = await startStack({ name: 'layout', apiPort: API_PORT, webPort: WEB_PORT, web: 'preview', fixture: true, owner: true });
for (const sig of ['SIGINT', 'SIGTERM']) process.on(sig, () => { void stack.stop().finally(() => process.exit(130)); });
const BASE = stack.web;

const report = [];
// Routes that never rendered a real screen. A dead API, a rejected cookie or a crashed route leaves a small
// login, "Not connected" or error panel that cannot spill, so without this the run passed while testing nothing.
const unchecked = [];
let browser;
try {
  browser = await chromium.launch({ executablePath: findChrome() });
  for (const w of WIDTHS) {
    const ctx = await signedIn(browser, stack, { viewport: { width: w, height: HEIGHTS[w] ?? 900 }, isMobile: w < 640, hasTouch: w < 1024 });
    for (const route of ROUTES) {
      const page = await ctx.newPage();
      // /login redirects a signed-in person home, so it is linted with the cookie cleared.
      if (route === '/login') await ctx.clearCookies();
      let p = null;
      try {
        await page.goto(BASE + route, { waitUntil: 'domcontentloaded', timeout: 20000 });
        p = await settle(page);
      } catch (e) {
        unchecked.push({ w, route, why: 'navigation: ' + String(e.message).split('\n')[0].slice(0, 160) });
      } finally {
        if (route === '/login') await ctx.addCookies([{ name: 'nc_session', value: stack.token, url: stack.web, httpOnly: true, sameSite: 'Lax' }]);
      }
      if (!p) { await page.close(); continue; }
      const why = [
        p.offline && 'the app says it is not connected',
        p.crashed && 'the route crashed',
        p.busy && 'still loading after 15 s',
        p.text < 80 && `only ${p.text} chars of content`,
        route !== '/login' && new URL(page.url()).pathname === '/login' && 'signed out: it landed on /login',
      ].filter(Boolean);
      if (why.length) { unchecked.push({ w, route, why: why.join('; ') }); await page.close(); continue; }
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
  await stack.stop();
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
if (unchecked.length) {
  console.log(`\n${unchecked.length} route×width combinations never rendered a screen, so they were not checked`);
  for (const u of unchecked) console.log(`  ${String(u.w).padStart(4)}px ${u.route.padEnd(16)} ${u.why}`);
}
const total = report.length;
console.log(total ? `\n✗ ${total} route×width combinations spill` : unchecked.length ? '\n✗ nothing spilled, but not every screen was checked' : '\n✓ nothing spills at any width');
process.exitCode = total || unchecked.length ? 1 : 0;
