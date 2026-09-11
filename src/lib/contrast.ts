/* ═══════════════════════════════════════════════════════════════
   CONTRAST ENGINE

   248 palettes cannot be hand-checked, and a shadcn palette's
   `--primary` is a button colour picked for a filled background —
   used as TEXT it is often unreadable (yellow on white is 1.6:1).
   repairPalette() takes the palette the ThemeProvider just applied
   and returns overrides that move each text colour toward the
   page's opposite pole just far enough to pass WCAG, keeping its
   hue. scripts/theme-audit.mjs runs the same function over every
   palette × mode × ground tone and fails if anything slips through.
   ═══════════════════════════════════════════════════════════════ */

export type RGB = [number, number, number];

/** Minimum contrast ratios the whole product is held to. */
export const TARGET = { text: 4.5, meta: 3.0 } as const;

/** Status colours per mode. Keep in sync with the defaults in src/index.css (they paint before JS runs). */
export const STATUS: Record<'light' | 'dark', Record<string, string>> = {
  light: { '--status-success': '142 71% 38%', '--status-warning': '32 95% 42%', '--status-error': '0 74% 48%', '--status-info': '199 89% 42%', '--status-violet': '271 70% 52%' },
  dark: { '--status-success': '142 69% 58%', '--status-warning': '38 92% 60%', '--status-error': '0 84% 65%', '--status-info': '199 89% 62%', '--status-violet': '271 85% 74%' },
};

const INK_DARK: RGB = [17, 19, 24];
const INK_LIGHT: RGB = [255, 255, 255];

export function hslToRgb(triplet: string): RGB {
  const [h, s, l] = triplet.trim().replace(/%/g, '').split(/\s+/).map(Number);
  const S = s / 100;
  const L = l / 100;
  const a = S * Math.min(L, 1 - L);
  const f = (n: number) => {
    const k = (n + h / 30) % 12;
    return L - a * Math.max(-1, Math.min(k - 3, 9 - k, 1));
  };
  return [f(0), f(8), f(4)].map((v) => Math.round(v * 255)) as RGB;
}

export function rgbToHsl([r, g, b]: RGB): string {
  const R = r / 255, G = g / 255, B = b / 255;
  const max = Math.max(R, G, B), min = Math.min(R, G, B);
  const l = (max + min) / 2;
  let h = 0, s = 0;
  if (max !== min) {
    const d = max - min;
    s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    h = max === R ? (G - B) / d + (G < B ? 6 : 0) : max === G ? (B - R) / d + 2 : (R - G) / d + 4;
    h *= 60;
  }
  return `${h.toFixed(1)} ${(s * 100).toFixed(1)}% ${(l * 100).toFixed(1)}%`;
}

/** Same arithmetic as CSS `color-mix(in srgb, a p, b)`. */
export const mix = (a: RGB, b: RGB, p: number): RGB =>
  a.map((v, i) => Math.round(v * p + b[i] * (1 - p))) as RGB;

const lin = (c: number) => {
  const v = c / 255;
  return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
};
export const luminance = ([r, g, b]: RGB) => 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);

export function contrast(a: RGB, b: RGB) {
  const la = luminance(a), lb = luminance(b);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

export const toCss = ([r, g, b]: RGB) => `rgb(${r} ${g} ${b})`;

/** Whichever of near-black / white reads better on `bg`. */
export const bestOn = (bg: RGB): RGB => (contrast(INK_DARK, bg) >= contrast(INK_LIGHT, bg) ? INK_DARK : INK_LIGHT);

/** Move `fg` toward `pole` by the smallest amount that reaches `target` against every ground. */
export function ensureContrast(fg: RGB, grounds: RGB[], target: number, pole: RGB): RGB {
  const ok = (c: RGB) => grounds.every((g) => contrast(c, g) >= target);
  if (ok(fg)) return fg;
  let lo = 0, hi = 1;
  for (let i = 0; i < 20; i++) {
    const m = (lo + hi) / 2;
    if (ok(mix(pole, fg, m))) hi = m; else lo = m;
  }
  return mix(pole, fg, hi);
}

export interface Repair {
  /** CSS custom properties to set on :root, after the palette itself. */
  css: Record<string, string>;
  /** The resolved colours, for the audit. */
  rgb: Record<'brand' | 'brandInk' | 'primary' | 'primaryInk' | 'soft' | 'dim' | 'ok' | 'warn' | 'danger' | 'info' | 'violet', RGB>;
}

export function repairPalette(v: Record<string, string>, base: 'light' | 'dark'): Repair {
  const at = (k: string, fallback: string) => hslToRgb(v[k] ?? fallback);
  const bg = at('--background', base === 'dark' ? '240 10% 4%' : '0 0% 100%');
  const card = at('--card', v['--background'] ?? '0 0% 100%');
  const secondary = at('--secondary', v['--background'] ?? '0 0% 96%');
  const primary = at('--primary', '221 83% 53%');
  const primaryInk0 = at('--primary-foreground', '0 0% 100%');
  const muted = at('--muted-foreground', base === 'dark' ? '240 5% 65%' : '240 4% 46%');

  const grounds = [bg, card, secondary];
  const pole = luminance(bg) > 0.18 ? INK_DARK : INK_LIGHT;
  const fix = (c: RGB, target: number) => ensureContrast(c, grounds, target, pole);

  const brand = fix(primary, TARGET.text);
  const brandInk = bestOn(brand);
  // Buttons paint --primary with --primary-foreground on top. On a mid-tone fill neither near-black
  // nor white reaches 4.5:1, so swapping the ink is not enough — nudge the FILL away from the ink.
  const primaryInk = contrast(primaryInk0, primary) >= TARGET.text ? primaryInk0 : bestOn(primary);
  const fill = ensureContrast(primary, [primaryInk], TARGET.text, luminance(primaryInk) > 0.18 ? INK_DARK : INK_LIGHT);
  const soft = fix(muted, TARGET.text);
  const dim = fix(mix(muted, bg, 0.74), TARGET.meta);

  const st = Object.fromEntries(Object.entries(STATUS[base]).map(([k, t]) => [k, fix(hslToRgb(t), TARGET.text)]));

  const css: Record<string, string> = {
    '--os-brand': toCss(brand),
    '--os-brand-ink': toCss(brandInk),
    '--os-soft': toCss(soft),
    '--os-dim': toCss(dim),
  };
  for (const [k, c] of Object.entries(st)) css[k] = rgbToHsl(c);
  if (primaryInk !== primaryInk0) css['--primary-foreground'] = rgbToHsl(primaryInk);
  if (fill !== primary) css['--primary'] = rgbToHsl(fill);

  return {
    css,
    rgb: {
      brand, brandInk, primary: fill, primaryInk, soft, dim,
      ok: st['--status-success'], warn: st['--status-warning'], danger: st['--status-error'],
      info: st['--status-info'], violet: st['--status-violet'],
    },
  };
}
