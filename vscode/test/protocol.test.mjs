#!/usr/bin/env node
// The extension's rules, read back without an editor.
//
// VS Code cannot be started here, and the half of this extension that talks to the editor is thin on
// purpose — every decision it makes lives in src/protocol.ts, which imports nothing. This is that
// file, compiled by the same `tsc -p .` that builds the extension, and checked.
//
//   cd vscode && npm test          (builds, then runs this)
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import protocol from '../out/protocol.js';

const {
  candidates, isHealth, looksLikeToken, readEvents, relativeTo, folderOf,
  selectionQuestion, sessionTitle, runLabel, runDescription, order, applyChange,
  workbenchUrl, sessionUrl, planUrl, runUrl, withoutTrailingSlash,
} = protocol;

const here = path.dirname(fileURLToPath(import.meta.url));

/* ── finding the server ────────────────────────────────────────── */

test('one address is tried as a web app first, then as an API — the order nc uses', () => {
  assert.deepEqual(candidates('http://localhost:5180'), [
    { api: 'http://localhost:5180/api', web: 'http://localhost:5180' },
    { api: 'http://localhost:5180', web: null },
  ]);
});

test('a bare host is http, and a trailing slash is not a different server', () => {
  assert.deepEqual(candidates('localhost:5180/')[0], {
    api: 'http://localhost:5180/api',
    web: 'http://localhost:5180',
  });
  assert.equal(withoutTrailingSlash('https://nc.example.com///'), 'https://nc.example.com');
  assert.deepEqual(candidates('   '), []);
});

test('some other server answering 200 is not NeuroCode', () => {
  assert.ok(isHealth({ ok: true, db: 'x', counts: {} }));
  assert.ok(!isHealth({ status: 'ok' }));
  assert.ok(!isHealth(null));
  assert.ok(!isHealth('ok'));
});

test('a password pasted into the token box is refused before it is stored', () => {
  assert.ok(looksLikeToken('nc_pat_9f2c8ab41de04c7f8b'));
  assert.ok(!looksLikeToken('hunter2'));
  assert.ok(!looksLikeToken('nc_pat_'));
  assert.ok(!looksLikeToken('Bearer nc_pat_9f2c8ab41de04c7f'));
});

/* ── the live stream ───────────────────────────────────────────── */

test('an event split across two reads is one event, not two halves', () => {
  // This is the whole reason the parser is written this way: a stream is bytes, and the network
  // decides where they are cut. A run's new state arriving as two unparsable halves is a sidebar
  // that silently stops being true.
  const first = readEvents('event: change\ndata: {"op":"put","collec');
  assert.deepEqual(first.events, []);
  const second = readEvents(first.rest + 'tion":"runs","doc":{"ref":"RUN-7","status":"running"}}\n\n');
  assert.equal(second.events.length, 1);
  assert.equal(second.events[0].kind, 'change');
  assert.equal(second.events[0].data.doc.ref, 'RUN-7');
  assert.equal(second.rest, '');
});

test('keep-alive comments and retry lines are not events', () => {
  const { events } = readEvents(': keep-alive\n\nretry: 3000\n\nevent: reset\ndata: {"at":"2026-09-20"}\n\n');
  assert.equal(events.length, 1);
  assert.equal(events[0].kind, 'reset');
});

test('a data line that is not JSON is passed on rather than ending the stream', () => {
  const { events } = readEvents('event: run\ndata: not json\n\nevent: run\ndata: {"runRef":"RUN-1"}\n\n');
  assert.deepEqual(events.map((e) => e.data), ['not json', { runRef: 'RUN-1' }]);
});

test('several data lines are one payload, and CRLF is the same as LF', () => {
  const { events } = readEvents('event: chat\r\ndata: {"a":\r\ndata: 1}\r\n\r\n');
  assert.deepEqual(events[0].data, { a: 1 });
});

/* ── the runs list ─────────────────────────────────────────────── */

const run = (ref, status, startedAt, requirement = '') => ({ ref, status, startedAt, requirement });

test('a change event moves a run in the list instead of the list being polled', () => {
  const before = [run('RUN-1', 'running', '2026-09-20T10:00:00Z')];
  const after = applyChange(before, {
    op: 'put',
    collection: 'runs',
    doc: run('RUN-1', 'waiting', '2026-09-20T10:00:00Z'),
  });
  assert.equal(after.length, 1);
  assert.equal(after[0].status, 'waiting');
});

test('a dropped run leaves, and another collection’s changes are none of its business', () => {
  const list = [run('RUN-1', 'running', '2026-09-20T10:00:00Z')];
  assert.deepEqual(applyChange(list, { op: 'drop', collection: 'runs', id: 'RUN-1' }), []);
  assert.equal(applyChange(list, { op: 'put', collection: 'tasks', doc: { ref: 'T-1' } }), list);
  assert.equal(applyChange(list, null), list);
});

test('whatever is stopped at a person is at the top — the list’s whole purpose', () => {
  const sorted = order([
    run('RUN-1', 'running', '2026-09-20T12:00:00Z'),
    run('RUN-2', 'waiting', '2026-09-20T09:00:00Z'),
    run('RUN-3', 'done', '2026-09-20T13:00:00Z'),
  ]);
  assert.deepEqual(sorted.map((r) => r.ref), ['RUN-2', 'RUN-3', 'RUN-1']);
});

test('a run reads as its reference and what it was asked to do, cut to fit', () => {
  assert.equal(runLabel(run('RUN-7', 'running', '', '')), 'RUN-7');
  const long = runLabel(run('RUN-7', 'running', '', 'x'.repeat(200)), 40);
  assert.ok(long.length <= 40 && long.startsWith('RUN-7 · ') && long.endsWith('…'));
  assert.equal(runLabel(run('RUN-7', 'running', '', ' credit\n notes ')), 'RUN-7 · credit notes');
});

test('a state is said in words, and a state the API grows is not hidden', () => {
  assert.equal(runDescription({ ref: 'RUN-1', status: 'waiting', projectName: 'Ledger' }), 'waiting on you · Ledger');
  assert.equal(runDescription({ ref: 'RUN-1', status: 'interrupted' }), 'interrupted');
  assert.equal(runDescription({ ref: 'RUN-1', status: 'something-new' }), 'something-new');
});

/* ── attaching what you are looking at ─────────────────────────── */

test('a file outside the folder open here has no path, and says so rather than inventing one', () => {
  assert.equal(relativeTo('/Users/r/ledger', '/Users/r/ledger/src/tax.py'), 'src/tax.py');
  assert.equal(relativeTo('/Users/r/ledger/', '/Users/r/ledger/src/tax.py'), 'src/tax.py');
  assert.equal(relativeTo('/Users/r/ledger', '/Users/r/other/tax.py'), null);
  // The near-miss that a naive startsWith gets wrong: a sibling folder with the same prefix.
  assert.equal(relativeTo('/Users/r/ledger', '/Users/r/ledger-old/tax.py'), null);
  assert.equal(relativeTo('/Users/r/ledger', '/Users/r/ledger'), null);
  assert.equal(relativeTo('C:\\code\\ledger', 'C:\\code\\ledger\\src\\tax.py'), 'src/tax.py');
});

test('the Workbench is opened at the folder the file is in', () => {
  assert.equal(folderOf('/Users/r/ledger/src/tax.py'), '/Users/r/ledger/src');
  assert.equal(folderOf('/tax.py'), '/');
});

test('the question carries the lines, so the answer can point back at them', () => {
  const asked = selectionQuestion('why does this round twice?', {
    path: 'src/tax.py',
    from: 42,
    to: 58,
    text: 'def apply_gst(amount):\n    return round(amount * 1.18, 2)\n',
    language: 'python',
  });
  assert.match(asked, /^why does this round twice\?/);
  assert.match(asked, /From `src\/tax\.py`, lines 42–58:/);
  assert.match(asked, /```python\ndef apply_gst/);
  assert.ok(asked.trimEnd().endsWith('```'));
});

test('one line is “line 42”, not “lines 42–42”', () => {
  const asked = selectionQuestion('what is this?', { path: 'a.py', from: 42, to: 42, text: 'x = 1', language: 'python' });
  assert.match(asked, /line 42:/);
  assert.doesNotMatch(asked, /lines/);
});

test('the session is titled for the file and the lines, within what the API accepts', () => {
  assert.equal(sessionTitle({ path: 'src/tax.py', from: 42, to: 58, text: '', language: '' }), 'tax.py:42-58');
  assert.ok(sessionTitle({ path: `${'d/'.repeat(80)}f.py`, from: 1, to: 2, text: '', language: '' }).length <= 80);
});

/* ── the screens it opens ──────────────────────────────────────── */

test('every link is a real screen of the web app, with its reference escaped', () => {
  assert.equal(workbenchUrl('http://localhost:5180/', 'PRJ-1'), 'http://localhost:5180/workbench?project=PRJ-1');
  assert.equal(
    workbenchUrl('http://localhost:5180', 'PRJ 1', '/Users/r/led ger'),
    'http://localhost:5180/workbench?project=PRJ%201&folder=%2FUsers%2Fr%2Fled%20ger',
  );
  assert.equal(sessionUrl('http://x', 'SES-3'), 'http://x/sessions?ref=SES-3');
  assert.equal(planUrl('http://x', 'PLAN-3'), 'http://x/plans?ref=PLAN-3');
  assert.equal(runUrl('http://x', 'RUN-3'), 'http://x/runs?ref=RUN-3');
});

/* ── the manifest is a promise about the code ──────────────────── */

test('every command the manifest contributes is registered, and every one registered is declared', () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(here, '..', 'package.json'), 'utf8'));
  const source = fs.readFileSync(path.join(here, '..', 'src', 'extension.ts'), 'utf8');
  const declared = manifest.contributes.commands.map((c) => c.command).sort();
  const registered = [...source.matchAll(/registerCommand\('([^']+)'/g)].map((m) => m[1]).sort();
  assert.deepEqual(registered, declared);
  // Every menu entry and every view points at something that exists.
  for (const group of Object.values(manifest.contributes.menus)) {
    for (const item of group) assert.ok(declared.includes(item.command), `${item.command} is in a menu and nowhere else`);
  }
});

test('the token is never a setting — it belongs in the editor’s secret store', () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(here, '..', 'package.json'), 'utf8'));
  const keys = Object.keys(manifest.contributes.configuration.properties);
  assert.ok(!keys.some((key) => /token|secret|password/i.test(key)), keys.join(', '));
  const source = fs.readFileSync(path.join(here, '..', 'src', 'extension.ts'), 'utf8');
  assert.match(source, /context\.secrets\.store\(TOKEN, token\)/);
});
