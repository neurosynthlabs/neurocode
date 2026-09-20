#!/usr/bin/env node
// Reading a whole session back, page by page.
//
// The screen asked the detail route once, from zero, and got the FIRST five hundred turns — so a long
// session lost its newest ones with nothing saying so. The walk that replaces it is a plain function
// over a page-fetcher, which is exactly the kind of thing that is wrong by one page without anyone
// noticing, so it is tested here against a server that counts what it was asked.
//
//   node src/lib/__tests__/transcript.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

const here = path.dirname(fileURLToPath(import.meta.url));
const load = async (file) => {
  const source = fs.readFileSync(path.join(here, '..', file), 'utf8')
    .split('\n').filter((line) => !line.startsWith('import ')).join('\n');
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  });
  return import('data:text/javascript;base64,' + Buffer.from(outputText).toString('base64'));
};

const { readTurns, TURN_PAGE } = await load('../components/sessions/transcript.ts');

/** A session of `total` turns, answering "the turns after this id, up to `size`" and counting the asks. */
function session(total, size = TURN_PAGE) {
  const asked = [];
  const rows = Array.from({ length: total }, (_, i) => ({ id: i + 1, text: `turn ${i + 1}` }));
  return {
    asked,
    page: async (after) => {
      asked.push(after);
      return rows.filter((r) => r.id > after).slice(0, size);
    },
  };
}

test('a session that fits in one page is one request, as it always was', async () => {
  const s = session(12);
  const out = await readTurns(s.page);
  assert.equal(out.messages.length, 12);
  assert.deepEqual(s.asked, [0]);
  assert.equal(out.whole, true);
  assert.equal(out.earlier, false);
});

test('a long session keeps going until a page comes back short — and ends on its newest turn', async () => {
  const s = session(1201);
  const out = await readTurns(s.page);
  assert.equal(out.messages.length, 1201);
  assert.equal(out.messages.at(-1).id, 1201);
  assert.deepEqual(s.asked, [0, 500, 1000]);
  assert.equal(out.whole, true);
});

test('a session that is exactly a whole number of pages still asks once more, and says it is whole', async () => {
  const s = session(1000);
  const out = await readTurns(s.page);
  assert.deepEqual(s.asked, [0, 500, 1000]);
  assert.equal(out.messages.length, 1000);
  assert.equal(out.whole, true);
});

test('past the cap the OLDEST turns are the ones dropped, and it says they were', async () => {
  const s = session(2400, 400);
  const out = await readTurns(s.page, { size: 400, cap: 1000 });
  assert.equal(out.messages.length, 1000);
  assert.equal(out.messages.at(-1).id, 2400);          // today's answers are there
  assert.equal(out.messages[0].id, 1401);              // last Tuesday's are not
  assert.equal(out.earlier, true);
  assert.equal(out.whole, true);
});

test('a walk that runs out of requests says so rather than passing off what it has as the whole thing', async () => {
  const s = session(5000, 100);
  const out = await readTurns(s.page, { size: 100, cap: 10_000, requests: 3 });
  assert.equal(s.asked.length, 3);
  assert.equal(out.messages.length, 300);
  assert.equal(out.whole, false);
});

test('an empty session is no turns and no second request', async () => {
  const s = session(0);
  const out = await readTurns(s.page);
  assert.deepEqual(out.messages, []);
  assert.deepEqual(s.asked, [0]);
  assert.equal(out.whole, true);
});

const { matchSessions, sessionHits, turnsSaying } = await load('../components/sessions/transcript.ts');

const SESSIONS = [
  { id: 's1', ref: 'CHAT-7', title: 'Why the merge preview was wrong', projectName: 'Shop', turns: 12, toolCalls: 40, waitingOn: null },
  { id: 's2', ref: 'CHAT-8', title: 'GST split on interstate invoices', projectName: 'Ledger', turns: 1, toolCalls: 0, waitingOn: { messageId: 3, tool: 'web_fetch' } },
];

test('a session is found by its reference, its title or its project', () => {
  assert.deepEqual(matchSessions(SESSIONS, 'merge preview').map((s) => s.ref), ['CHAT-7']);
  assert.deepEqual(matchSessions(SESSIONS, 'chat-8').map((s) => s.ref), ['CHAT-8']);
  assert.deepEqual(matchSessions(SESSIONS, 'ledger').map((s) => s.ref), ['CHAT-8']);
  assert.deepEqual(matchSessions(SESSIONS, '  ').map((s) => s.ref), ['CHAT-7', 'CHAT-8']);
  assert.deepEqual(matchSessions(SESSIONS, 'nothing here'), []);
});

test('⌘K opens the session a hit names, and says what is in it', () => {
  const [first, second] = sessionHits(SESSIONS);
  assert.equal(first.group, 'Sessions');
  assert.equal(first.title, 'CHAT-7 · Why the merge preview was wrong');
  assert.equal(first.to, '/sessions?ref=CHAT-7');
  assert.equal(first.subtitle, 'Shop · 12 answers · 40 tool calls');
  assert.equal(second.subtitle, 'Ledger · 1 answer · 0 tool calls');
  assert.equal(second.meta, 'WAITING');        // it is waiting on a permission card
  assert.equal(first.meta, undefined);
});

test('searching inside a session reads the words, a tool’s line and the tool’s name', () => {
  const placed = [
    { m: { text: 'where is the interstate tax split?', detail: null, tool: null } },
    { m: { text: 'apply_gst_breakup in pkg/tax.py', detail: 'pkg/tax.py:118', tool: 'read_file' } },
    { m: { text: 'nothing to do with it', detail: null, tool: null } },
  ];
  assert.equal(turnsSaying(placed, 'interstate').length, 1);
  assert.equal(turnsSaying(placed, 'pkg/tax.py').length, 1);
  assert.equal(turnsSaying(placed, 'read_file').length, 1);
  assert.equal(turnsSaying(placed, 'GST').length, 1);            // and case does not matter
  assert.equal(turnsSaying(placed, '').length, 3);
  assert.equal(turnsSaying(placed, 'nowhere').length, 0);
});
