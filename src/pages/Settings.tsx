import { useEffect, useMemo, useState, type SyntheticEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { Check, Copy, Globe, KeyRound, Loader2, Plus, Search, Terminal } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import {
  More, Page, PageHeader, PageBody, Panel, Mono, Ascii, SelectField, SectionTitle, KV, Field, Tag,
} from '@/components/os';
import { API_BASE, ApiError } from '@/lib/api';
import { useRemote } from '@/lib/remote';
import { webApi, WEB_KEY_PERMISSION, type WebPage, type WebSearch, type WebStatus } from '@/lib/live/web';
import { channelsApi, type Channels, type LinkCode } from '@/lib/live/channels';
import {
  tokensApi, MACHINE_PERMISSION, type AccessToken, type MadeToken, type TokenPage,
} from '@/lib/live/tokens';
import { useTheme, THEMES } from '@/lib/theme';
import { permissionLabel, useAccess } from '@/lib/access';
import { useData } from '@/lib/data';
import { useAuth } from '@/lib/auth';
import { cn } from '@/lib/utils';
import { plural } from '@/lib/words';
import { ago, at } from '@/lib/time';

/* Only what something reads. Appearance is this browser's; the routing policy, the lanes and their keys,
   roles and gates each have a screen of their own, which is where they are changed. The web is here:
   the search key, and a place to try a search or read one page. So are your access tokens, for the `nc`
   terminal client and your scripts. */


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
        done.backup ? `Backup first: ${done.backup}, in Admin → Database.` : 'No backup: pg_dump is not on this machine.',
        `${plural(done.counts.projects ?? 0, 'project')} and ${plural(done.counts.tasks ?? 0, 'task')} remain; ${plural(done.counts.agents ?? 0, 'agent')} kept.`,
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
        subtitle="Appearance, notifications, web search, tokens and reset."
        about={<p>Routing, model keys, approvals and roles each have a screen of their own, linked under Set elsewhere.</p>}
      />

      <PageBody>
        <div className="grid grid-cols-12 gap-4">
          <div className="col-span-12 space-y-3 2xl:col-span-8">
            <Panel className="accent-top" eyebrow="This browser" title="Appearance">
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
                        <span className="text-[11px] text-dim">{p.mode}</span>
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

            <NotificationsPanel />

            <TokensPanel />

            <Panel title="Set elsewhere">
              <div className="divide-y divide-line/60">
                <Elsewhere what="Routing policy and lanes" where="Models" onOpen={() => nav('/models')} />
                {admin && <Elsewhere what="Model keys and lane limits" where="Models → Keys" onOpen={() => nav('/models?tab=keys')} />}
                <Elsewhere what="Approvals and tool rules" where="Permissions" onOpen={() => nav('/permissions')} />
                {canAny('roles:manage', 'users:manage') && <Elsewhere what="Who may do what" where="Roles" onOpen={() => nav('/admin/roles')} />}
              </div>
            </Panel>
          </div>

          <div className="col-span-12 space-y-3 2xl:col-span-4">
            <Panel eyebrow="Read-only" title="Config export">
              <More label="Show JSON"><Ascii className="max-h-[420px] overflow-auto">{exported}</Ascii></More>
            </Panel>
            <Panel title="Storage">
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
            <Panel eyebrow="Danger zone" title="Reset" className="border-danger/30"
              about={<><p>Appearance resets in this browser only.</p><p>The workspace reset takes a backup, then empties projects, tasks,
                plans, runs, memory, approvals and the activity log.</p><p>People, roles, teams, keys, agents, the usage ledger
                and the audit log are kept.</p></>}>
              <p className="text-[12.5px] text-soft">Workspace reset empties all work, after a backup.</p>
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
              {!admin && <p className="mt-2 text-[12px] text-dim">Workspace reset needs workspace admin.</p>}
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
      <span className="min-w-0 text-[13.5px] text-ink-2">{what} <span className="text-dim">· {where}</span></span>
      <Button size="xs" variant="outline" aria-label={`Open ${where}`} onClick={onOpen}>Open</Button>
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
    <Panel eyebrow={s ? s.label : 'Search and pages'}
      title={<span className="flex items-center gap-1.5"><Globe className="size-3.5 text-brand" />Web</span>}
      about={<><p>Research searches the web for each angle and reads the top pages when “The web” is ticked.</p>
        <p>The key is kept beside the model keys and never shown again. The tool rules apply to every search and page.</p></>}
      actions={s && <Tag tone={s.configured ? 'ok' : 'neutral'}>{s.configured ? 'search configured' : 'search not configured'}</Tag>}>
      {status.loading ? (
        <p className="text-[13px] text-dim"><Loader2 className="mr-2 inline size-3.5 animate-spin" />asking the API…</p>
      ) : status.error || !s ? (
        <p className="text-[13px] text-danger">{status.error ?? 'No answer.'}</p>
      ) : (
        <>
          {admin ? (
            <div className="flex flex-wrap items-end gap-2">
              <Field className="min-w-0 flex-1 basis-56" label={s.keyMask ? `Key · ${s.keyMask}` : 'Key'} type="password"
                value={key} onChange={setKey} placeholder={s.keyMask ? 'Paste to replace' : 'BSA…'}
                autoComplete="off" mono
                hint={<>From the <a className="text-brand hover:underline" href={s.keysAt} target="_blank" rel="noreferrer noopener">Brave Search API dashboard ↗</a>. Changes are audit-logged.</>} />
              <div className="flex gap-1.5 pb-6">
                <Button size="sm" disabled={!key.trim() || saving} onClick={() => void saveKey(key.trim())}>
                  {saving && <Loader2 className="size-3.5 animate-spin" />}Save key
                </Button>
                {s.keyMask && <Button size="sm" variant="outline" disabled={saving} onClick={() => void saveKey('')}>Remove</Button>}
              </div>
            </div>
          ) : (
            <p className="text-[12px] text-dim">
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
                Public http(s) only · {s.fetch.maxHops} redirects, each checked · {Math.round(s.fetch.maxBytes / 1048576)} MB · {s.fetch.timeoutS} s
              </p>
            </div>
          </div>
        </>
      )}
    </Panel>
  );
}

/* ── Access tokens ─────────────────────────────────────────────── */

/** How long a new token lasts, as offered. The API takes 1–366 days, or none: until revoked. */
const LIFETIMES: { value: string; label: string }[] = [
  { value: '30', label: '30 days' },
  { value: '90', label: '90 days' },
  { value: '7', label: '7 days' },
  { value: '365', label: 'A year' },
  { value: 'never', label: 'Until I revoke it' },
];

/** Where `nc login` should point: this web app, whose API is under /api — or the API itself when it lives elsewhere. */
function loginLine(): string {
  const origin = window.location.origin;
  return /^https?:\/\//.test(API_BASE) ? `nc login ${API_BASE} --web ${origin}` : `nc login ${origin}`;
}

const STATE_TONE = { active: 'ok', expired: 'neutral', revoked: 'danger' } as const;

/** Your personal access tokens: made here (shown once), listed with when each was last used, revoked in two clicks. */
/* Telegram: the gates that need you, on your phone. An admin adds the bot once; each person links their own
   chat with a one-time code, so a chat is only ever the account that made the code. */
function NotificationsPanel() {
  const { can } = useAuth();
  const admin = can('workspace:admin');
  const [version, setVersion] = useState(0);
  const status = useRemote<Channels>(`channels:${version}`, () => channelsApi.status());
  const [token, setToken] = useState('');
  const [busy, setBusy] = useState(false);
  const [code, setCode] = useState<LinkCode | null>(null);
  const tg = status.data?.telegram;
  const waiting = !!code && !!tg && !tg.linked;
  // While a code is out, ask every few seconds whether the bot has heard it.
  useEffect(() => {
    if (!waiting) return;
    const id = window.setInterval(() => setVersion((v) => v + 1), 3000);
    return () => window.clearInterval(id);
  }, [waiting]);

  const act = async (fn: () => Promise<unknown>, done: string) => {
    setBusy(true);
    try {
      await fn();
      toast.success(done);
      setVersion((v) => v + 1);
      return true;
    } catch (e) {
      toast.error('Not saved', { description: reason(e) });
      return false;
    } finally {
      setBusy(false);
    }
  };
  const saveToken = async (e: SyntheticEvent) => {
    e.preventDefault();
    if (await act(() => channelsApi.setToken(token.trim()), 'Bot saved')) setToken('');
  };
  const link = async () => {
    setBusy(true);
    try {
      setCode(await channelsApi.link());
    } catch (e) {
      toast.error('No code made', { description: reason(e) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel title="Notifications"
      about={<><p>Gates you may decide arrive in Telegram with their answers as buttons.</p><p>A signature or a question opens here, where the diff is.</p></>}>
      {status.error ? <p className="text-[13px] text-danger">{status.error}</p> : !tg ? (
        <Loader2 className="size-4 animate-spin text-dim" />
      ) : (
        <div className="space-y-3">
          <KV k="Telegram" v={!tg.configured ? <Tag>Not set up</Tag> : (
            <span className="inline-flex flex-wrap items-center gap-2">
              <Mono>{tg.bot}</Mono>
              {tg.linked ? <Tag tone="ok">Linked{tg.chat ? ` · @${tg.chat}` : ''}</Tag> : <Tag>Not linked</Tag>}
            </span>
          )} />
          {tg.configured && !tg.linked && (code ? (
            <div className="space-y-1.5 text-[13px] text-ink-2">
              <p>Send this to {tg.bot} within ten minutes:</p>
              <div className="flex flex-wrap items-center gap-2">
                <Mono>/start {code.code}</Mono>
                <a href={code.url} target="_blank" rel="noopener noreferrer" className="text-brand hover:underline">Open Telegram</a>
                <Loader2 className="size-3.5 animate-spin text-dim" />
              </div>
            </div>
          ) : (
            <Button size="sm" onClick={() => void link()} disabled={busy}>Link Telegram</Button>
          ))}
          {tg.linked && (
            <Button size="sm" variant="outline" disabled={busy}
              onClick={() => { setCode(null); void act(() => channelsApi.unlink(), 'Telegram unlinked'); }}>Unlink</Button>
          )}
          {admin && (
            <More label={tg.configured ? 'Bot token' : 'Set up the bot'} open={!tg.configured}>
              <form onSubmit={saveToken} className="flex flex-col gap-2 sm:flex-row sm:items-end">
                <Field className="flex-1" label="Bot token" type="password" mono value={token} onChange={setToken}
                  placeholder="123456789:AA…" autoComplete="off" icon={<KeyRound className="size-3.5" />} />
                <Button type="submit" disabled={busy || !token.trim()}>Save token</Button>
                {tg.configured && (
                  <Button type="button" variant="ghost" className="text-danger hover:text-danger" disabled={busy}
                    onClick={() => void act(() => channelsApi.setToken(''), 'Telegram turned off')}>Turn off</Button>
                )}
              </form>
              <p className="mt-2 text-[12px] text-dim">
                Make a bot with @BotFather and paste its token.{typeof tg.linkedPeople === 'number' ? ` ${plural(tg.linkedPeople, 'person', 'people')} linked.` : ''}
              </p>
            </More>
          )}
        </div>
      )}
    </Panel>
  );
}

function TokensPanel() {
  const { catalogue } = useAccess();
  const [version, setVersion] = useState(0);
  const [extra, setExtra] = useState<AccessToken[]>([]);
  const [next, setNext] = useState<number | null>(null);
  const [more, setMore] = useState(false);
  const [making, setMaking] = useState(false);
  const [armed, setArmed] = useState<string | null>(null);
  const [revoking, setRevoking] = useState<string | null>(null);
  const page = useRemote<TokenPage>(`tokens:${version}`, () => tokensApi.list());
  useEffect(() => {
    if (!armed) return;
    const id = window.setTimeout(() => setArmed(null), 4000);
    return () => window.clearTimeout(id);
  }, [armed]);

  const reload = () => { setExtra([]); setNext(null); setVersion((v) => v + 1); };
  const items = [...(page.data?.items ?? []), ...extra];
  const nextOffset = next ?? page.data?.nextOffset ?? null;
  const live = items.filter((t) => t.state === 'active').length;

  const loadMore = async () => {
    if (nextOffset === null) return;
    setMore(true);
    try {
      const found = await tokensApi.list(nextOffset);
      setExtra((e) => [...e, ...found.items]);
      setNext(found.nextOffset ?? -1);
    } catch (e) {
      toast.error('More tokens did not load', { description: reason(e) });
    } finally {
      setMore(false);
    }
  };
  const revoke = async (t: AccessToken) => {
    if (armed !== t.id) { setArmed(t.id); return; }
    setArmed(null);
    setRevoking(t.id);
    try {
      await tokensApi.revoke(t.id);
      toast.success(`${t.name} revoked`, { description: 'Anything still using it is refused from now on.' });
      reload();
    } catch (e) {
      toast.error('Not revoked', { description: reason(e) });
    } finally {
      setRevoking(null);
    }
  };

  return (
    <Panel eyebrow="For nc and scripts"
      title={<span className="flex items-center gap-1.5"><KeyRound className="size-3.5 text-brand" />Access tokens</span>}
      about={<><p>A token signs in as you, limited to the permissions it names and never beyond yours.</p>
        <p>With none named it carries all you hold except <Mono>{MACHINE_PERMISSION}</Mono>. Only a token that names it opens a terminal here.</p>
        <p><Mono>nc login</Mono> makes one for its machine and keeps it in the keychain. Making and revoking is audit-logged.</p></>}
      actions={<Button size="sm" variant="outline" onClick={() => setMaking(true)}><Plus className="size-3.5" />New token</Button>}>
      <div className="flex min-w-0 flex-wrap items-center gap-2 text-[12.5px] text-soft">
        <Terminal className="size-3.5 shrink-0 text-dim" />
        <span>From a terminal</span>
        <Mono className="min-w-0 [overflow-wrap:anywhere]">{loginLine()}</Mono>
      </div>

      <div className="mt-4 border-t border-line pt-1">
        {page.loading ? (
          <p className="py-3 text-[13px] text-dim"><Loader2 className="mr-2 inline size-3.5 animate-spin" />asking the API…</p>
        ) : page.error ? (
          <div className="flex flex-wrap items-center gap-2 py-3">
            <p className="text-[13px] text-danger">{page.error}</p>
            <Button size="xs" variant="outline" onClick={reload}>Try again</Button>
          </div>
        ) : items.length === 0 ? (
          <p className="py-3 text-[13px] text-dim">No tokens yet.</p>
        ) : (
          <>
            <div className="divide-y divide-line/60">
              {items.map((t) => (
                <div key={t.id} className={cn('flex flex-wrap items-center gap-x-4 gap-y-1.5 py-2.5', t.state !== 'active' && 'opacity-70')}>
                  <div className="min-w-0 flex-1 basis-56">
                    <div className="flex min-w-0 flex-wrap items-center gap-2">
                      <span className="truncate text-[13.5px] font-medium text-ink">{t.name}</span>
                      <Mono>{t.prefix}…</Mono>
                      {t.state !== 'active' && <Tag tone={STATE_TONE[t.state]}>{t.state}</Tag>}
                    </div>
                    <p className="mt-0.5 text-[12px] text-dim [overflow-wrap:anywhere]">
                      {t.allScopes
                        ? 'Everything you hold, except machine access'
                        : t.scopes.map((s) => permissionLabel(catalogue, s)).join(' · ')}
                    </p>
                  </div>
                  <div className="text-[12px] text-soft">
                    <div>Last used {t.lastUsedAt ? ago(t.lastUsedAt) : 'never'}</div>
                    <div className="text-dim">
                      Made {ago(t.createdAt)} ·{' '}
                      {t.revokedAt ? `revoked ${ago(t.revokedAt)}` : t.expiresAt
                        ? `${t.state === 'expired' ? 'expired' : 'expires'} ${at(t.expiresAt)}` : 'no expiry'}
                    </div>
                  </div>
                  {t.state === 'active' && (
                    <Button size="xs" variant={armed === t.id ? 'destructive' : 'outline'} disabled={revoking === t.id}
                      onClick={() => void revoke(t)}>
                      {revoking === t.id && <Loader2 className="size-3.5 animate-spin" />}
                      {armed === t.id ? 'Click again to revoke' : 'Revoke'}
                    </Button>
                  )}
                </div>
              ))}
            </div>
            <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-[12px] text-dim">
              <span>{plural(live, 'live token')} of {page.data?.total ?? items.length}</span>
              {nextOffset !== null && nextOffset >= 0 && (
                <Button size="xs" variant="outline" disabled={more} onClick={() => void loadMore()}>
                  {more && <Loader2 className="size-3.5 animate-spin" />}Show more
                </Button>
              )}
            </div>
          </>
        )}
      </div>

      <MakeToken open={making} onOpenChange={setMaking} onMade={reload} />
    </Panel>
  );
}

/** The new-token dialog: a name, how long it lasts, what it may do — then the token, once. */
function MakeToken({ open, onOpenChange, onMade }: {
  open: boolean; onOpenChange: (open: boolean) => void; onMade: () => void;
}) {
  const { user } = useAuth();
  const { catalogue } = useAccess();
  const [name, setName] = useState('');
  const [lifetime, setLifetime] = useState('90');
  const [limited, setLimited] = useState(false);
  const [picked, setPicked] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [made, setMade] = useState<MadeToken | null>(null);

  // Only what you hold can be given to a token, in the catalogue's order, grouped as the access screen groups them.
  const held = useMemo(() => {
    const mine = new Set(user?.permissions ?? []);
    const known = catalogue?.permissions ?? [];
    const listed = known.filter((p) => mine.has(p.id));
    const unknown = [...mine].filter((id) => !known.some((p) => p.id === id))
      .map((id) => ({ id, label: id, group: 'Other', description: '' }));
    return [...listed, ...unknown];
  }, [user, catalogue]);

  const ready = !!name.trim() && (!limited || picked.length > 0);
  const close = () => {
    onOpenChange(false);
    setName(''); setLifetime('90'); setLimited(false); setPicked([]); setMade(null);
  };
  const submit = async (e: SyntheticEvent) => {
    e.preventDefault();
    if (!ready || busy) return;
    setBusy(true);
    try {
      const token = await tokensApi.create({
        name: name.trim(), scopes: limited ? picked : [], expiresInDays: lifetime === 'never' ? null : Number(lifetime),
      });
      setMade(token);
      onMade();
    } catch (err) {
      toast.error('Token not made', { description: reason(err) });
    } finally {
      setBusy(false);
    }
  };
  const copy = () => {
    if (!made) return;
    navigator.clipboard.writeText(made.token)
      .then(() => toast.success('Token copied'), () => toast.error('Copying is blocked here. Select the text instead.'));
  };

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) close(); }}>
      <DialogContent className="sm:max-w-[560px]">
        {made ? (
          <>
            <DialogHeader>
              <DialogTitle>{made.name} is ready</DialogTitle>
              <DialogDescription>
                Copy it now. It is never shown again.
              </DialogDescription>
            </DialogHeader>
            <div className="min-w-0 rounded-xl border border-line bg-surface-2/50 px-4 py-3">
              <code className="block font-mono text-[12.5px] break-all text-ink select-all">{made.token}</code>
              <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
                <span className="text-[12px] text-dim">
                  {made.expiresAt ? `Works until ${at(made.expiresAt)}.` : 'Works until you revoke it.'}
                </span>
                <Button type="button" size="sm" variant="outline" onClick={copy}><Copy className="size-3.5" />Copy</Button>
              </div>
            </div>
            <div className="min-w-0 text-[12.5px] text-soft">
              <p>Use as <Mono>Authorization: Bearer …</Mono>, or in a terminal:</p>
              <Mono className="mt-1.5 inline-block [overflow-wrap:anywhere]">{`${loginLine()} --token -`}</Mono>
            </div>
            <DialogFooter><Button onClick={close}>Done</Button></DialogFooter>
          </>
        ) : (
          <form onSubmit={submit} className="grid min-w-0 gap-4">
            <DialogHeader>
              <DialogTitle>New access token</DialogTitle>
              <DialogDescription>Signs in as you, within the rights you choose.</DialogDescription>
            </DialogHeader>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <Field label="Name" value={name} onChange={setName} autoFocus autoComplete="off" placeholder="CI on the build server" />
              <SelectField label="Works for" value={lifetime} onChange={setLifetime} options={LIFETIMES} />
            </div>
            <div className="min-w-0">
              <div className="mb-1.5 text-[12.5px] font-medium text-soft">What it may do</div>
              <div className="grid gap-1.5">
                {[{ on: false, title: 'Everything you hold, except machine access', note: 'The usual choice. Cannot open a terminal here.' },
                  { on: true, title: 'Only what I choose', note: 'Pick machine access if it must open a terminal.' }].map((o) => (
                  <label key={String(o.on)} className={cn('flex cursor-pointer items-start gap-3 rounded-lg border px-3 py-2.5 transition-colors',
                    limited === o.on ? 'border-brand/45 bg-brand/6' : 'border-line hover:bg-surface-2/60')}>
                    <input type="radio" name="token-scope" className="mt-0.5 size-4 shrink-0 accent-[var(--os-brand)]"
                      checked={limited === o.on} onChange={() => setLimited(o.on)} />
                    <span className="min-w-0">
                      <span className="block text-[13.5px] font-medium text-ink">{o.title}</span>
                      <span className="block text-[12.5px] leading-snug text-soft">{o.note}</span>
                    </span>
                  </label>
                ))}
              </div>
              {limited && (
                <div className="mt-2 max-h-[240px] space-y-1 overflow-y-auto rounded-lg border border-line p-2">
                  {held.length === 0 ? (
                    <p className="px-1 py-1.5 text-[12.5px] text-dim">Your roles hold no permissions to give.</p>
                  ) : held.map((p) => {
                    const on = picked.includes(p.id);
                    return (
                      <label key={p.id} className="flex cursor-pointer items-start gap-2.5 rounded-md px-1.5 py-1.5 hover:bg-surface-2/60">
                        <input type="checkbox" className="mt-0.5 size-4 shrink-0 accent-[var(--os-brand)]" checked={on}
                          onChange={(e) => setPicked(e.target.checked ? [...picked, p.id] : picked.filter((x) => x !== p.id))} />
                        <span className="min-w-0">
                          <span className="block text-[13px] text-ink">
                            {p.label} <span className="font-mono text-[11.5px] text-dim">{p.id}</span>
                          </span>
                          {p.id === MACHINE_PERMISSION
                            ? <span className="block text-[12px] text-warn">Can open a terminal and change files here.</span>
                            : p.description && <span className="block text-[12px] leading-snug text-dim">{p.description}</span>}
                        </span>
                      </label>
                    );
                  })}
                </div>
              )}
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={close}>Cancel</Button>
              <Button type="submit" disabled={!ready || busy}>{busy && <Loader2 className="size-3.5 animate-spin" />}Make token</Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}
