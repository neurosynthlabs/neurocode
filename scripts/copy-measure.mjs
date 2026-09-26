#!/usr/bin/env node
// How much the UI says, measured from the source: every JSX text node and every string handed to a
// prop that is read on screen (title, hint, description, placeholder, …) or to a toast. Data is not
// counted — only words a person wrote into the app. The design rule it checks: hints stay short and no
// sentence on screen runs past 20 words.
//
//   node scripts/copy-measure.mjs            totals, the heaviest files, and every sentence over 20 words
//   node scripts/copy-measure.mjs --json     the same as JSON
//   node scripts/copy-measure.mjs --max 20   exit 1 if any sentence is longer than 20 words
import fs from 'node:fs';
import path from 'node:path';
import ts from 'typescript';
import { fileURLToPath } from 'node:url';

const ROOT = process.env.COPY_ROOT ?? path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const SRC = path.join(ROOT, 'src');
const args = process.argv.slice(2);
const asJson = args.includes('--json');
const maxAt = args.indexOf('--max');
const MAX = maxAt >= 0 ? Number(args[maxAt + 1]) : null;

/** Props whose value is read by a person, not by code. */
const SPOKEN = new Set(['title', 'description', 'hint', 'label', 'placeholder', 'detail', 'body', 'note', 'empty',
  'subtitle', 'tip', 'tooltip', 'help', 'message', 'caption', 'summary', 'text', 'confirm', 'lede',
  'emptyText', 'emptyTitle', 'emptyHint', 'sub', 'explain', 'why', 'reason']);
const TOASTS = new Set(['success', 'error', 'info', 'warning', 'message']);

function files(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((d) => {
    const p = path.join(dir, d.name);
    if (d.isDirectory()) return ['__tests__', 'mock', 'ui'].includes(d.name) ? [] : files(p);
    return d.name.endsWith('.tsx') ? [p] : [];
  });
}

const words = (s) => s.split(/\s+/).filter((w) => /[A-Za-z]/.test(w));
const sentences = (s) => s.split(/(?<=[.!?…])\s+|\n+/).map((x) => x.trim()).filter(Boolean);

function literal(node) {
  if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) return node.text;
  if (ts.isTemplateExpression(node)) return [node.head.text, ...node.templateSpans.map((s) => ' X ' + s.literal.text)].join('');
  return null;
}

/** Words for a person, not a class list, a key or a path: a space or a capital, and at least two letters. */
const prose = (raw) => {
  const t = raw.trim();
  if (!/[A-Za-z]{2}/.test(t)) return false;
  if (!/\s/.test(t) && !/^[A-Z]/.test(t)) return false;           // a key, a slug, a path
  return !t.split(/\s+/).every((w) => /[-:/[\]=#@.]|\d/.test(w));  // a class list or a command
};

/** Every string a JSX expression can put on screen: the branches of a ternary, both sides of ||/??, a template. */
function spoken(node, sf, out) {
  const t = literal(node);
  if (t !== null) { if (prose(t)) out.push({ t, line: sf.getLineAndCharacterOfPosition(node.getStart()).line + 1 }); return; }
  if (ts.isJsxElement(node) || ts.isJsxSelfClosingElement(node) || ts.isJsxFragment(node)) return;  // visit() reads those
  if (ts.isCallExpression(node)) {
    const callee = node.expression.getText(sf);
    if (/^(cx|cn|clsx|classNames)$/.test(callee)) return;
  }
  if (ts.isJsxAttribute(node) && /^(className|key|href|to|src|value|type|id|name|role|variant|size|tone|side|align)$/.test(node.name.getText(sf))) return;
  ts.forEachChild(node, (c) => spoken(c, sf, out));
}

const report = [];
for (const file of files(SRC)) {
  const text = fs.readFileSync(file, 'utf8');
  const sf = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const bits = [];
  // `folded` marks words one click deeper: an `about=` prop, or the children of <About> / <More>.
  const at = (node) => sf.getLineAndCharacterOfPosition(node.getStart()).line + 1;
  const visit = (node, folded = false) => {
    const push = (list) => { for (const b of list) bits.push({ ...b, folded }); };
    if (ts.isJsxText(node)) {
      const t = node.text.replace(/\s+/g, ' ').trim();
      if (t && /[A-Za-z]{2}/.test(t)) bits.push({ t, line: at(node), folded });
      return;
    }
    if (ts.isJsxAttribute(node) && node.initializer) {
      const name = node.name.getText(sf);
      if (name === 'about') {
        const t = literal(node.initializer);
        if (t !== null) bits.push({ t, line: at(node), folded: true });
        else ts.forEachChild(node.initializer, (c) => visit(c, true));
        return;
      }
      if (SPOKEN.has(name)) { const out = []; spoken(node.initializer, sf, out); push(out); }
      else ts.forEachChild(node.initializer, (c) => visit(c, folded));
      return;
    }
    if ((ts.isJsxElement(node) || ts.isJsxSelfClosingElement(node))) {
      const tag = (ts.isJsxElement(node) ? node.openingElement.tagName : node.tagName).getText(sf);
      if (tag === 'About' || tag === 'More') { ts.forEachChild(node, (c) => visit(c, true)); return; }
    }
    if (ts.isJsxExpression(node) && node.expression && (ts.isJsxElement(node.parent) || ts.isJsxFragment(node.parent))) {
      const out = []; spoken(node.expression, sf, out); push(out);
      // JSX nested inside the expression (a ternary of elements) still has text of its own.
      ts.forEachChild(node.expression, function deep(c) { if (ts.isJsxElement(c) || ts.isJsxFragment(c) || ts.isJsxSelfClosingElement(c)) visit(c, folded); else ts.forEachChild(c, deep); });
      return;
    }
    if (ts.isStringLiteral(node) && ts.isJsxAttribute(node.parent) === false && folded && prose(node.text)) {
      bits.push({ t: node.text, line: at(node), folded }); return;
    }
    if (ts.isCallExpression(node) && ts.isPropertyAccessExpression(node.expression)
      && node.expression.expression.getText(sf) === 'toast' && TOASTS.has(node.expression.name.text) && node.arguments[0]) {
      const out = []; spoken(node.arguments[0], sf, out); push(out);
    }
    ts.forEachChild(node, (c) => visit(c, folded));
  };
  visit(sf);
  if (!bits.length) continue;
  // Adjacent JSX text nodes on the same line are one sentence broken by an inline element.
  const long = [];
  for (const b of bits) for (const s of sentences(b.t)) if (words(s).length > 20) long.push({ line: b.line, words: words(s).length, s });
  const count = (xs) => xs.reduce((n, b) => n + words(b.t).length, 0);
  report.push({ file: path.relative(ROOT, file), words: count(bits.filter((b) => !b.folded)), folded: count(bits.filter((b) => b.folded)), long });
}

report.sort((a, b) => b.words - a.words);
const total = report.reduce((n, r) => n + r.words, 0);
const folded = report.reduce((n, r) => n + r.folded, 0);
const longAll = report.flatMap((r) => r.long.map((l) => ({ file: r.file, ...l })));
if (asJson) {
  console.log(JSON.stringify({ total, folded, long: longAll.length, files: report }, null, 2));
} else {
  console.log(`UI copy: ${total.toLocaleString()} words on the page, ${folded.toLocaleString()} one click deeper, in ${report.length} files · ${longAll.length} sentences over 20 words`);
  console.log('\nHeaviest files:');
  for (const r of report.slice(0, 20)) console.log(`  ${String(r.words).padStart(5)}  ${r.file}${r.long.length ? `  (${r.long.length} long)` : ''}`);
  if (longAll.length) {
    console.log('\nSentences over 20 words:');
    for (const l of longAll) console.log(`  ${l.file}:${l.line}  [${l.words}] ${l.s.slice(0, 140)}`);
  }
}
if (MAX !== null && longAll.some((l) => l.words > MAX)) process.exit(1);
