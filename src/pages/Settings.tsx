import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Check } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import {
  Page, PageHeader, PageBody, Panel, Mono, Ascii, SelectField, SectionTitle, KV,
} from '@/components/os';
import { useTheme, THEMES } from '@/lib/theme';
import { useAccess } from '@/lib/access';
import { useData } from '@/lib/data';
import { useAuth } from '@/lib/auth';
import { cn } from '@/lib/utils';
import { plural } from '@/lib/words';

/* Only what something reads. Appearance is this browser's; the routing policy, the lanes and their keys,
   roles and gates each have a screen of their own, which is where they are changed. */


export default function Settings() {
  const t = useTheme();
  const data = useData();
  const nav = useNavigate();
  const { roleNames, can, canAny } = useAuth();
  const { agents } = useAccess();
  const admin = can('workspace:admin');
  // Emptying the workspace is two clicks: the first arms the button for four seconds, the second does it.
  const [armed, setArmed] = useState(false);
  useEffect(() => {
    if (!armed) return;
    const id = window.setTimeout(() => setArmed(false), 4000);
    return () => window.clearTimeout(id);
  }, [armed]);
  const resetData = async () => {
    if (!armed) { setArmed(true); return; }
    setArmed(false);
    const done = await data.reset();
    if (!done) return;
    toast.success('Workspace emptied', {
      description: [
        done.backup ? `A backup was taken first: ${done.backup}, listed in Admin → Database.` : 'No backup was taken: pg_dump is not on this machine.',
        `${plural(done.counts.projects ?? 0, 'project')} and ${plural(done.counts.tasks ?? 0, 'task')} remain; the ${plural(done.counts.agents ?? 0, 'agent')} on the roster were kept.`,
      ].join(' '),
    });
  };

  const exported = useMemo(() => JSON.stringify({
    appearance: { preset: t.theme, mode: t.mode, palette: t.themeColor, ground: t.surfaceTone, rail: t.rail, radius: t.radius, font: t.fontFamily, size: t.fontSize },
  }, null, 2), [t]);

  const compiler = data.health?.compiler;

  return (
    <Page>
      <PageHeader
        title="Settings"
        subtitle="How NeuroCode looks in this browser, where its data lives, and the one reset that empties the workspace."
      />

      <PageBody>
        <div className="grid grid-cols-12 gap-4">
          <div className="col-span-12 space-y-3 2xl:col-span-8">
            <Panel className="accent-top" eyebrow="This browser · every control here changes the app immediately" title="Appearance">
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

            <Panel eyebrow="Changed on their own screens" title="Set elsewhere">
              <div className="divide-y divide-line/60">
                <Elsewhere what="Routing policy and lanes" where="Models & Router" onOpen={() => nav('/models')} />
                {admin && <Elsewhere what="Model keys and each lane's limits" where="AI providers" onOpen={() => nav('/admin/ai')} />}
                <Elsewhere what="What needs your approval" where="Permissions" onOpen={() => nav('/permissions')} />
                {canAny('roles:manage', 'users:manage') && <Elsewhere what="Who may do what" where="Roles & permissions" onOpen={() => nav('/admin/roles')} />}
              </div>
            </Panel>
          </div>

          <div className="col-span-12 space-y-3 2xl:col-span-4">
            <Panel eyebrow="Read-only" title="Config export">
              <Ascii className="max-h-[420px] overflow-auto">{exported}</Ascii>
            </Panel>
            <Panel eyebrow="Where this lives" title="Storage">
              <KV k="Appearance" v="this browser" mono />
              <KV k="Screen preferences" v="Postgres · prefs table" mono />
              <KV k="Project rules" v="Postgres · projects.rules" mono />
              <KV k="Model keys" v="server/secrets.json · owner-only" mono />
              <KV k="Memory" v="Postgres · full-text + pgvector" mono />
              <KV k="Operational data" v="Postgres · local API" mono />
              {data.health?.db && <KV k="Database" v={data.health.db} mono />}
              {compiler && (
                <KV k="Requirement compiler" v={compiler.note ?? (compiler.provider === 'rules' ? 'no model configured' : compiler.model)} />
              )}
            </Panel>
            <Panel eyebrow="Danger zone" title="Reset" className="border-danger/30">
              <p className="text-[12.5px] text-soft">
                Resetting appearance is instant and only touches this browser. Resetting the workspace empties projects, tasks,
                plans, runs, memory, approvals and the activity log, after taking a backup. People, roles, teams, keys, the
                agent roster, the usage ledger and the audit log are kept.
              </p>
              <div className="mt-2.5 flex flex-wrap gap-1.5">
                <Button size="xs" variant="outline" onClick={() => { t.setTheme('graphite'); t.setRail('tinted'); t.setRadius(0.5); t.setFontFamily('system'); t.setFontSize('default'); toast('Appearance reset'); }}>
                  Reset appearance
                </Button>
                {admin && (
                  <Button size="xs" variant="destructive" onClick={() => void resetData()}>
                    {armed ? 'Click again to confirm' : 'Reset workspace'}
                  </Button>
                )}
              </div>
              {!admin && <p className="mt-2 text-[12px] text-dim">Resetting the workspace needs workspace admin.</p>}
            </Panel>
            <Panel eyebrow="Build" title="About">
              <KV k="Version" v={<Mono>v{__APP_VERSION__}</Mono>} />
              <KV k="Your roles" v={roleNames || 'none'} />
              {agents.length > 0 && <KV k="Agents" v={`${agents.length} on the roster`} />}
            </Panel>
          </div>
        </div>
      </PageBody>
    </Page>
  );
}

function Elsewhere({ what, where, onOpen }: { what: string; where: string; onOpen: () => void }) {
  return (
    <div className="flex items-center justify-between gap-3 py-2.5">
      <span className="text-[13.5px] text-ink-2">{what}</span>
      <Button size="xs" variant="outline" onClick={onOpen}>Open {where}</Button>
    </div>
  );
}
