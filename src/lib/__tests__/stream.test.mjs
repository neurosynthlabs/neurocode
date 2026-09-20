#!/usr/bin/env node
// What the change stream does with the frames the server really sends.
//
// `api.stream` is the one place the web app learns that what is on screen may no longer be true, and
// the server grew a frame for a case nothing else can tell us about: this reader fell behind and events
// went past it. That is a listener that is either registered or silently absent, so it is tested here
// rather than trusted — the same in-memory transpile the other unit tests use, with EventSource and the
// two window functions the module reaches for stood in for.
//
//   node --test src/lib/__tests__/
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

const here = path.dirname(fileURLToPath(import.meta.url));
const js = (file) => ts.transpileModule(fs.readFileSync(path.join(here, '..', file), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText;
const asModule = (source) => 'data:text/javascript;base64,' + Buffer.from(source).toString('base64');

/** `api.ts` as node can run it: its one runtime import inlined, and Vite's build-time constant filled
    in the way the browser build fills it — this test says nothing about which API a build talks to. */
const api = (await import(asModule(
  js('api.ts')
    .replace("from '@/lib/paging'", `from '${asModule(js('paging.ts'))}'`)
    .replace(/import\.meta\.env/g, '({})'),
))).api;

/** The browser's EventSource, as much of it as the module uses: listeners by name, and a way to send. */
class FakeSource {
  static CLOSED = 2;
  constructor() {
    this.listeners = new Map();
    this.readyState = 1;
    this.closed = false;
  }
  addEventListener(name, fn) {
    this.listeners.set(name, fn);
  }
  close() {
    this.closed = true;
  }
  /** One server-sent event, shaped as the browser hands it over. */
  send(name, data) {
    const fn = this.listeners.get(name);
    assert.ok(fn, `nothing is listening for the server's "${name}" event`);
    fn({ data: JSON.stringify(data) });
  }
}

/** Opens a stream over a fake source and hands back what the caller was told. */
function opened() {
  const made = [];
  globalThis.EventSource = class extends FakeSource {
    constructor(...args) {
      super(...args);
      made.push(this);
    }
  };
  globalThis.window = { setTimeout: () => 0, clearTimeout: () => {}, dispatchEvent: () => {} };
  const told = [];
  const stop = api.stream({
    activity: () => {},
    change: () => {},
    resync: (why) => told.push(why),
  });
  return { source: made[0], told, stop };
}

test('a reader the server says fell behind is told it missed events', () => {
  const { source, told, stop } = opened();
  source.send('resync', { why: 'missed', events: 20 });
  assert.deepEqual(told, ['missed']);
  stop();
});

test('an emptied workspace is still an emptied workspace', () => {
  const { source, told, stop } = opened();
  source.send('reset', {});
  assert.deepEqual(told, ['reset']);
  stop();
});

test('a resync frame this bundle does not know still asks for a reload', () => {
  // A newer server with a reason of its own: what it means is that something happened we did not see,
  // which is exactly what "missed" means. Silence here would leave the screens quietly stale.
  const { source, told, stop } = opened();
  source.send('resync', { why: 'something-new' });
  assert.deepEqual(told, ['missed']);
  stop();
});
