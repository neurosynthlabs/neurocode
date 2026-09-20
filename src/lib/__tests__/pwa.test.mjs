#!/usr/bin/env node
// Installing on a phone: the two rules, read back.
//
// Both are the kind of thing that is only ever noticed in the wrong place — a service worker left
// behind on localhost serves yesterday's chunks to tomorrow's dev server, and a cached /api answer is
// a figure on a screen that nothing measured, which is the one thing this product does not do. So
// they are plain functions, and they are checked here.
//
// public/sw.js is imported as it ships: it is an ES module whose listeners only attach when there is
// a `self` to attach them to, so in node it is just its rules.
//
//   node --test src/lib/__tests__/pwa.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

const here = path.dirname(fileURLToPath(import.meta.url));
const load = async (file) => {
  const source = fs.readFileSync(path.join(here, '..', file), 'utf8');
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  });
  return import('data:text/javascript;base64,' + Buffer.from(outputText).toString('base64'));
};

const { verdict, shouldReload } = await load('pwa.ts');
const worker = await import(path.join(here, '..', '..', '..', 'public', 'sw.js'));
const { plan, stale, PRECACHE, SHELL, UNHASHED } = worker;

const phone = { supported: true, dev: false, desktop: false, secure: true, automated: false };
const ORIGIN = 'https://neurocode.example.com';
const asked = (url, extra = {}) => plan({ method: 'GET', url, mode: 'no-cors', ...extra }, ORIGIN);

/* ── who gets a service worker ─────────────────────────────────── */

test('a phone on the real thing gets the worker', () => {
  assert.equal(verdict(phone).act, 'register');
  assert.match(verdict(phone).why, /never cached/);
});

test('the dev server never gets one, and loses one it was left with', () => {
  // The failure this prevents: `vite preview` on :5180 registers a worker, and the next morning the
  // dev server on the same port is served that build's chunks with no way to tell.
  const found = verdict({ ...phone, dev: true });
  assert.equal(found.act, 'remove');
  assert.match(found.why, /Vite/);
});

test('the desktop app never gets one', () => {
  const found = verdict({ ...phone, desktop: true });
  assert.equal(found.act, 'remove');
  assert.match(found.why, /desktop app/);
});

test('plain http from another machine is left alone, and says why', () => {
  const found = verdict({ ...phone, secure: false });
  assert.equal(found.act, 'nothing');
  assert.match(found.why, /HTTPS/);
  assert.match(found.why, /still works/);
});

test('a browser without service workers is not an error', () => {
  assert.equal(verdict({ ...phone, supported: false }).act, 'nothing');
});

test('a browser being driven by a check sees the build, not a cache of it', () => {
  const found = verdict({ ...phone, automated: true });
  assert.equal(found.act, 'nothing');
  assert.match(found.why, /driven by a check/);
});

test('the first install does not reload the page it was registered from', () => {
  // It claims that page the moment it activates, which is what makes the very first visit work
  // offline afterwards — and which, read as "a new version has arrived", is a white flash on every
  // first visit for a worker that has changed nothing. Only a *replacement* is worth a reload.
  assert.equal(shouldReload(false, false), false);
  assert.equal(shouldReload(true, false), true);
  // And never twice: a deploy that cannot settle must not become a tab that reloads for ever.
  assert.equal(shouldReload(true, true), false);
});

/* ── what the worker may answer ────────────────────────────────── */

test('nothing under /api is ever cached', () => {
  for (const url of [`${ORIGIN}/api/runs`, `${ORIGIN}/api/health`, `${ORIGIN}/api`,
                     `${ORIGIN}/api/activity/stream`, `${ORIGIN}/api/sessions/SES-1/messages`]) {
    assert.equal(asked(url), 'pass', `${url} must go to the network every time`);
  }
  // Including the navigation-shaped ones: a request for /api with mode navigate is still the API.
  assert.equal(asked(`${ORIGIN}/api/docs`, { mode: 'navigate' }), 'pass');
});

test('a page is fetched, with the cached shell only as a fallback', () => {
  assert.equal(asked(`${ORIGIN}/`, { mode: 'navigate' }), 'shell');
  assert.equal(asked(`${ORIGIN}/runs`, { mode: 'navigate' }), 'shell');
  assert.equal(asked(`${ORIGIN}/projects/PRJ-1`, { mode: 'navigate' }), 'shell');
});

test('fingerprinted chunks come from the cache, unhashed files are refreshed behind you', () => {
  assert.equal(asked(`${ORIGIN}/assets/react-Bq1s9d.js`), 'asset');
  assert.equal(asked(`${ORIGIN}/assets/index-9f2a.css`), 'asset');
  for (const path of UNHASHED) assert.equal(asked(ORIGIN + path), 'refresh', path);
});

test('another origin, and anything that is not a plain GET, is none of its business', () => {
  assert.equal(asked('https://api.groq.com/openai/v1/chat'), 'pass');
  assert.equal(asked(`${ORIGIN}/`, { method: 'POST', mode: 'navigate' }), 'pass');
  assert.equal(asked(`${ORIGIN}/assets/x.js`, { method: 'HEAD' }), 'pass');
  assert.equal(plan({ method: 'GET', url: 'not a url at all', mode: 'no-cors' }, ORIGIN), 'pass');
});

test('the shell it precaches is the page it falls back to', () => {
  assert.ok(PRECACHE.includes(SHELL));
  assert.ok(PRECACHE.includes('/manifest.webmanifest'));
});

test('activation clears versions that are gone and nobody else’s caches', () => {
  const gone = stale(['neurocode-shell-v0', 'neurocode-assets-v0', 'neurocode-shell-v1',
                      'neurocode-assets-v1', 'workbox-precache', 'some-other-app']);
  assert.deepEqual(gone.sort(), ['neurocode-assets-v0', 'neurocode-shell-v0']);
});

/* ── the manifest is a fact about files on disk ────────────────── */

test('every icon the manifest names is really there', () => {
  const root = path.join(here, '..', '..', '..');
  const manifest = JSON.parse(fs.readFileSync(path.join(root, 'public', 'manifest.webmanifest'), 'utf8'));
  assert.equal(manifest.display, 'standalone');
  assert.equal(manifest.start_url, '/');
  for (const icon of manifest.icons) {
    assert.ok(fs.existsSync(path.join(root, 'public', icon.src.slice(1))), `${icon.src} is missing`);
  }
  // Android crops an adaptive icon to a circle; without a maskable one it crops the artwork instead.
  assert.ok(manifest.icons.some((i) => i.purpose === 'maskable'));
  // A phone's launcher wants a 192 and a 512, and an SVG alone is not enough for every launcher.
  for (const size of ['192x192', '512x512']) {
    assert.ok(manifest.icons.some((i) => i.sizes === size && i.type === 'image/png'), size);
  }
});

test('the page asks for the manifest and hands the phone its safe area', () => {
  const page = fs.readFileSync(path.join(here, '..', '..', '..', 'index.html'), 'utf8');
  assert.match(page, /rel="manifest" href="\/manifest\.webmanifest"/);
  assert.match(page, /viewport-fit=cover/);
  assert.match(page, /env\(safe-area-inset-top\)/);
  // A 24 px icon button is the right size to look at and the wrong size to hit.
  assert.match(page, /max\(100%, 44px\)/);
});
