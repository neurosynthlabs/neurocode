#!/usr/bin/env node
// The store's two ceilings, tested on their own.
//
// The web app has no unit runner — its tests are the browser scripts in scripts/ — but the page walk
// and the feed's ceiling are plain functions with no React and no network in them, and they are exactly
// the kind of thing that is wrong by one page or one row without anyone noticing. So they are tested
// here: the module's own TypeScript is transpiled in memory with the compiler the repo already has,
// and imported. No build step, no new dependency.
//
//   node src/lib/__tests__/limits.test.mjs
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

const { walkPages } = await load('paging.ts');
const { addEvent, FEED_CAP } = await load('feed.ts');

/** A server holding `total` rows, answering offset/limit and counting what it was asked. */
function server(total) {
  const asked = [];
  const rows = Array.from({ length: total }, (_, i) => ({ id: `r${i}` }));
  return {
    asked,
    fetchPage: async (offset, limit) => {
      asked.push(offset);
      return rows.slice(offset, offset + limit);
    },
  };
}
const WALK = { pageMax: 10, loadCap: 100, pagesAtOnce: 3 };

test('a workspace that fits in one page costs one request', async () => {
  const s = server(7);
  const out = await walkPages(s.fetchPage, WALK);
  assert.equal(out.items.length, 7);
  assert.equal(out.capped, false);
  assert.deepEqual(s.asked, [0]);
});

test('an exactly full first page is not mistaken for the end of the list', async () => {
  const s = server(10);
  const out = await walkPages(s.fetchPage, WALK);
  assert.equal(out.items.length, 10);
  assert.equal(out.capped, false);
});

test('every row is read, whatever the list length', async () => {
  for (const total of [0, 1, 10, 11, 29, 30, 31, 55, 99, 100]) {
    const out = await walkPages(server(total).fetchPage, WALK);
    assert.equal(out.items.length, total, `${total} rows`);
    assert.equal(out.capped, false, `${total} rows should not read as capped`);
  }
});

test('pages after the first are asked for several at a time', async () => {
  const rows = Array.from({ length: 55 }, (_, i) => ({ id: `r${i}` }));
  let live = 0;
  let most = 0;
  let firstPageCompany = null;
  await walkPages(async (offset, limit) => {
    live += 1;
    most = Math.max(most, live);
    if (offset === 0) firstPageCompany = live;
    await new Promise((r) => setTimeout(r, 5));
    live -= 1;
    return rows.slice(offset, offset + limit);
  }, WALK);
  // Most workspaces fit in the first page, so that one is asked for alone and costs one request.
  assert.equal(firstPageCompany, 1, 'the first page should be asked for on its own');
  // After it, waiting for each page before asking for the next is what made signing in slow.
  assert.ok(most > 1, `pages after the first should overlap; at most ${most} was in flight`);
});

test('a list at the ceiling says so, and one row short of it does not', async () => {
  assert.equal((await walkPages(server(120).fetchPage, WALK)).capped, true);
  assert.equal((await walkPages(server(100).fetchPage, WALK)).capped, false);
});

test('a row repeated across two pages is kept once', async () => {
  const rows = Array.from({ length: 25 }, (_, i) => ({ id: `r${i}` }));
  const out = await walkPages(async (offset, limit) => rows.slice(Math.max(0, offset - 1), Math.max(0, offset - 1) + limit), WALK);
  assert.equal(out.items.length, 25);
  assert.equal(new Set(out.items.map((r) => r.id)).size, 25);
});

test('the feed never grows past its ceiling', () => {
  let feed = [];
  for (let i = 0; i < FEED_CAP + 500; i += 1) feed = addEvent(feed, { id: String(i) });
  assert.equal(feed.length, FEED_CAP);
  // Newest first: the newest line is at the top and the oldest ones are the ones that went.
  assert.equal(feed[0].id, String(FEED_CAP + 499));
  assert.equal(feed.at(-1).id, String(500));
});

test('a line the feed already holds is not added twice', () => {
  const one = addEvent([], { id: 'a' });
  const again = addEvent(one, { id: 'a' });
  assert.equal(again, one, 'the same feed should come back, so nothing re-renders');
  assert.equal(again.length, 1);
});
