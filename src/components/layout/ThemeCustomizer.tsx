import { useMemo, useState } from 'react';
import { Check, Search, RotateCcw, PanelLeft, PanelLeftDashed, PanelLeftClose } from 'lucide-react';
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from '@/components/ui/sheet';
import { Button } from '@/components/ui/button';
import { Segmented, SectionTitle, Divider } from '@/components/os';
import { useTheme, THEMES, type FontFamily, type FontSize, type Rail } from '@/lib/theme';
import { MODES } from './theme-modes';
import { cn } from '@/lib/utils';


const RADII = [0, 0.25, 0.375, 0.5, 0.75, 1];

const RAILS: { id: Rail; label: string; icon: typeof PanelLeft; note: string }[] = [
  { id: 'tinted', label: 'Tinted', icon: PanelLeftDashed, note: 'Palette hue washed into the ground.' },
  { id: 'solid',  label: 'Solid',  icon: PanelLeft,       note: 'A full slab of the accent; best on mid-tone palettes.' },
  { id: 'flush',  label: 'Flush',  icon: PanelLeftClose,  note: 'Edge to edge, for the most density.' },
];

/** Reads the palette's own vars so each swatch previews its real colour. */
function paletteSwatch(vars: Record<string, string>) {
  return [vars['--background'], vars['--card'], vars['--primary']].map((v) => `hsl(${v})`);
}

export function ThemeCustomizer({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  const t = useTheme();
  const [tab, setTab] = useState<'presets' | 'colour' | 'ground' | 'type'>('presets');
  const [q, setQ] = useState('');

  const colours = useMemo(() => {
    const s = q.trim().toLowerCase();
    const list = s ? t.availableColors.filter((c) => c.name.toLowerCase().includes(s)) : t.availableColors;
    return list;
  }, [q, t.availableColors]);

  const tones = useMemo(
    () => t.surfaceTones.filter((x) => x.mode === t.baseMode),
    [t.surfaceTones, t.baseMode],
  );

  const reset = () => {
    t.setTheme('graphite');
    t.setRail('tinted');
    t.setRadius(0.5);
    t.setFontFamily('inter');
    t.setFontSize('compact');
    t.setDensity('compact');
  };

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-[420px] gap-0 p-0 sm:max-w-none">
        <SheetHeader className="shrink-0 border-b border-line px-4 pt-4 pb-3">
          <SheetTitle className="text-[14px] font-semibold text-ink">Appearance</SheetTitle>
          <SheetDescription className="text-[13px] text-soft">
            {t.availableColors.length} palettes · {t.surfaceTones.length} grounds · {MODES.length} modes. Applies at once.
          </SheetDescription>
        </SheetHeader>

        {/* Mode — always visible, it gates everything below */}
        <div className="shrink-0 border-b border-line px-4 py-3">
          <SectionTitle>Mode</SectionTitle>
          <div className="grid grid-cols-4 gap-1.5">
            {MODES.map((m) => {
              const Icon = m.icon;
              const active = t.mode === m.id;
              return (
                <button
                  key={m.id}
                  onClick={() => t.setMode(m.id)}
                  className={cn(
                    'flex flex-col items-center gap-1 rounded-sm border px-1.5 py-2 text-[11.5px] transition-colors',
                    active
                      ? 'border-brand bg-brand/10 font-medium text-brand'
                      : 'border-line bg-surface-2 text-soft hover:border-line-strong hover:text-ink',
                  )}
                >
                  <Icon className="size-3.5" />
                  {m.label}
                </button>
              );
            })}
          </div>
        </div>

        {/* Rail */}
        <div className="shrink-0 border-b border-line px-4 py-3">
          <SectionTitle right={<span className="text-[11.5px] text-dim">sidebar</span>}>Rail style</SectionTitle>
          <div className="grid grid-cols-3 gap-1.5">
            {RAILS.map((r) => {
              const Icon = r.icon;
              const active = t.rail === r.id;
              return (
                <button
                  key={r.id}
                  onClick={() => t.setRail(r.id)}
                  title={r.note}
                  className={cn(
                    'flex flex-col items-center gap-1 rounded-sm border px-1.5 py-2 text-[11.5px] transition-colors',
                    active ? 'border-brand bg-brand/10 font-medium text-brand'
                           : 'border-line bg-surface-2 text-soft hover:border-line-strong hover:text-ink',
                  )}
                >
                  <Icon className="size-3.5" />
                  {r.label}
                </button>
              );
            })}
          </div>
          <p className="mt-1.5 text-[11.5px] leading-snug text-dim">
            {RAILS.find((r) => r.id === t.rail)?.note}
          </p>
        </div>

        <div className="shrink-0 border-b border-line px-4 py-2.5">
          <Segmented
            className="w-full"
            options={[
              { id: 'presets', label: 'Presets' },
              { id: 'colour', label: `Palette (${t.availableColors.length})` },
              { id: 'ground', label: `Ground (${tones.length})` },
              { id: 'type', label: 'Type' },
            ]}
            value={tab}
            onChange={(v) => setTab(v)}
          />
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
          {tab === 'presets' && (
            <div className="space-y-1.5">
              {THEMES.map((p) => (
                <button
                  key={p.id}
                  onClick={() => t.setTheme(p.id)}
                  className={cn(
                    'hover-lift flex w-full items-center gap-3 rounded-md border px-2.5 py-2 text-left',
                    t.theme === p.id ? 'border-brand bg-brand/8' : 'border-line bg-surface',
                  )}
                >
                  <span className="flex shrink-0 overflow-hidden rounded-sm border border-line-strong">
                    {p.swatch.map((c, i) => <span key={i} className="size-5" style={{ background: c }} />)}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-1.5">
                      <span className="text-[13.5px] font-medium text-ink">{p.name}</span>
                      <span className="text-[11px] text-dim">{p.mode}</span>
                    </span>
                    <span className="block truncate text-[12px] text-dim">{p.note}</span>
                  </span>
                  {t.theme === p.id && <Check className="size-3.5 shrink-0 text-brand" />}
                </button>
              ))}
            </div>
          )}

          {tab === 'colour' && (
            <>
              <div className="mb-2.5 flex h-9 items-center gap-2 rounded-lg border border-line bg-surface-2 px-2.5 focus-within:border-brand">
                <Search className="size-3.5 shrink-0 text-dim" />
                <input
                  value={q}
                  onChange={(e) => setQ(e.target.value)}
                  placeholder={`Search ${t.availableColors.length} palettes…`}
                  className="min-w-0 flex-1 bg-transparent text-[13px] text-ink placeholder:text-dim focus-visible:outline-none"
                />
                {q && <button onClick={() => setQ('')} className="text-[12px] text-dim hover:text-ink">clear</button>}
              </div>
              <div className="grid grid-cols-3 gap-1.5">
                {colours.map((c) => {
                  const sw = paletteSwatch(t.baseMode === 'dark' ? c.darkVars : c.lightVars);
                  const active = t.themeColor === c.value;
                  return (
                    <button
                      key={c.value}
                      onClick={() => t.setThemeColor(c.value)}
                      title={c.name}
                      className={cn(
                        'flex items-center gap-1.5 rounded-sm border px-1.5 py-1.5 transition-colors',
                        active ? 'border-brand bg-brand/10' : 'border-line bg-surface hover:border-line-strong',
                      )}
                    >
                      <span className="flex shrink-0 overflow-hidden rounded-xs border border-line-strong">
                        {sw.map((s, i) => <span key={i} className="size-3" style={{ background: s }} />)}
                      </span>
                      <span className={cn('min-w-0 flex-1 truncate text-left text-[11.5px]', active ? 'font-medium text-brand' : 'text-ink-2')}>
                        {c.name}
                      </span>
                    </button>
                  );
                })}
                {!colours.length && (
                  <p className="col-span-3 py-6 text-center text-[13px] text-dim">No palette named “{q}”.</p>
                )}
              </div>
            </>
          )}

          {tab === 'ground' && (
            <>
              <p className="mb-2.5 text-[12.5px] text-dim">
                Page and card shades for <span className="text-ink-2">{t.baseMode}</span> mode; the accent stays.
              </p>
              <div className="grid grid-cols-2 gap-1.5">
                {tones.map((s) => {
                  const active = t.surfaceTone === s.value;
                  return (
                    <button
                      key={s.value}
                      onClick={() => t.setSurfaceTone(s.value)}
                      className={cn(
                        'flex items-center gap-2 rounded-sm border px-2 py-1.5 text-left transition-colors',
                        active ? 'border-brand bg-brand/10' : 'border-line bg-surface hover:border-line-strong',
                      )}
                    >
                      <span className="size-5 shrink-0 rounded-xs border border-line-strong" style={{ background: `hsl(${s.preview})` }} />
                      <span className="min-w-0 flex-1">
                        <span className={cn('block truncate text-[12.5px]', active ? 'font-medium text-brand' : 'text-ink-2')}>{s.label}</span>
                        <span className="block truncate text-[11px] text-dim">{s.description}</span>
                      </span>
                    </button>
                  );
                })}
              </div>
            </>
          )}

          {tab === 'type' && (
            <div className="space-y-4">
              <div>
                <SectionTitle right={<span className="tnum font-mono text-[12px] text-brand">{t.radius}rem</span>}>
                  Corner radius
                </SectionTitle>
                <div className="flex gap-1.5">
                  {RADII.map((r) => (
                    <button
                      key={r}
                      onClick={() => t.setRadius(r)}
                      className={cn(
                        'flex flex-1 flex-col items-center gap-1.5 border px-1 py-2 transition-colors',
                        t.radius === r ? 'border-brand bg-brand/10 text-brand' : 'border-line bg-surface text-soft hover:border-line-strong',
                      )}
                      style={{ borderRadius: `${Math.max(r * 8, 2)}px` }}
                    >
                      <span className="size-4 border border-current" style={{ borderRadius: `${r * 10}px` }} />
                      <span className="tnum text-[11px]">{r}</span>
                    </button>
                  ))}
                </div>
              </div>

              <Divider />

              <div>
                <SectionTitle>Font family</SectionTitle>
                <div className="space-y-1">
                  {t.fontFamilies.map((f) => {
                    const active = t.fontFamily === f.value;
                    return (
                      <button
                        key={f.value}
                        onClick={() => t.setFontFamily(f.value as FontFamily)}
                        className={cn(
                          'flex w-full items-center justify-between gap-3 rounded-sm border px-2.5 py-1.5 text-left transition-colors',
                          active ? 'border-brand bg-brand/10' : 'border-line bg-surface hover:border-line-strong',
                        )}
                      >
                        <span className={cn('text-[13px]', active ? 'font-medium text-brand' : 'text-ink-2')}>{f.label}</span>
                        <span className="truncate text-[12.5px] text-dim" style={{ fontFamily: FONT_PREVIEW[f.value] }}>
                          {f.preview}
                        </span>
                      </button>
                    );
                  })}
                </div>
              </div>

              <Divider />

              <div>
                <SectionTitle>Base size</SectionTitle>
                <div className="grid grid-cols-4 gap-1.5">
                  {t.fontSizes.map((s) => {
                    const active = t.fontSize === s.value;
                    return (
                      <button
                        key={s.value}
                        onClick={() => t.setFontSize(s.value as FontSize)}
                        className={cn(
                          'rounded-sm border px-1.5 py-2 transition-colors',
                          active ? 'border-brand bg-brand/10 text-brand' : 'border-line bg-surface text-soft hover:border-line-strong',
                        )}
                      >
                        <span className="block text-[12px] font-medium">{s.label}</span>
                        <span className="tnum block text-[11px] text-dim">{s.size}</span>
                      </button>
                    );
                  })}
                </div>
              </div>

              <Divider />

              <div>
                <SectionTitle>Row density</SectionTitle>
                <Segmented
                  className="w-full"
                  options={[{ id: 'compact', label: 'Compact' }, { id: 'comfortable', label: 'Comfortable' }]}
                  value={t.density}
                  onChange={(v) => t.setDensity(v)}
                />
              </div>
            </div>
          )}
        </div>

        {/* Live preview strip */}
        <div className="shrink-0 border-t border-line bg-base px-4 py-3">
          <SectionTitle right={
            <Button size="xs" variant="ghost" onClick={reset}><RotateCcw className="size-3" />Reset</Button>
          }>Preview</SectionTitle>
          <div className="flex items-center gap-2">
            {/* Shapes, not figures: a preview of the palette should not look like a report on the workspace. */}
            <div className="flex-1 rounded-md border border-line bg-surface p-2.5" aria-hidden>
              <div className="text-[11px] font-medium text-dim">Card</div>
              <div className="mt-1.5 h-3 w-20 rounded-xs bg-ink/80" />
              <div className="mt-2 h-1 overflow-hidden rounded-full bg-surface-3">
                <div className="h-full w-2/3 rounded-full bg-brand" />
              </div>
            </div>
            <div className="flex flex-col gap-1.5">
              <span className="rounded-xs border border-ok/30 bg-ok/10 px-1.5 py-px text-[11.5px] font-medium text-ok">Pass</span>
              <span className="rounded-xs border border-warn/30 bg-warn/10 px-1.5 py-px text-[11.5px] font-medium text-warn">Review</span>
              <span className="rounded-xs border border-danger/30 bg-danger/10 px-1.5 py-px text-[11.5px] font-medium text-danger">Blocked</span>
            </div>
            <Button size="sm">Approve</Button>
          </div>
        </div>
      </SheetContent>
    </Sheet>
  );
}

const FONT_PREVIEW: Record<string, string> = {
  inter: '"Inter Variable", sans-serif',
  system: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
  serif: 'Charter, Georgia, "Times New Roman", serif',
  mono: 'ui-monospace, "SF Mono", monospace',
  poppins: 'Poppins, sans-serif',
  'dm-sans': '"DM Sans Variable", sans-serif',
};
