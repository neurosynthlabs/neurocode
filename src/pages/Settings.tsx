import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Check, Globe, Loader2, Search } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import {
  Page, PageHeader, PageBody, Panel, Mono, Ascii, SelectField, SectionTitle, KV, Field, Tag,
} from '@/components/os';
import { ApiError } from '@/lib/api';
import { useRemote } from '@/lib/remote';
import { webApi, WEB_KEY_PERMISSION, type WebPage, type WebSearch, type WebStatus } from '@/lib/live/web';
import { useTheme, THEMES } from '@/lib/theme';
import { useAccess } from '@/lib/access';
import { useData } from '@/lib/data';
import { useAuth } from '@/lib/auth';
import { cn } from '@/lib/utils';
import { plural } from '@/lib/words';

/* Only what something reads. Appearance is this browser's; the routing policy, the lanes and their keys,
   roles and gates each have a screen of their own, which is where they are changed. The web is here:
   the search key, and a place to try a search or read one page. */


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

            <WebPanel />

            <Panel eyebrow="Changed on their own screens" title="Set elsewhere">
              <div className="divide-y divide-line/60">
                <Elsewhere what="Routing policy and lanes" where="Models & Router" onOpen={() => nav('/models')} />
                {admin && <Elsewhere what="Model keys and each lane's limits" where="AI providers" onOpen={() => nav('/admin/ai')} />}
                <Elsewhere what="What needs your approval, and the tool rules" where="Permissions" onOpen={() => nav('/permissions')} />
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
              <KV k="Web search key" v="server/secrets.json · owner-only" mono />
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

const reason = (e: unknown) => (e instanceof ApiError ? e.message : 'The local API did not answer.');
const kb = (n: number) => (n < 1024 ? `${n} B` : `${Math.round(n / 1024)} KB`);

/** Web search: its key, and trying it. Every search and page passes the tool rules, and is in the activity log. */
function WebPanel() {
  const { can } = useAuth();
  const admin = can(WEB_KEY_PERMISSION);
  const mayTry = can('ai:use');
  const [version, setVersion] = useState(0);
  const status = useRemote(`web:${version}`, webApi.status);
  const [key, setKey] = useState('');
  const [saving, setSaving] = useState(false);
  const [q, setQ] = useState('');
  const [url, setUrl] = useState('');
  const [busy, setBusy] = useState<'search' | 'fetch' | null>(null);
  const [found, setFound] = useState<WebSearch | null>(null);
  const [page, setPage] = useState<WebPage | null>(null);
  const s: WebStatus | null = status.data;

  const saveKey = async (value: string) => {
    setSaving(true);
    try {
      const next = await webApi.setKey(value);
      setKey('');
      setVersion((v) => v + 1);
      toast.success(value ? 'Web search key saved' : 'Web search key removed', {
        description: value ? `Kept in the API's secrets file as ${next.keyMask}.` : 'Research can no longer search the web.',
      });
    } catch (e) {
      toast.error('Key not saved', { description: reason(e) });
    } finally {
      setSaving(false);
    }
  };
  const trySearch = async () => {
    setBusy('search');
    try {
      setFound(await webApi.search(q.trim()));
    } catch (e) {
      setFound(null);
      toast.error('No search', { description: reason(e) });
    } finally {
      setBusy(null);
    }
  };
  const tryFetch = async () => {
    setBusy('fetch');
    try {
      setPage(await webApi.fetch(url.trim()));
    } catch (e) {
      setPage(null);
      toast.error('Page not read', { description: reason(e) });
    } finally {
      setBusy(null);
    }
  };

  return (
    <Panel eyebrow={s ? `${s.label} · the tool rules apply to every search and page` : 'Search and pages'}
      title={<span className="flex items-center gap-1.5"><Globe className="size-3.5 text-brand" />Web</span>}
      actions={s && <Tag tone={s.configured ? 'ok' : 'neutral'}>{s.configured ? 'search configured' : 'search not configured'}</Tag>}>
      {status.loading ? (
        <p className="text-[13px] text-dim"><Loader2 className="mr-2 inline size-3.5 animate-spin" />asking the API…</p>
      ) : status.error || !s ? (
        <p className="text-[13px] text-danger">{status.error ?? 'No answer.'}</p>
      ) : (
        <>
          <p className="text-[12.5px] text-ink-2">
            Research can search the web for each angle and read the top pages when you tick “The web”. Search goes
            through the {s.label}; its key is kept beside the model keys and never shown again.
          </p>
          {admin ? (
            <div className="mt-3 flex flex-wrap items-end gap-2">
              <Field className="min-w-0 flex-1 basis-56" label={s.keyMask ? `Key · ${s.keyMask}` : 'Key'} type="password"
                value={key} onChange={setKey} placeholder={s.keyMask ? 'Paste a new key to replace it' : 'BSA…'}
                autoComplete="off" mono
                hint={<>Made in the <a className="text-brand hover:underline" href={s.keysAt} target="_blank" rel="noreferrer noopener">Brave Search API dashboard ↗</a>. Setting it is written to the audit log.</>} />
              <div className="flex gap-1.5 pb-6">
                <Button size="sm" disabled={!key.trim() || saving} onClick={() => void saveKey(key.trim())}>
                  {saving && <Loader2 className="size-3.5 animate-spin" />}Save key
                </Button>
                {s.keyMask && <Button size="sm" variant="outline" disabled={saving} onClick={() => void saveKey('')}>Remove</Button>}
              </div>
            </div>
          ) : (
            <p className="mt-2 text-[12px] text-dim">
              {s.configured ? 'An admin has set the key.' : `Setting the key needs ${WEB_KEY_PERMISSION}.`}
            </p>
          )}

          <div className="mt-4 grid grid-cols-1 gap-4 border-t border-line pt-3 md:grid-cols-2">
            <div className="min-w-0">
              <SectionTitle>Try a search</SectionTitle>
              <div className="flex items-end gap-2">
                <Field className="min-w-0 flex-1" value={q} onChange={setQ} placeholder="postgres advisory locks"
                  disabled={!s.configured || !mayTry} />
                <Button size="sm" variant="outline" disabled={!s.configured || !mayTry || !q.trim() || busy !== null}
                  title={!s.configured ? 'Add a key first.' : !mayTry ? 'Needs ai:use.' : undefined} onClick={() => void trySearch()}>
                  {busy === 'search' ? <Loader2 className="size-3.5 animate-spin" /> : <Search className="size-3.5" />}Search
                </Button>
              </div>
              {found && (
                <div className="mt-2 space-y-2">
                  {found.results.length === 0 ? <p className="text-[12.5px] text-dim">No results for “{found.query}”.</p>
                    : found.results.map((r) => (
                      <div key={r.url} className="min-w-0">
                        <a href={r.url} target="_blank" rel="noreferrer noopener" className="block truncate text-[13px] text-brand hover:underline">{r.title}</a>
                        <Mono className="block truncate">{r.url}</Mono>
                        {r.snippet && <p className="line-clamp-2 text-[12px] text-soft">{r.snippet}</p>}
                      </div>
                    ))}
                  <p className="text-[11.5px] text-dim">{found.decision.why}</p>
                </div>
              )}
            </div>
            <div className="min-w-0">
              <SectionTitle>Read a page</SectionTitle>
              <div className="flex items-end gap-2">
                <Field className="min-w-0 flex-1" value={url} onChange={setUrl} placeholder="https://www.postgresql.org/docs/" mono disabled={!mayTry} />
                <Button size="sm" variant="outline" disabled={!mayTry || !url.trim() || busy !== null}
                  title={!mayTry ? 'Needs ai:use.' : undefined} onClick={() => void tryFetch()}>
                  {busy === 'fetch' && <Loader2 className="size-3.5 animate-spin" />}Read
                </Button>
              </div>
              {page && (
                <div className="mt-2 min-w-0">
                  <p className="truncate text-[13px] text-ink">{page.title || page.url}</p>
                  <p className="text-[11.5px] text-dim">
                    HTTP {page.status} · {page.contentType} · {kb(page.bytes)}{page.truncated ? ' · cut' : ''}
                    {page.hops ? ` · ${page.hops} redirect${page.hops > 1 ? 's' : ''}` : ''}
                  </p>
                  <Ascii className="mt-1.5 max-h-[200px] overflow-auto whitespace-pre-wrap">{page.text.slice(0, 2000) || '(no text on the page)'}</Ascii>
                </div>
              )}
              <p className="mt-2 text-[11.5px] text-dim">
                http and https only, never a local or private address; up to {s.fetch.maxHops} redirects, each checked
                again; {Math.round(s.fetch.maxBytes / 1048576)} MB and {s.fetch.timeoutS} s at most.
              </p>
            </div>
          </div>
        </>
      )}
    </Panel>
  );
}
