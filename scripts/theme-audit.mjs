#!/usr/bin/env node
// Contrast audit for the entire theme system: every palette × every mode × every ground tone of that
// mode, measured before and after src/lib/contrast.ts repairs it. Pure arithmetic, no browser — it
// mirrors how src/index.css derives the OS tokens from a palette, so a regression in either file
// shows up here.   npm run audit:themes
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const OUT = path.join(ROOT, 'node_modules/.cache/nc-audit');
const require = createRequire(import.meta.url);

// Transpile the theme sources to CommonJS without type-checking. (The tsc CLI refuses to emit at all
// under TS 6 when an option like `--module commonjs` is deprecated.) The package.json marks the
// output as CommonJS because the project root is "type": "module".
const ts = require(path.join(ROOT, 'node_modules/typescript'));
fs.rmSync(OUT, { recursive: true, force: true });
fs.mkdirSync(path.join(OUT, 'themes'), { recursive: true });
fs.writeFileSync(path.join(OUT, 'package.json'), '{ "type": "commonjs" }\n');
for (const [src, dest] of [
  ['src/lib/themes/theme-utils.ts', 'themes/theme-utils.js'],
  ['src/lib/themes/theme-definitions.ts', 'themes/theme-definitions.js'],
  ['src/lib/contrast.ts', 'contrast.js'],
]) {
  const { outputText } = ts.transpileModule(fs.readFileSync(path.join(ROOT, src), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  });
  fs.writeFileSync(path.join(OUT, dest), outputText);
}

const { themeColors, surfaceToneOptions } = require(path.join(OUT, 'themes/theme-definitions.js'));
const { themeBaseMap, themeModeOverrides } = require(path.join(OUT, 'themes/theme-utils.js'));
const C = require(path.join(OUT, 'contrast.js'));

const MODES = ['light', 'dark', 'dim', 'midnight', 'sepia', 'high-contrast'];
const T = C.TARGET;

// [label, pick(tokens) → [fg, bg], minimum]
const CHECKS = [
  ['ink on page', (t) => [t.ink, t.bg], T.text],
  ['ink-2 on card', (t) => [t.ink2, t.surface], T.text],
  ['muted text on card', (t) => [t.soft, t.surface], T.text],
  ['muted text on raised', (t) => [t.soft, t.surface2], T.text],
  ['meta labels on card', (t) => [t.dim, t.surface], T.meta],
  ['brand text on card', (t) => [t.brand, t.surface], T.text],
  ['brand text on page', (t) => [t.brand, t.bg], T.text],
  ['text on brand fill', (t) => [t.brandInk, t.brand], T.text],
  ['button text on primary', (t) => [t.primaryInk, t.primary], T.text],
  ['nav item on rail', (t) => [t.railItem, t.railBg], T.text],
  ['active nav on rail', (t) => [t.ink, t.railActive], T.text],
  ['solid rail: nav item', (t) => [t.primaryInk, t.primary], T.text],
  ['success text on card', (t) => [t.ok, t.surface], T.text],
  ['warning text on card', (t) => [t.warn, t.surface], T.text],
  ['error text on card', (t) => [t.danger, t.surface], T.text],
  ['info text on card', (t) => [t.info, t.surface], T.text],
];

function tokens(v, base, repaired) {
  const h = (k) => C.hslToRgb(v[k]);
  const bg = h('--background'), fg = h('--foreground'), card = h('--card'), sec = h('--secondary'), prim = h('--primary');
  const muted = h('--muted-foreground');
  const baseRgb = base === 'dark' ? C.mix(bg, [0, 0, 0], 0.92) : C.mix(bg, fg, 0.94);
  const status = Object.fromEntries(Object.entries(C.STATUS[base]).map(([k, s]) => [k, C.hslToRgb(s)]));
  const t = {
    bg, surface: card, surface2: sec, ink: fg, ink2: C.mix(fg, bg, 0.76), primary: prim,
    soft: muted, dim: C.mix(muted, bg, 0.74), brand: prim, brandInk: h('--primary-foreground'), primaryInk: h('--primary-foreground'),
    ok: status['--status-success'], warn: status['--status-warning'], danger: status['--status-error'], info: status['--status-info'],
    railBg: C.mix(prim, baseRgb, base === 'dark' ? 0.06 : 0.07),
    railItem: C.mix(fg, bg, base === 'dark' ? 0.86 : 0.88),
    railActive: C.mix(prim, sec, base === 'dark' ? 0.16 : 0.15),
  };
  if (!repaired) return t;
  const r = { ...t, ...repaired.rgb };
  r.railBg = C.mix(r.primary, baseRgb, base === 'dark' ? 0.06 : 0.07);
  r.railActive = C.mix(r.primary, sec, base === 'dark' ? 0.16 : 0.15);
  return r;
}

const tally = { before: {}, after: {} };
const worst = { before: {}, after: {} };
let combos = 0;

for (const scheme of themeColors) {
  for (const mode of MODES) {
    const base = themeBaseMap[mode] ?? 'light';
    const tones = surfaceToneOptions.filter((x) => x.mode === base);
    for (const tone of tones) {
      const v = { ...(base === 'dark' ? scheme.darkVars : scheme.lightVars), ...(themeModeOverrides[mode] ?? {}), ...tone.overrides };
      combos++;
      const fixed = C.repairPalette(v, base);
      for (const [phase, tk] of [['before', tokens(v, base, null)], ['after', tokens(v, base, fixed)]]) {
        for (const [label, pick, min] of CHECKS) {
          const [f, b] = pick(tk);
          const r = C.contrast(f, b);
          if (r < min) tally[phase][label] = (tally[phase][label] ?? 0) + 1;
          if (!worst[phase][label] || r < worst[phase][label].r) worst[phase][label] = { r, where: `${scheme.name} · ${mode} · ${tone.label}` };
        }
      }
    }
  }
}

console.log(`theme audit: ${combos.toLocaleString()} combinations (${themeColors.length} palettes × ${MODES.length} modes × every ground tone of that mode)\n`);
console.log('check'.padEnd(26) + 'min'.padStart(5) + '   before: fails · worst'.padEnd(44) + '   after: fails · worst');
let afterFails = 0;
for (const [label, , min] of CHECKS) {
  const b = tally.before[label] ?? 0, a = tally.after[label] ?? 0;
  afterFails += a;
  const wb = worst.before[label], wa = worst.after[label];
  console.log(label.padEnd(26) + String(min).padStart(5) +
    `   ${String(b).padStart(6)} · ${wb.r.toFixed(2)} ${wb.where}`.slice(0, 44).padEnd(44) +
    `   ${String(a).padStart(6)} · ${wa.r.toFixed(2)}${a ? '  ← ' + wa.where : ''}`);
}
console.log(afterFails ? `\n✗ ${afterFails} failing checks remain after repair` : '\n✓ every combination passes after repair');
process.exitCode = afterFails ? 1 : 0;
