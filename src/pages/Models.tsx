import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { Cpu, HardDrive, Cloud, Loader2, Search } from 'lucide-react';
import { toast } from 'sonner';
import { useData, usePref } from '@/lib/data';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/switch';
import {
  Page, PageHeader, PageBody, Panel, Tag, Dot, Mono, Ascii, Toolbar, Field, SelectField,
  DataTable, Row, Cell, Stat, StatGrid, Bar, Segmented, Empty, KV,
} from '@/components/os';
import { models, routingRules, routerToggles, routerSplit, routerDiagram } from '@/mock/models';
import { ApiError, api, type AiPatch, type AiPreference, type LaneId } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { fetchModels, type FleetLane, type ModelsReport, type RouteLine } from '@/lib/live/models';
import { useRemote } from '@/lib/remote';
import { cn } from '@/lib/utils';

const KIND_TONE = { reasoning: 'violet', coding: 'brand', vision: 'info', small: 'neutral', embedding: 'ok', reranker: 'warn' } as const;

const ROUTER_ON: Record<string, boolean> = Object.fromEntries(routerToggles.map((t) => [t.id, t.on]));
const RULES_ON: Record<string, boolean> = Object.fromEntries(routingRules.map((r) => [r.id, r.enabled]));

/** The gateway's real lanes and routes when the local API is up; the worked example in the demo. */
export default function Models() {
  const { mode } = useData();
  return mode === 'live' ? <LiveModels /> : <SampleModels />;
}

function SampleModels() {
  const [q, setQ] = useState('');
  const [kind, setKind] = useState('all');
  const [host, setHost] = useState('all');
  const [tab, setTab] = useState<'fleet' | 'routing'>('fleet');
  const [toggles, setToggles] = usePref('models.router', ROUTER_ON);
  const [rules, setRules] = usePref('models.rules', RULES_ON);

  const list = useMemo(() => {
    const s = q.trim().toLowerCase();
    return models.filter((m) =>
      (kind === 'all' || m.kind === kind) &&
      (host === 'all' || m.hosting === host) &&
      (!s || (m.name + m.vendor + m.notes).toLowerCase().includes(s)));
  }, [q, kind, host]);

  const localCost = models.filter((m) => m.hosting === 'local').reduce((n, m) => n + m.cost24h, 0);
  const remoteCost = models.filter((m) => m.hosting === 'remote').reduce((n, m) => n + m.cost24h, 0);

  return (
    <Page>
      <PageHeader
        title="Models & Router"
        subtitle="One model for everything is the most expensive mistake available. The router picks per task class, falls back on failure, and prefers the machine on your desk."
        actions={<Segmented options={[{ id: 'fleet', label: `Fleet (${models.length})` }, { id: 'routing', label: `Routing (${routingRules.length})` }]} value={tab} onChange={setTab} />}
      >
        {tab === 'fleet' && (
          <Toolbar>
            <Field className="w-56" value={q} onChange={setQ} icon={<Search className="size-3.5" />} placeholder="Search models…" onClear={() => setQ('')} />
            <SelectField className="w-40" value={kind} onChange={setKind}
              options={[{ value: 'all', label: 'Any kind' }, ...['reasoning', 'coding', 'vision', 'small', 'embedding', 'reranker'].map((k) => ({ value: k, label: k }))]} />
            <SelectField className="w-36" value={host} onChange={setHost}
              options={[{ value: 'all', label: 'Anywhere' }, { value: 'local', label: 'local' }, { value: 'remote', label: 'remote' }]} />
            <span className="ml-auto text-[12.5px] text-dim">{list.length} of {models.length}</span>
          </Toolbar>
        )}
      </PageHeader>

      <PageBody className="space-y-4">
        <StatGrid cols={5}>
          <Stat label="Local share" value={`${routerSplit.localPct}%`} tone="ok" sub={`${routerSplit.localCalls.toLocaleString()} calls`} icon={<HardDrive className="size-3" />} />
          <Stat label="Remote share" value={`${routerSplit.remotePct}%`} tone="brand" sub={`${routerSplit.remoteCalls.toLocaleString()} calls`} icon={<Cloud className="size-3" />} />
          <Stat label="Saved today" value={`$${routerSplit.savedToday.toFixed(2)}`} tone="ok" sub={`vs $${routerSplit.wouldHaveCost.toFixed(2)} all-remote`} />
          <Stat label="Actual spend" value={`$${routerSplit.actualCost.toFixed(2)}`} sub={`local $${localCost.toFixed(2)} · remote $${remoteCost.toFixed(2)}`} />
          <Stat label="Ready" value={models.filter((m) => m.status === 'ready').length} sub={`of ${models.length} in the fleet`} icon={<Cpu className="size-3" />} />
        </StatGrid>

        <div className="grid grid-cols-12 gap-3">
          <Panel className="col-span-12 xl:col-span-7" eyebrow="How a task finds its model" title="Router">
            <Ascii className="overflow-auto">{routerDiagram}</Ascii>
            <div className="mt-3 border-t border-line pt-2.5">
              <div className="mb-1.5 flex items-baseline justify-between">
                <span className="eyebrow">local vs remote, last 24h</span>
                <span className="tnum text-[12.5px] text-soft">{routerSplit.localPct}% / {routerSplit.remotePct}%</span>
              </div>
              <div className="flex h-2 overflow-hidden rounded-full bg-surface-3">
                <div className="bg-ok" style={{ width: `${routerSplit.localPct}%` }} />
                <div className="bg-brand" style={{ width: `${routerSplit.remotePct}%` }} />
              </div>
            </div>
          </Panel>

          <Panel className="col-span-12 xl:col-span-5" eyebrow="Router behaviour" title="Policy" flush>
            <div className="divide-y divide-line">
              {routerToggles.map((t) => {
                const on = toggles[t.id];
                return (
                  <div key={t.id} className="px-3.5 py-2.5">
                    <div className="flex items-center justify-between gap-3">
                      <span className="text-[13.5px] font-medium text-ink">{t.label}</span>
                      <Switch checked={on} onCheckedChange={(v) => { setToggles({ ...toggles, [t.id]: v }, `Router: ${t.label} ${v ? 'on' : 'off'}`); toast(`${t.label} ${v ? 'on' : 'off'}`); }} />
                    </div>
                    <p className={cn('mt-1 text-[12.5px]', on ? 'text-soft' : 'text-warn')}>{on ? t.onText : t.offText}</p>
                  </div>
                );
              })}
            </div>
          </Panel>
        </div>

        {tab === 'fleet' ? (
          <Panel flush>
            {list.length === 0 ? <Empty title="No model matches" /> : (
              <DataTable head={['Model', 'Kind', 'Host', 'Context', 'In / 1M', 'Out / 1M', 'Latency', 'Quality', 'Calls 24h', 'Cost 24h', 'Status']}>
                {list.map((m) => (
                  <Row key={m.id}>
                    <Cell className="font-medium text-ink">
                      {m.name}
                      <span className="mt-0.5 block max-w-[380px] truncate text-[12px] font-normal text-dim">{m.vendor} · {m.notes}</span>
                    </Cell>
                    <Cell><Tag tone={KIND_TONE[m.kind]}>{m.kind}</Tag></Cell>
                    <Cell>{m.hosting === 'local'
                      ? <span className="flex items-center gap-1.5 text-ok"><HardDrive className="size-3" />local</span>
                      : <span className="flex items-center gap-1.5 text-brand"><Cloud className="size-3" />remote</span>}</Cell>
                    <Cell className="tnum">{m.contextK}K</Cell>
                    <Cell className="tnum">{m.inPer1M === 0 ? '—' : `$${m.inPer1M.toFixed(2)}`}</Cell>
                    <Cell className="tnum">{m.outPer1M === 0 ? '—' : `$${m.outPer1M.toFixed(2)}`}</Cell>
                    <Cell className="tnum">{m.latencyMs}ms</Cell>
                    <Cell><span className="flex items-center gap-2"><Bar className="w-14" pct={m.quality} /><span className="tnum text-[12px]">{m.quality}</span></span></Cell>
                    <Cell className="tnum">{m.calls24h.toLocaleString()}</Cell>
                    <Cell className={cn('tnum', m.cost24h === 0 ? 'text-ok' : 'text-ink')}>{m.cost24h === 0 ? 'free' : `$${m.cost24h.toFixed(2)}`}</Cell>
                    <Cell><span className="flex items-center gap-1.5"><Dot state={m.status} pulse={m.status === 'ready'} />{m.status}</span></Cell>
                  </Row>
                ))}
              </DataTable>
            )}
          </Panel>
        ) : (
          <Panel eyebrow="Evaluated top to bottom · first match wins" title="Routing rules" flush>
            <DataTable head={['When', 'Route to', 'Fallback', 'Hits 24h', 'Enabled']}>
              {routingRules.map((r) => (
                <Row key={r.id} className={cn(!rules[r.id] && 'opacity-50')}>
                  <Cell className="text-[13px] text-ink">{r.when}</Cell>
                  <Cell><Mono tone="brand">{r.route}</Mono></Cell>
                  <Cell><Mono>{r.fallback}</Mono></Cell>
                  <Cell className="tnum">{r.hits24h.toLocaleString()}</Cell>
                  <Cell><Switch checked={rules[r.id]} onCheckedChange={(v) => setRules({ ...rules, [r.id]: v }, `Routing rule ${r.id} ${v ? 'enabled' : 'disabled'}`)} /></Cell>
                </Row>
              ))}
            </DataTable>
            <div className="border-t border-line px-3.5 py-2.5">
              <KV k="Fallback chain" v="primary → declared fallback → smallest local model that passed the eval bar" />
              <KV k="On total failure" v="the task is paused and surfaced to you — never silently downgraded" />
            </div>
          </Panel>
        )}
      </PageBody>
    </Page>
  );
}

/* ── Live: the gateway's own view, and a day of its ledger ────── */

const ROLE_TONE = { write: 'brand', review: 'violet', plan: 'info', chat: 'ok' } as const;
const GENERAL: { id: 'auto' | 'free' | 'local' | 'rules'; label: string }[] = [
  { id: 'auto', label: 'Auto' }, { id: 'free', label: 'Free only' }, { id: 'local', label: 'Local only' }, { id: 'rules', label: 'No model' },
];
const pct = (part: number, whole: number) => (whole ? Math.round((100 * part) / whole) : 0);
const money = (usd: number) => (usd === 0 ? 'free' : `$${usd.toFixed(usd < 1 ? 4 : 2)}`);
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
  const [tab, setTab] = useState<'fleet' | 'routing'>('fleet');
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
        title="Models & Router"
        subtitle="Every model call goes through one gateway and its lanes. Which lane answers, what each feature asks for, and what happened in the last day, measured."
        actions={r && <Segmented options={[{ id: 'fleet', label: `Lanes (${r.lanes.length})` }, { id: 'routing', label: `Routing (${r.routes.length})` }]} value={tab} onChange={setTab} />}
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
      ) : (
        <PageBody className="space-y-4">
          <Totals r={r} />

          <div className="grid grid-cols-12 gap-3">
            <Panel className="col-span-12 xl:col-span-7" eyebrow="What each feature would try now" title="Router">
              <Ascii className="overflow-auto">{diagram(r.routes)}</Ascii>
              <p className="mt-2 text-[12.5px] text-dim">{r.ordering}</p>
              <Split24h r={r} />
            </Panel>

            <Panel className="col-span-12 xl:col-span-5" eyebrow="Which lanes the router may use" title="Policy">
              <Policy r={r} admin={admin} busy={busy === 'preference'}
                onChange={(preference) => void save({ preference }, `Routing set to ${preference}`, 'preference')} />
            </Panel>
          </div>

          {tab === 'fleet' ? (
            <Panel flush>
              {list.length === 0 ? <Empty title="No lane matches" /> : (
                <DataTable head={['Lane', 'Good at', 'Host', 'In / 1M', 'Out / 1M', 'Avg 24h', 'OK rate 24h', 'Calls 24h', 'Cost 24h', 'Status', ...(admin ? ['On', ''] : [])]}>
                  {list.map((l) => {
                    const okRate = l.calls24h ? pct(l.calls24h - l.failures24h, l.calls24h) : null;
                    return (
                      <Row key={l.id}>
                        <Cell className="font-medium text-ink">
                          {l.label} <Mono className="ml-1">{l.model}</Mono>
                          <span className="mt-0.5 block max-w-[380px] truncate text-[12px] font-normal text-dim">
                            {l.free ? 'free' : 'paid'}{l.rpm ? ` · ${l.rpm}/min` : ''}{l.rpd ? ` · ${l.rpd}/day` : ''}{l.embed ? ` · embeds with ${l.embed}` : ''} · {l.note}
                          </span>
                        </Cell>
                        <Cell><span className="flex flex-wrap gap-1">{l.goodAt.map((g) => <Tag key={g} tone={ROLE_TONE[g]}>{g}</Tag>)}</span></Cell>
                        <Cell>{l.hosting === 'local'
                          ? <span className="flex items-center gap-1.5 text-ok"><HardDrive className="size-3" />local</span>
                          : <span className="flex items-center gap-1.5 text-brand"><Cloud className="size-3" />remote</span>}</Cell>
                        <Cell className="tnum">{!l.priced ? 'unpriced' : l.usdPerMIn === 0 ? '—' : `$${l.usdPerMIn.toFixed(2)}`}</Cell>
                        <Cell className="tnum">{!l.priced ? 'unpriced' : l.usdPerMOut === 0 ? '—' : `$${l.usdPerMOut.toFixed(2)}`}</Cell>
                        <Cell className="tnum">{l.calls24h ? `${l.avgMs24h.toLocaleString()} ms` : '—'}</Cell>
                        <Cell>{okRate === null ? <span className="text-dim">—</span> : <span className="flex items-center gap-2"><Bar className="w-14" pct={okRate} /><span className="tnum text-[12px]">{okRate}%</span></span>}</Cell>
                        <Cell className="tnum">{l.calls24h.toLocaleString()}</Cell>
                        <Cell className={cn('tnum', l.cost24h === 0 ? 'text-ok' : l.cost24h === null ? 'text-dim' : 'text-ink')}>{l.cost24h === null ? 'unpriced' : money(l.cost24h)}</Cell>
                        <Cell>
                          <span className="flex items-center gap-1.5">
                            <Dot state={l.ready ? 'ready' : l.enabled ? 'offline' : 'disabled'} pulse={l.ready} />
                            <span className={cn('text-[12.5px]', l.ready ? 'text-ok' : 'text-soft')}>{l.ready ? `ready · ${l.spent.today} today` : l.blocked ?? 'not allowed'}</span>
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
            <Panel eyebrow="Fixed in the code that makes each call" title="What each feature asks for" flush>
              <DataTable head={['Feature', 'Asks for', 'Would try now', 'With no lane', 'Calls 24h', 'Offline 24h', 'Failed 24h']}>
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
                    <Cell className={cn('text-[12.5px]', line.offline ? 'text-ok' : 'text-warn')}>{line.offline ? 'rules answer' : 'no answer'}</Cell>
                    <Cell className="tnum">{line.calls24h.toLocaleString()}</Cell>
                    <Cell className="tnum">{line.offline24h.toLocaleString()}</Cell>
                    <Cell className={cn('tnum', line.failures24h > 0 && 'text-warn')}>{line.failures24h.toLocaleString()}</Cell>
                  </Row>
                ))}
              </DataTable>
              <div className="border-t border-line px-5 py-3">
                <KV k="When a lane fails" v="the call moves to the next lane in the chain" />
                <KV k="When every lane fails" v="features with an offline answer give it and say so; the rest say no lane answered" />
              </div>
            </Panel>
          )}
        </PageBody>
      )}
    </Page>
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
      <Stat label="Offline answers" value={t.offline.toLocaleString()} sub={offlineReviews ? `rules answered, last 24h · plus ${offlineReviews} rules-read review${offlineReviews === 1 ? '' : 's'}` : 'rules answered, no model, last 24h'} />
      <Stat
        label="Spend 24h"
        value={t.costComplete ? money(t.costUsd) : t.costUsd === 0 ? 'unpriced' : `≥ ${money(t.costUsd)}`}
        tone={t.costComplete && t.costUsd === 0 ? 'ok' : undefined}
        sub={t.costComplete ? `${(t.tokensIn + t.tokensOut).toLocaleString()} tokens at each lane's price` : 'a lane with no declared price answered: this is a floor'}
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
        <span className="eyebrow">local · remote · offline, last 24h</span>
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
      <KV k="Answering now" v={r.active.provider === 'rules' ? 'offline rules' : <Mono tone="brand">{r.active.provider} · {r.active.model}</Mono>} />
      {r.active.note && <p className="text-[12.5px] text-warn">{r.active.note}</p>}
      {locked ? (
        <p className="text-[12.5px] text-warn">NEUROCODE_COMPILER is set on the server, and it wins over this setting.</p>
      ) : !admin && (
        <p className="text-[12.5px] text-dim">Read-only: changing the routing needs workspace admin. Keys and limits live in <Link to="/admin/ai" className="text-brand hover:underline">Admin → AI providers</Link>.</p>
      )}
    </div>
  );
}
