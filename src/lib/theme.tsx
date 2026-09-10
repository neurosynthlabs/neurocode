import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import {
  themeColors, surfaceToneOptions, fontFamilyOptions, fontSizeOptions,
  themeBaseMap, themeModeOverrides, fontFamilyMap, fontSizeMap,
} from '@/lib/themes';
import type { Theme, ThemeColor, SurfaceToneOption, FontFamily, FontSize } from '@/lib/themes';

export type { Theme, ThemeColor, SurfaceToneOption, FontFamily, FontSize };

/* ═══════════════════════════════════════════════════════════════
   Presets — one-click combinations of mode + palette + ground.
   The full axes stay available underneath in the customizer.
   ═══════════════════════════════════════════════════════════════ */
export interface ThemeDef {
  id: string;
  name: string;
  mode: Theme;
  color: string;
  tone: string;
  swatch: [string, string, string];
  note: string;
}

export const THEMES: ThemeDef[] = [
  { id: 'graphite', name: 'Graphite', mode: 'dark',  color: 'zinc',       tone: 'dark-graphite', swatch: ['#0F1013', '#17181C', '#A1A1AA'], note: 'Neutral slate — the reference ground' },
  { id: 'cobalt',   name: 'Cobalt',   mode: 'dark',  color: 'blue',       tone: 'dark-slate',    swatch: ['#0A0C10', '#12151B', '#3B82F6'], note: 'Deep slate, blue signal' },
  { id: 'carbon',   name: 'Carbon',   mode: 'dark',  color: 'gold',       tone: 'dark-carbon',   swatch: ['#0A0A0B', '#131314', '#F0B429'], note: 'Near-black, amber signal' },
  { id: 'abyss',    name: 'Abyss',    mode: 'dark',  color: 'cyan',       tone: 'dark-navy',     swatch: ['#070B14', '#0E1524', '#22D3EE'], note: 'Deep navy, cyan signal' },
  { id: 'moss',     name: 'Moss',     mode: 'dark',  color: 'emerald',    tone: 'dark-forest',   swatch: ['#08110D', '#101C17', '#34D399'], note: 'Forest ground, mint signal' },
  { id: 'plum',     name: 'Plum',     mode: 'dark',  color: 'violet',     tone: 'dark-plum',     swatch: ['#0D0A14', '#171122', '#A78BFA'], note: 'Ink violet, soft contrast' },
  { id: 'nord',     name: 'Nord',     mode: 'dim',   color: 'steel-blue', tone: 'dark-cool',     swatch: ['#1D1F24', '#26292F', '#88C0D0'], note: 'Dimmed arctic slate, easy on the eyes' },
  { id: 'void',     name: 'Void',     mode: 'midnight', color: 'indigo',  tone: 'dark-void',     swatch: ['#000000', '#080808', '#818CF8'], note: 'True black — OLED, maximum contrast' },
  { id: 'contrast', name: 'Contrast', mode: 'high-contrast', color: 'yellow', tone: 'dark-void', swatch: ['#000000', '#0A0A0A', '#FACC15'], note: 'Accessibility — maximum legibility' },
  { id: 'paper',    name: 'Paper',    mode: 'light', color: 'blue',       tone: 'light-default', swatch: ['#FFFFFF', '#F4F5F7', '#2563EB'], note: 'Clean white, blue signal' },
  { id: 'snow',     name: 'Snow',     mode: 'light', color: 'slate',      tone: 'light-snow',    swatch: ['#FBFCFD', '#F1F3F5', '#475569'], note: 'Icy neutral, quiet' },
  { id: 'sand',     name: 'Sand',     mode: 'light', color: 'gold',       tone: 'light-sand',    swatch: ['#F8F6F2', '#F0EDE6', '#B45309'], note: 'Warm ground, clay signal' },
  { id: 'sepia',    name: 'Sepia',    mode: 'sepia', color: 'mocha',      tone: 'light-linen',   swatch: ['#F0E9DC', '#E7DECB', '#8B5E3C'], note: 'Paper warmth — long reading sessions' },
];

export type Density = 'compact' | 'comfortable';
export type Rail = 'tinted' | 'solid' | 'flush';

interface Ctx {
  /* preset axis (legacy-compatible) */
  theme: string;
  setTheme: (id: string) => void;
  themes: ThemeDef[];
  current: ThemeDef;

  /* full axes */
  mode: Theme;
  setMode: (m: Theme) => void;
  themeColor: string;
  setThemeColor: (v: string) => void;
  surfaceTone: string;
  setSurfaceTone: (v: string) => void;
  radius: number;
  setRadius: (n: number) => void;
  fontFamily: FontFamily;
  setFontFamily: (f: FontFamily) => void;
  fontSize: FontSize;
  setFontSize: (s: FontSize) => void;
  density: Density;
  setDensity: (d: Density) => void;
  rail: Rail;
  setRail: (r: Rail) => void;

  /* catalogues */
  availableColors: ThemeColor[];
  surfaceTones: SurfaceToneOption[];
  fontFamilies: typeof fontFamilyOptions;
  fontSizes: typeof fontSizeOptions;
  baseMode: 'light' | 'dark';
}

const C = createContext<Ctx | null>(null);
const K = 'aios.theme';

const read = (k: string, d: string) => {
  try { return localStorage.getItem(`${K}.${k}`) ?? d; } catch { return d; }
};
const write = (k: string, v: string) => {
  try { localStorage.setItem(`${K}.${k}`, v); } catch { /* private mode */ }
};

const resolveBase = (m: Theme): 'light' | 'dark' => {
  if (m === 'system') {
    return typeof window !== 'undefined' && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }
  return themeBaseMap[m] ?? 'light';
};

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [preset, setPreset] = useState(() => read('preset', 'graphite'));
  const [mode, setModeState] = useState<Theme>(() => read('mode', 'dark') as Theme);
  const [themeColor, setColorState] = useState(() => read('color', 'zinc'));
  const [surfaceTone, setToneState] = useState(() => read('tone', 'dark-graphite'));
  const [radius, setRadiusState] = useState(() => Number(read('radius', '0.5')) || 0.5);
  const [fontFamily, setFontState] = useState<FontFamily>(() => read('font', 'inter') as FontFamily);
  const [fontSize, setSizeState] = useState<FontSize>(() => read('size', 'compact') as FontSize);
  const [density, setDensityState] = useState<Density>(() => read('density', 'compact') as Density);
  const [rail, setRailState] = useState<Rail>(() => read('rail', 'tinted') as Rail);

  const baseMode = resolveBase(mode);

  /* ── mode → light/dark class + data attrs ───────────────────── */
  useEffect(() => {
    const root = document.documentElement;
    root.classList.remove('light', 'dark');
    root.classList.add(baseMode);
    root.setAttribute('data-mode', mode);
    root.setAttribute('data-theme', preset);
    root.setAttribute('data-density', density);
    root.setAttribute('data-rail', rail);
    root.style.colorScheme = baseMode;
  }, [mode, baseMode, preset, density, rail]);

  /* ── palette → surface tone → mode overrides, in that order ─── */
  useEffect(() => {
    const root = document.documentElement;
    const scheme = themeColors.find((c) => c.value === themeColor) ?? themeColors[0];
    const vars = baseMode === 'dark' ? scheme.darkVars : scheme.lightVars;
    Object.entries(vars).forEach(([k, v]) => root.style.setProperty(k, v));

    const overrides = themeModeOverrides[mode];
    if (overrides) Object.entries(overrides).forEach(([k, v]) => root.style.setProperty(k, v));

    const tone = surfaceToneOptions.find((t) => t.value === surfaceTone);
    if (tone && tone.mode === baseMode) {
      Object.entries(tone.overrides).forEach(([k, v]) => root.style.setProperty(k, v));
    }
  }, [themeColor, surfaceTone, mode, baseMode]);

  /* ── radius / type ──────────────────────────────────────────── */
  useEffect(() => {
    const root = document.documentElement;
    root.style.setProperty('--radius', `${radius}rem`);
    root.style.setProperty('--app-font-family', fontFamilyMap[fontFamily]);
    root.style.setProperty('--app-font-size', fontSizeMap[fontSize]);
  }, [radius, fontFamily, fontSize]);

  /* ── react to OS scheme changes while on "system" ───────────── */
  useEffect(() => {
    if (mode !== 'system') return;
    const mq = window.matchMedia('(prefers-color-scheme: dark)');
    const onChange = () => setModeState('system');
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, [mode]);

  const setMode = useCallback((m: Theme) => { write('mode', m); setModeState(m); }, []);
  const setThemeColor = useCallback((v: string) => { write('color', v); setColorState(v); }, []);
  const setSurfaceTone = useCallback((v: string) => { write('tone', v); setToneState(v); }, []);
  const setRadius = useCallback((n: number) => { write('radius', String(n)); setRadiusState(n); }, []);
  const setFontFamily = useCallback((f: FontFamily) => { write('font', f); setFontState(f); }, []);
  const setFontSize = useCallback((s: FontSize) => { write('size', s); setSizeState(s); }, []);
  const setDensity = useCallback((d: Density) => { write('density', d); setDensityState(d); }, []);
  const setRail = useCallback((r: Rail) => { write('rail', r); setRailState(r); }, []);

  /** Applying a preset moves all three colour axes at once. */
  const setTheme = useCallback((id: string) => {
    const p = THEMES.find((t) => t.id === id);
    if (!p) return;
    write('preset', p.id); setPreset(p.id);
    write('mode', p.mode); setModeState(p.mode);
    write('color', p.color); setColorState(p.color);
    write('tone', p.tone); setToneState(p.tone);
  }, []);

  const current = useMemo(() => THEMES.find((t) => t.id === preset) ?? THEMES[0], [preset]);

  const value = useMemo<Ctx>(() => ({
    theme: preset, setTheme, themes: THEMES, current,
    mode, setMode, themeColor, setThemeColor, surfaceTone, setSurfaceTone,
    radius, setRadius, fontFamily, setFontFamily, fontSize, setFontSize,
    density, setDensity, rail, setRail,
    availableColors: themeColors,
    surfaceTones: surfaceToneOptions,
    fontFamilies: fontFamilyOptions,
    fontSizes: fontSizeOptions,
    baseMode,
  }), [preset, setTheme, current, mode, setMode, themeColor, setThemeColor, surfaceTone,
       setSurfaceTone, radius, setRadius, fontFamily, setFontFamily, fontSize, setFontSize,
       density, setDensity, rail, setRail, baseMode]);

  return <C.Provider value={value}>{children}</C.Provider>;
}

export function useTheme() {
  const ctx = useContext(C);
  if (!ctx) throw new Error('useTheme must be used inside <ThemeProvider>');
  return ctx;
}
