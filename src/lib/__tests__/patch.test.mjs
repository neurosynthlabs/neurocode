#!/usr/bin/env node
// A run's diff, read into its files.
//
// The run screen used to print the whole patch as one block of text, so the moment that matters most —
// the signature — was a wall of monochrome. Splitting it is the one piece of that with no React in it:
// what git wrote in, which files come out, and what each one counts. Loaded the way the other unit
// tests here load a module: transpiled in memory, no build step.
//
//   node src/lib/__tests__/patch.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

const here = path.dirname(fileURLToPath(import.meta.url));

/** The module, with its imports left behind: `splitPatch` uses none of them, and `@/lib/api` has no
    loader out here. Everything else in the file is types, which the transpiler drops. */
const load = async (file) => {
  const source = fs.readFileSync(path.join(here, '..', file), 'utf8')
    .split('\n').filter((line) => !line.startsWith('import ')).join('\n');
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  });
  return import('data:text/javascript;base64,' + Buffer.from(outputText).toString('base64'));
};

const { splitPatch } = await load('live/runtime.ts');

const PATCH = `diff --git a/src/pay.ts b/src/pay.ts
index 1111111..2222222 100644
--- a/src/pay.ts
+++ b/src/pay.ts
@@ -1,4 +1,5 @@
 export function total(n: number) {
-  return n;
+  // GST is split between the two states
+  return n * 1.18;
 }
diff --git a/src/new.ts b/src/new.ts
new file mode 100644
index 0000000..3333333
--- /dev/null
+++ b/src/new.ts
@@ -0,0 +1,2 @@
+export const rate = 0.18;
+
diff --git a/old/gone.py b/old/gone.py
deleted file mode 100644
index 4444444..0000000
--- a/old/gone.py
+++ /dev/null
@@ -1,1 +0,0 @@
-print("bye")
diff --git a/docs/a.md b/docs/b.md
similarity index 98%
rename from docs/a.md
rename to docs/b.md
diff --git a/logo.png b/logo.png
index 5555555..6666666 100644
Binary files a/logo.png and b/logo.png differ
`;

test('every file in the patch comes out, in the order git wrote them', () => {
  const files = splitPatch(PATCH);
  assert.deepEqual(files.map((f) => f.path), ['src/pay.ts', 'src/new.ts', 'old/gone.py', 'docs/b.md', 'logo.png']);
});

test('what happened to each file is read from git’s header, not from the counts', () => {
  const by = Object.fromEntries(splitPatch(PATCH).map((f) => [f.path, f]));
  assert.equal(by['src/pay.ts'].change, 'M');
  assert.equal(by['src/new.ts'].change, 'A');
  assert.equal(by['old/gone.py'].change, 'D');
  assert.equal(by['docs/b.md'].change, 'R');
  assert.equal(by['docs/b.md'].from, 'docs/a.md');
  assert.equal(by['logo.png'].binary, true);
  assert.equal(by['logo.png'].additions, 0);
});

test('the counts are the lines the patch really holds', () => {
  const by = Object.fromEntries(splitPatch(PATCH).map((f) => [f.path, f]));
  assert.deepEqual([by['src/pay.ts'].additions, by['src/pay.ts'].deletions], [2, 1]);
  assert.deepEqual([by['src/new.ts'].additions, by['src/new.ts'].deletions], [2, 0]);
  assert.deepEqual([by['old/gone.py'].additions, by['old/gone.py'].deletions], [0, 1]);
});

test('a file’s body starts at its first hunk, so the header is not drawn twice', () => {
  const [first] = splitPatch(PATCH);
  assert.ok(first.body.startsWith('@@ -1,4 +1,5 @@'));
  assert.ok(!first.body.includes('index 1111111'));
  assert.ok(first.body.includes('+  return n * 1.18;'));
});

test('a rename with no hunks has no body, and nothing invented for it', () => {
  const renamed = splitPatch(PATCH).find((f) => f.path === 'docs/b.md');
  assert.equal(renamed.body, '');
  assert.deepEqual([renamed.additions, renamed.deletions], [0, 0]);
});

test('a patch cut off mid-file keeps the half it was given', () => {
  const cut = PATCH.slice(0, PATCH.indexOf('+  return n * 1.18;') + 10);
  const [only] = splitPatch(cut);
  assert.equal(splitPatch(cut).length, 1);
  assert.equal(only.path, 'src/pay.ts');
  assert.equal(only.additions, 2);            // both + lines are there; the second one stops mid-line
  assert.ok(only.body.endsWith('+  return'));   // trailing space and all, trimmed at the end
});

test('nothing at all is no files, not one empty one', () => {
  assert.deepEqual(splitPatch(''), []);
  assert.deepEqual(splitPatch('\n \n'), []);
});
