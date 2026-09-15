import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { RotateCcw, Check } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/switch';
import {
  Page, PageHeader, PageBody, Panel, Tag, Mono, Ascii, SelectField, Field, SectionTitle, KV,
} from '@/components/os';
import { settingsGroups } from '@/mock/settings';
import { useTheme, THEMES } from '@/lib/theme';
import { categoryMeta } from '@/mock/memory';
import { useData, usePref } from '@/lib/data';
import { useAuth } from '@/lib/auth';
import { cn } from '@/lib/utils';

const SETTING_DEFAULTS: Record<string, string | boolean> = Object.fromEntries(
  settingsGroups.flatMap((g) => g.items.map((i) => [i.id, i.value])),
);

export default function Settings() {
  const t = useTheme();
  const data = useData();
  const nav = useNavigate();
  const { roleNames } = useAuth();
  // Resetting data is two clicks: the first arms the button for four seconds, the second does it.
  const [armed, setArmed] = useState(false);
  useEffect(() => {
    if (!armed) return;
    const id = window.setTimeout(() => setArmed(false), 4000);
    return () => window.clearTimeout(id);
  }, [armed]);
  const resetData = async () => {
    if (!armed) { setArmed(true); return; }
    setArmed(false);
    if (await data.reset()) {
      toast.success(data.mode === 'live' ? 'Database restored to the seed' : 'Demo data restored',
        { description: 'Approvals, tasks, memory and the activity log are back to where they started.' });
    }
  };
  const [group, setGroup] = useState(settingsGroups[0].id);
  const [vals, setVals] = usePref('settings.values', SETTING_DEFAULTS);

  const g = useMemo(() => settingsGroups.find((x) => x.id === group) ?? settingsGroups[0], [group]);
  // A switch or a choice leaves an audit line; free text is saved without logging every keystroke.
  const set = (id: string, v: string | boolean) => {
    const item = settingsGroups.flatMap((x) => x.items).find((i) => i.id === id);
    const shown = typeof v === 'boolean' ? (v ? 'on' : 'off') : v;
    setVals({ ...vals, [id]: v }, item && item.kind !== 'text' ? `${item.label} → ${shown}` : undefined);
  };

  const exported = useMemo(() => JSON.stringify({
    appearance: { preset: t.theme, mode: t.mode, palette: t.themeColor, ground: t.surfaceTone, rail: t.rail, radius: t.radius, font: t.fontFamily, size: t.fontSize },
    ...Object.fromEntries(settingsGroups.map((x) => [x.id, Object.fromEntries(x.items.map((i) => [i.id.split('.').pop()!, vals[i.id]]))])),
  }, null, 2), [t, vals]);

  return (
    <Page>
      <PageHeader
        title="Settings"
        subtitle="Everything the OS will do without asking, and everything it will always stop for."
        actions={<Button size="sm" variant="outline" onClick={() => { setVals({ ...vals, ...Object.fromEntries(g.items.map((i) => [i.id, i.value])) }, `${g.name} settings reset to defaults`); toast('Defaults restored for this group'); }}><RotateCcw className="size-3.5" />Reset group</Button>}
      />

      <PageBody className="flex h-full flex-col gap-0 p-0 md:flex-row">
        {/* Group rail */}
        <div className="no-scrollbar w-full shrink-0 max-h-[42vh] md:max-h-none md:w-[212px] overflow-y-auto border-b border-line md:border-b-0 md:border-r py-2">
          {settingsGroups.map((x) => (
            <button
              key={x.id}
              onClick={() => setGroup(x.id)}
              className={cn('flex w-full items-center justify-between px-3.5 py-[7px] text-left text-[13.5px] transition-colors',
                group === x.id ? 'bg-surface-2 font-medium text-ink' : 'text-soft hover:text-ink-2')}
            >
              <span className="truncate">{x.name}</span>
              <span className="tnum text-[11.5px] text-dim">{x.items.length}</span>
            </button>
          ))}
        </div>

        <div className="min-w-0 flex-1 overflow-y-auto p-5">
          <div className="grid grid-cols-12 gap-4">
            <div className="col-span-12 space-y-3 2xl:col-span-8">
              {/* Appearance is the one group wired end to end */}
              {group === 'appearance' ? (
                <>
                  <Panel className="accent-top" eyebrow="Live — every control here changes the app immediately" title="Appearance">
                    <SectionTitle>Preset</SectionTitle>
                    <div className="grid grid-cols-2 gap-1.5 lg:grid-cols-3">
                      {THEMES.map((p) => (
                        <button key={p.id} onClick={() => t.setTheme(p.id)}
                          className={cn('hover-lift flex items-center gap-2.5 rounded-sm border px-2.5 py-2 text-left',
                            t.theme === p.id ? 'border-brand bg-brand/8' : 'border-line bg-surface')}>
                          <span className="flex shrink-0 overflow-hidden rounded-xs border border-line-strong">
                            {p.swatch.map((c, i) => <span key={i} className="size-4" style={{ background: c }} />)}
                          </span>
                          <span className="min-w-0 flex-1">
                            <span className="flex items-center gap-1.5">
                              <span className="text-[13px] font-medium text-ink">{p.name}</span>
                              <span className="eyebrow">{p.mode}</span>
                            </span>
                            <span className="block truncate text-[11.5px] text-dim">{p.note}</span>
                          </span>
                          {t.theme === p.id && <Check className="size-3.5 shrink-0 text-brand" />}
                        </button>
                      ))}
                    </div>

                    <div className="mt-4 grid grid-cols-1 gap-4 border-t border-line pt-3 md:grid-cols-2">
                      <div>
                        <SectionTitle>Mode</SectionTitle>
                        <SelectField value={t.mode} onChange={(v) => t.setMode(v as typeof t.mode)}
                          options={['light', 'dark', 'dim', 'midnight', 'sepia', 'high-contrast', 'system']} />
                        <SectionTitle className="mt-3">Palette · {t.availableColors.length} available</SectionTitle>
                        <SelectField value={t.themeColor} onChange={t.setThemeColor}
                          options={t.availableColors.map((c) => ({ value: c.value, label: c.name }))} />
                        <SectionTitle className="mt-3">Ground · {t.surfaceTones.filter((s) => s.mode === t.baseMode).length} for {t.baseMode}</SectionTitle>
                        <SelectField value={t.surfaceTone} onChange={t.setSurfaceTone}
                          options={t.surfaceTones.filter((s) => s.mode === t.baseMode).map((s) => ({ value: s.value, label: `${s.label} — ${s.description}` }))} />
                      </div>
                      <div>
                        <SectionTitle>Rail style</SectionTitle>
                        <SelectField value={t.rail} onChange={(v) => t.setRail(v as typeof t.rail)}
                          options={[{ value: 'tinted', label: 'Tinted — palette hue in the ground' }, { value: 'solid', label: 'Solid — full accent slab' }, { value: 'flush', label: 'Flush — edge to edge' }]} />
                        <SectionTitle className="mt-3">Corner radius · {t.radius}rem</SectionTitle>
                        <div className="flex gap-1.5">
                          {[0, 0.25, 0.375, 0.5, 0.75, 1].map((r) => (
                            <button key={r} onClick={() => t.setRadius(r)}
                              className={cn('flex-1 border px-1 py-1.5 text-[11.5px] transition-colors',
                                t.radius === r ? 'border-brand bg-brand/10 text-brand' : 'border-line bg-surface text-soft')}
                              style={{ borderRadius: `${Math.max(r * 8, 2)}px` }}>{r}</button>
                          ))}
                        </div>
                        <SectionTitle className="mt-3">Font</SectionTitle>
                        <SelectField value={t.fontFamily} onChange={(v) => t.setFontFamily(v as typeof t.fontFamily)}
                          options={t.fontFamilies.map((f) => ({ value: f.value, label: f.label }))} />
                        <SectionTitle className="mt-3">Base size</SectionTitle>
                        <SelectField value={t.fontSize} onChange={(v) => t.setFontSize(v as typeof t.fontSize)}
                          options={t.fontSizes.map((f) => ({ value: f.value, label: `${f.label} — ${f.size}` }))} />
                      </div>
                    </div>
                  </Panel>
                </>
              ) : (
                <Panel eyebrow={`${g.items.length} settings`} title={g.name} flush>
                  <div className="divide-y divide-line">
                    {g.items.map((i) => (
                      <div key={i.id} className="flex items-start justify-between gap-6 px-3.5 py-3">
                        <div className="min-w-0 flex-1">
                          <div className="text-[13.5px] font-medium text-ink">{i.label}</div>
                          <p className="mt-0.5 text-[12.5px] text-soft">{i.description}</p>
                        </div>
                        <div className="w-56 shrink-0">
                          {i.kind === 'toggle' && (
                            <div className="flex justify-end">
                              <Switch checked={!!vals[i.id]} onCheckedChange={(v) => set(i.id, v)} />
                            </div>
                          )}
                          {i.kind === 'select' && (
                            <SelectField value={String(vals[i.id])} onChange={(v) => set(i.id, v)} options={i.options ?? []} />
                          )}
                          {i.kind === 'text' && (
                            <Field value={String(vals[i.id])} onChange={(v) => set(i.id, v)} mono />
                          )}
                          {i.kind === 'secret' && (i.id === 'key.deepseek' ? (
                            <Button size="sm" variant="outline" className="w-full" onClick={() => nav('/admin/ai')}>Manage in AI providers</Button>
                          ) : (
                            <div className="flex h-9 items-center justify-end">
                              <Tag tone="neutral">Arrives with its integration</Tag>
                            </div>
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>
                </Panel>
              )}

              {group === 'memory' && (
                <Panel eyebrow="Per-category half-life" title="What decays and what does not" flush>
                  <div className="divide-y divide-line">
                    {categoryMeta.map((c) => (
                      <div key={c.id} className="flex items-center gap-3 px-3.5 py-1.5">
                        <span className="w-32 shrink-0 text-[13px] text-ink-2">{c.label}</span>
                        <span className="tnum w-16 shrink-0 font-mono text-[12px] text-soft">{c.halfLifeDays >= 3650 ? '∞' : `${c.halfLifeDays}d`}</span>
                        <span className="tnum w-10 shrink-0 font-mono text-[12px] text-ok">+{c.reinforce}</span>
                        <span className="min-w-0 flex-1 truncate text-[12px] text-dim">{c.note}</span>
                      </div>
                    ))}
                  </div>
                </Panel>
              )}
            </div>

            {/* Rail */}
            <div className="col-span-12 space-y-3 2xl:col-span-4">
              <Panel eyebrow="Read-only" title="Config export">
                <Ascii className="max-h-[420px] overflow-auto">{exported}</Ascii>
              </Panel>
              <Panel eyebrow="Where this lives" title="Storage">
                <KV k="Preferences" v={data.mode === 'live' ? 'Postgres · prefs table' : 'this tab only'} mono />
                <KV k="Project rules" v=".os/rules/*.md" mono />
                <KV k="Model keys" v="server/secrets.json · owner-only" mono />
                <KV k="Memory" v={data.mode === 'live' ? 'Postgres · full-text + pgvector' : 'seed data · this tab only'} mono />
                <KV k="Operational data" v={data.mode === 'live' ? 'Postgres · local API' : 'seed data · this tab only'} mono />
                {data.health?.db && <KV k="Database" v={data.health.db} mono />}
                {data.health?.compiler && (
                  <KV k="Requirement compiler" v={data.health.compiler.note ?? (data.health.compiler.provider === 'rules' ? 'offline planner' : data.health.compiler.model)} />
                )}
              </Panel>
              <Panel eyebrow="Danger zone" title="Reset" className="border-danger/30">
                <p className="text-[12.5px] text-soft">
                  Resetting appearance is instant and safe. Resetting memory or permissions is not — those hold everything
                  the OS has learned about your codebase and what it is allowed to do with it.
                </p>
                <div className="mt-2.5 flex flex-wrap gap-1.5">
                  <Button size="xs" variant="outline" onClick={() => { t.setTheme('graphite'); t.setRail('tinted'); t.setRadius(0.5); t.setFontFamily('system'); t.setFontSize('default'); toast('Appearance reset'); }}>
                    Reset appearance
                  </Button>
                  <Button size="xs" variant="destructive" onClick={() => toast('Refused — memory reset needs a typed confirmation in the real system')}>
                    Reset memory
                  </Button>
                  <Button size="xs" variant="destructive" onClick={resetData}>
                    {armed ? 'Click again to confirm' : data.mode === 'live' ? 'Reset database' : 'Reset demo data'}
                  </Button>
                </div>
              </Panel>
              <Panel eyebrow="Build" title="About">
                <KV k="Version" v={<Mono>v{__APP_VERSION__}</Mono>} />
                <KV k="Runtime" v="local · self-hosted" />
                <KV k="Your roles" v={roleNames || '—'} />
                <KV k="Agents" v="12 registered" />
              </Panel>
            </div>
          </div>
        </div>
      </PageBody>
    </Page>
  );
}
