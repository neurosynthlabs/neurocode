import { useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Cpu, HardDrive, Cloud, Loader2, Search } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/switch';
import {
  Page, PageHeader, PageBody, Panel, Tag, Dot, Mono, Ascii, Toolbar, Field, SelectField,
  DataTable, Row, Cell, Stat, StatGrid, Bar, Segmented, Empty, KV, More,
} from '@/components/os';
import { ApiError, api, type AiPatch, type AiPreference, type LaneId, type ThinkingLevel } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { fetchModels, type FleetLane, type ModelsReport, type RouteLine } from '@/lib/live/models';
import { useRemote } from '@/lib/remote';
import { cn } from '@/lib/utils';
import { ProviderKeys } from '@/pages/admin/AiProviders';

/** The gateway's real lanes and routes, and a day of its ledger. */
export default function Models() {
  return <LiveModels />;
}

/* ── Live: the gateway's own view, and a day of its ledger ────── */

type Tab = 'fleet' | 'routing' | 'keys';
const ROLE_TONE = { write: 'brand', review: 'violet', plan: 'info', chat: 'ok' } as const;
const GENERAL: { id: 'auto' | 'free' | 'local' | 'rules'; label: string }[] = [
  { id: 'auto', label: 'Auto' }, { id: 'free', label: 'Free only' }, { id: 'local', label: 'Local only' }, { id: 'rules', label: 'No model' },
];
const LEVELS: ThinkingLevel[] = ['off', 'low', 'high', 'max'];
const pct = (part: number, whole: number) => (whole ? Math.round((100 * part) / whole) : 0);
const compact = (n: number) => (n >= 1_000_000 ? `${(n / 1_000_000).toFixed(n % 1_000_000 ? 2 : 0)}M` : n >= 1000 ? `${Math.round(n / 1000)}k` : `${n}`);
const money = (usd: number) => (usd === 0 ? 'free' : `$${usd.toFixed(usd < 1 ? 4 : 2)}`);
/** What a lane allows itself, and whose numbers those are. A provider that publishes none gets said so:
    0 here means "no cap of ours", which is not the same as "unlimited", and must never read as it. */
const caps = (l: FleetLane) => {
  const parts = [l.rpm ? `${l.rpm}/min` : '', l.rpd ? `${l.rpd}/day` : '', l.tpd ? `${compact(l.tpd)} tokens/day` : '',
    l.maxRequestTokens ? `${compact(l.maxRequestTokens)} tokens a call, so it thinks less here` : ''].filter(Boolean);
  if (!parts.length) return '';
  return ` · ${parts.join(' · ')}${l.caps === 'ours' ? ' (ours — it publishes none)' : ''}`;
};
const why = (e: unknown) => (e instanceof ApiError ? e.message : 'The local API did not answer.');

/** A router diagram drawn from what the gateway would try now, one line per feature. */
function diagram(routes: RouteLine[]): string {
  const width = Math.max(...routes.map((r) => r.feature.length));
  return routes.map((r) => {
    const chain = r.chain.length ? r.chain.map((c) => c.lane).join(' → ') : r.feature === 'test' ? 'the lane an admin names' : 'no lane open';
    const tail = r.offline ? '  ⇢ rules' : '';
    return `${r.feature.padEnd(width)}  ${r.role ? `[${r.role}]`.padEnd(9) : '[any]'.padEnd(9)} ${chain}${tail}`;
  }).join('\n');
}

function LiveModels() {
  const { can } = useAuth();
  const admin = can('workspace:admin');
  const report = useRemote('models', fetchModels);
  const [params, setParams] = useSearchParams();
  const tab: Tab = params.get('tab') === 'routing' ? 'routing' : params.get('tab') === 'keys' && admin ? 'keys' : 'fleet';
  const setTab = (t: Tab) => setParams(t === 'fleet' ? {} : { tab: t }, { replace: true });
  const [q, setQ] = useState('');
  const [good, setGood] = useState('all');
  const [host, setHost] = useState('all');
  const [busy, setBusy] = useState<string | null>(null);

  const r = report.data;
  const list = useMemo(() => {
    const s = q.trim().toLowerCase();
    return (r?.lanes ?? []).filter((l) =>
      (good === 'all' || (good === 'embedding' ? !!l.embed : l.goodAt.includes(good as FleetLane['goodAt'][number]))) &&
      (host === 'all' || l.hosting === host) &&
      (!s || `${l.label} ${l.model} ${l.note} ${l.embed ?? ''}`.toLowerCase().includes(s)));
  }, [r, q, good, host]);

  const save = async (patch: AiPatch, done: string, key: string) => {
    setBusy(key);
    try {
      await api.admin.updateAi(patch);
      toast.success(done);
      report.reload();
    } catch (e) {
      toast.error('Not saved', { description: why(e) });
    } finally {
      setBusy(null);
    }
  };
  const test = async (lane: FleetLane) => {
    setBusy(`test:${lane.id}`);
    try {
      const out = await api.admin.testAi(lane.id);
      if (out.ok) toast.success(`${lane.label} answered in ${out.ms} ms`);
      else toast.error(`${lane.label} did not answer`, { description: out.detail });
      report.reload();                     // a refused key is remembered by the gateway: show it
    } catch (e) {
      toast.error('The test did not run', { description: why(e) });
    } finally {
      setBusy(null);
    }
  };

  return (
    <Page>
      <PageHeader
        title="Models"
        subtitle="Which lane answers each feature, and the last day, measured."
        about={<p>Every model call goes through one gateway and its lanes.</p>}
        actions={r && <Segmented<Tab> value={tab} onChange={setTab} options={[
          { id: 'fleet', label: `Lanes (${r.lanes.length})` }, { id: 'routing', label: `Routing (${r.routes.length})` },
          ...(admin ? [{ id: 'keys' as const, label: 'Keys' }] : [])]} />}
      >
        {r && tab === 'fleet' && (
          <Toolbar>
            <Field className="w-56" value={q} onChange={setQ} icon={<Search className="size-3.5" />} placeholder="Search lanes…" onClear={() => setQ('')} />
            <SelectField className="w-40" value={good} onChange={setGood}
              options={[{ value: 'all', label: 'Good at anything' }, ...['write', 'review', 'plan', 'chat', 'embedding'].map((k) => ({ value: k, label: k }))]} />
            <SelectField className="w-36" value={host} onChange={setHost}
              options={[{ value: 'all', label: 'Anywhere' }, { value: 'local', label: 'local' }, { value: 'remote', label: 'remote' }]} />
            <span className="ml-auto text-[12.5px] text-dim">{list.length} of {r.lanes.length}</span>
          </Toolbar>
        )}
      </PageHeader>

      {report.error ? (
        <PageBody><Empty title="The router did not load" hint={report.error} action={<Button size="sm" variant="outline" onClick={report.reload}>Try again</Button>} /></PageBody>
      ) : !r ? (
        <PageBody><Empty icon={<Loader2 className="size-5 animate-spin" />} title="Asking the gateway…" /></PageBody>
      ) : tab === 'keys' ? (
        <PageBody><ProviderKeys /></PageBody>
      ) : (
        <PageBody className="space-y-4">
          {r.totals24h.calls > 0 && <Totals r={r} />}

          <div className="grid grid-cols-12 gap-3">
            <Panel className="col-span-12 xl:col-span-7" title="Router" about={<><p>What each feature would try now.</p><p>{r.ordering}</p></>}>
              <Ascii className="overflow-auto">{diagram(r.routes)}</Ascii>
              <Split24h r={r} />
            </Panel>

            <Panel className="col-span-12 xl:col-span-5" title="Policy" about="Which lanes the router may use.">
              <Policy r={r} admin={admin} busy={busy === 'preference'}
                onChange={(preference) => void save({ preference }, `Routing set to ${preference}`, 'preference')} />
            </Panel>
          </div>

          {tab === 'fleet' ? (
            <Panel flush>
              {list.length === 0 ? <Empty title="No lane matches" /> : (
                <DataTable head={['Lane', 'Good at', 'Host', 'In / 1M', 'Out / 1M', 'Avg 24h', 'OK rate 24h', 'Calls 24h', 'Cache 24h', 'Cost 24h', 'Status', ...(admin ? ['On', ''] : [])]}>
                  {list.map((l) => {
                    const okRate = l.calls24h ? pct(l.calls24h - l.failures24h, l.calls24h) : null;
                    return (
                      <Row key={l.id}>
                        <Cell className="font-medium text-ink">
                          {l.label} <Mono className="ml-1">{l.model}</Mono>
                          <span className="mt-0.5 block max-w-[380px] truncate text-[12px] font-normal text-dim" title={l.note}>
                            {l.freedom}{l.expires ? ` · ${l.expires}` : ''}{l.window ? ` · ${compact(l.window)} context` : ''}{caps(l)}{l.embed ? ` · embeds with ${l.embed}` : ''} · {l.note}
                          </span>
                          {l.allowance && <span className="mt-0.5 block max-w-[380px] text-[12px] font-normal whitespace-normal text-soft">{l.allowance}</span>}
                          {l.retired && <span className="mt-0.5 block max-w-[380px] text-[12px] font-normal whitespace-normal text-warn">{l.retired}</span>}
                          {l.offPeak && <span className="mt-0.5 block max-w-[380px] text-[12px] font-normal whitespace-normal text-dim">{l.offPeak.words}</span>}
                        </Cell>
                        <Cell><span className="flex flex-wrap gap-1">{l.goodAt.map((g) => <Tag key={g} tone={ROLE_TONE[g]}>{g}</Tag>)}</span></Cell>
                        <Cell>{l.hosting === 'local'
                          ? <span className="flex items-center gap-1.5 text-ok"><HardDrive className="size-3" />local</span>
                          : <span className="flex items-center gap-1.5 text-brand"><Cloud className="size-3" />remote</span>}</Cell>
                        <Cell className="tnum">{!l.priced ? 'unpriced' : l.usdPerMIn === 0 ? 'free' : `$${l.usdPerMIn.toFixed(2)}`}</Cell>
                        <Cell className="tnum">{!l.priced ? 'unpriced' : l.usdPerMOut === 0 ? 'free' : `$${l.usdPerMOut.toFixed(2)}`}</Cell>
                        <Cell className="tnum">{l.calls24h ? `${l.avgMs24h.toLocaleString()} ms` : '—'}</Cell>
                        <Cell>{okRate === null ? <span className="text-dim">—</span> : <span className="flex items-center gap-2"><Bar className="w-14" pct={okRate} /><span className="tnum text-[12px]">{okRate}%</span></span>}</Cell>
                        <Cell className="tnum">{l.calls24h.toLocaleString()}</Cell>
                        <Cell className="tnum">
                          {l.tokensIn24h ? `${pct(l.tokensCached24h, l.tokensIn24h)}% hit` : <span className="text-dim">—</span>}
                          {!!l.saved24h && <span className="block text-[12px] text-ok">saved {money(l.saved24h)}</span>}
                          {l.tokensReasoning24h > 0 && <span className="block text-[12px] text-dim">{compact(l.tokensReasoning24h)} reasoning</span>}
                        </Cell>
                        <Cell className={cn('tnum', l.cost24h === 0 ? 'text-ok' : l.cost24h === null ? 'text-dim' : 'text-ink')}>{l.cost24h === null ? 'unpriced' : money(l.cost24h)}</Cell>
                        <Cell>
                          <span className="flex items-center gap-1.5">
                            <Dot state={l.ready ? 'ready' : l.enabled ? 'offline' : 'disabled'} pulse={l.ready} />
                            <span className={cn('text-[12.5px]', l.ready ? 'text-ok' : 'text-soft')}>{l.ready ? `ready · ${l.spent.today} today${l.tpd ? ` · ${compact(l.spent.tokensToday)} of ${compact(l.tpd)} tokens` : ''}` : l.blocked ?? 'not allowed'}</span>
                          </span>
                          {l.ready && !l.allowed && <span className="mt-0.5 block text-[12px] text-dim">outside the routing policy</span>}
                        </Cell>
                        {admin && (
                          <>
                            <Cell><Switch checked={l.enabled} disabled={busy === `lane:${l.id}`} onCheckedChange={(v) => void save({ lane: l.id, enabled: v }, `${l.label} ${v ? 'switched on' : 'switched off'}`, `lane:${l.id}`)} /></Cell>
                            <Cell>
                              <Button size="xs" variant="outline" disabled={busy === `test:${l.id}` || (l.needsKey && !l.hasKey)} onClick={() => void test(l)}>
                                {busy === `test:${l.id}` && <Loader2 className="size-3 animate-spin" />}Test
                              </Button>
                            </Cell>
                          </>
                        )}
                      </Row>
                    );
                  })}
                </DataTable>
              )}
            </Panel>
          ) : (
            <Panel title="What each feature asks for" flush about="Fixed in the code that makes each call.">
              <DataTable head={['Feature', 'Asks for', 'Would try now', 'Thinking', 'With no lane', 'Calls 24h', 'Offline 24h', 'Failed 24h']}>
                {r.routes.map((line) => (
                  <Row key={line.feature}>
                    <Cell className="text-ink">
                      <Mono tone="brand">{line.feature}</Mono>
                      <span className="mt-1 block max-w-[420px] text-[12.5px] whitespace-normal text-dim">{line.how}</span>
                    </Cell>
                    <Cell>{line.role ? <Tag tone={ROLE_TONE[line.role]}>{line.role}</Tag> : <span className="text-[12.5px] text-dim">any lane</span>}
                      {line.avoidsWriter && <span className="mt-1 block text-[12px] text-dim">not the writer's lane</span>}</Cell>
                    <Cell>
                      {line.chain.length ? (
                        <span className="flex flex-wrap gap-1">{line.chain.map((c) => <Mono key={c.lane}>{c.lane}</Mono>)}</span>
                      ) : <span className="text-[12.5px] text-dim">{line.feature === 'test' ? 'the lane named' : 'no lane open'}</span>}
                    </Cell>
                    <Cell>
                      <Thinking line={line} admin={admin} busy={busy === `thinking:${line.feature}`}
                        onChange={(level) => void save({ thinking: { [line.feature]: level } }, `${line.feature} thinks ${level === 'default' ? `${line.thinkingDefault}, its default` : level}`, `thinking:${line.feature}`)} />
                    </Cell>
                    <Cell className={cn('text-[12.5px]', line.offline ? 'text-ok' : 'text-warn')}>{line.offline ? 'rules answer' : 'no answer'}</Cell>
                    <Cell className="tnum">{line.calls24h.toLocaleString()}</Cell>
                    <Cell className="tnum">{line.offline24h.toLocaleString()}</Cell>
                    <Cell className={cn('tnum', line.failures24h > 0 && 'text-warn')}>{line.failures24h.toLocaleString()}</Cell>
                  </Row>
                ))}
              </DataTable>
              <More label="Fallbacks and thinking" className="border-t border-line px-5 py-3">
                <KV k="When a lane fails" v="the call moves to the next lane in the chain" />
                <KV k="When every lane fails" v="features with an offline answer give it and say so; the rest say no lane answered" />
                <KV k="Thinking" v="only DeepSeek's thinking and Gemini's reasoning effort (high at most); other lanes get none" />
              </More>
            </Panel>
          )}
        </PageBody>
      )}
    </Page>
  );
}

/** How hard a feature asks a model to think. An admin sets it here; the default is chosen per kind of work. */
function Thinking({ line, admin, busy, onChange }: {
  line: RouteLine; admin: boolean; busy: boolean; onChange: (level: ThinkingLevel | 'default') => void;
}) {
  if (!line.thinking) return <span className="text-[12.5px] text-dim">—</span>;
  const custom = line.thinking !== line.thinkingDefault;
  if (!admin) return <span className="text-[12.5px] text-ink-2">{line.thinking}{custom ? '' : <span className="text-dim"> · default</span>}</span>;
  return (
    <span className="flex items-center gap-1.5">
      <select value={line.thinking} disabled={busy} aria-label={`How hard ${line.feature} thinks`}
        onChange={(e) => onChange(e.target.value as ThinkingLevel)}
        className="h-7 rounded-md border border-line-strong bg-surface-2 px-1.5 text-[12.5px] text-ink-2 disabled:opacity-60">
        {LEVELS.map((level) => <option key={level} value={level} className="bg-surface">{level}{level === line.thinkingDefault ? ' (default)' : ''}</option>)}
      </select>
      {custom && <Button size="xs" variant="ghost" disabled={busy} onClick={() => onChange('default')}>reset</Button>}
    </span>
  );
}

function Totals({ r }: { r: ModelsReport }) {
  const t = r.totals24h;
  const model = t.local + t.remote;
  const offlineReviews = r.routes.find((x) => x.feature === 'review')?.offline24h ?? 0;
  const ready = r.lanes.filter((l) => l.ready && l.allowed).length;
  const free = r.lanes.filter((l) => l.free && l.hosting === 'remote').reduce((n, l) => n + l.calls24h, 0);
  return (
    <StatGrid cols={5}>
      <Stat label="Local share" value={`${pct(t.local, model)}%`} tone="ok" sub={`${t.local.toLocaleString()} of ${model.toLocaleString()} model calls`} icon={<HardDrive className="size-3" />} />
      <Stat label="Remote share" value={`${pct(t.remote, model)}%`} tone="brand" sub={`${t.remote.toLocaleString()} calls · ${free.toLocaleString()} on free lanes`} icon={<Cloud className="size-3" />} />
      <Stat label="Offline answers" value={t.offline.toLocaleString()} sub={offlineReviews ? `rules answered · plus ${offlineReviews} rules-read review${offlineReviews === 1 ? '' : 's'}` : 'rules answered, no model'} />
      <Stat
        label="Spend 24h"
        value={t.costComplete ? money(t.costUsd) : t.costUsd === 0 ? 'unpriced' : `≥ ${money(t.costUsd)}`}
        tone={t.costComplete && t.costUsd === 0 ? 'ok' : undefined}
        sub={t.costComplete ? `${(t.tokensIn + t.tokensOut).toLocaleString()} tokens at each lane's price` : 'a floor: an unpriced lane answered'}
      />
      <Stat label="Ready" value={ready} sub={`of ${r.lanes.length} lanes, now`} icon={<Cpu className="size-3" />} />
    </StatGrid>
  );
}

function Split24h({ r }: { r: ModelsReport }) {
  const t = r.totals24h;
  return (
    <div className="mt-3 border-t border-line pt-2.5">
      <div className="mb-1.5 flex items-baseline justify-between">
        <span className="text-[12px] text-dim">Local · remote · offline, last 24h</span>
        <span className="tnum text-[12.5px] text-soft">{t.local} / {t.remote} / {t.offline}</span>
      </div>
      {t.calls === 0 ? <p className="text-[12.5px] text-dim">No call in the last day.</p> : (
        <div className="flex h-2 overflow-hidden rounded-full bg-surface-3">
          <div className="bg-ok" style={{ width: `${pct(t.local, t.calls)}%` }} />
          <div className="bg-brand" style={{ width: `${pct(t.remote, t.calls)}%` }} />
          <div className="bg-line-strong" style={{ width: `${pct(t.offline, t.calls)}%` }} />
        </div>
      )}
    </div>
  );
}

function Policy({ r, admin, busy, onChange }: { r: ModelsReport; admin: boolean; busy: boolean; onChange: (p: AiPreference) => void }) {
  const pinned = r.lanes.find((l) => l.id === r.preference);
  const general = GENERAL.some((g) => g.id === r.preference);
  const locked = r.preferenceLocked;
  const editable = admin && !locked && !busy;
  return (
    <div className="space-y-3">
      <Segmented
        options={GENERAL.map((g) => ({ id: g.id, label: g.label }))}
        value={general ? (r.preference as (typeof GENERAL)[number]['id']) : ('' as (typeof GENERAL)[number]['id'])}
        onChange={(v) => editable && onChange(v)}
      />
      <select
        value={pinned ? pinned.id : ''} disabled={!editable} aria-label="Pin every call to one lane"
        onChange={(e) => e.target.value && onChange(e.target.value as LaneId)}
        className="h-8 w-full rounded-lg border border-line-strong bg-surface-2 px-2 text-[12.5px] text-ink-2 disabled:opacity-60"
      >
        <option value="" className="bg-surface">Or pin every call to one lane…</option>
        {r.lanes.map((l) => <option key={l.id} value={l.id} className="bg-surface">{l.label} · {l.model}</option>)}
      </select>
      <p className="text-[13px] text-ink-2">
        {pinned ? `Only ${pinned.label}. Every other lane is skipped.` : r.preferences[r.preference as keyof ModelsReport['preferences']]}
      </p>
      <KV k="Answering now" wrap v={r.active.provider === 'rules' ? 'no model' : <Mono tone="brand">{r.active.provider} · {r.active.model}</Mono>} />
      {r.active.note && <p className="text-[12.5px] text-warn">{r.active.note}</p>}
      {locked ? (
        <p className="text-[12.5px] text-warn">NEUROCODE_COMPILER is set on the server and overrides this.</p>
      ) : !admin && (
        <p className="text-[12.5px] text-dim">Read-only: routing and keys need workspace admin.</p>
      )}
    </div>
  );
}
