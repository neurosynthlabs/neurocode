import { useMemo, useState } from 'react';
import { Cpu, HardDrive, Cloud, Search } from 'lucide-react';
import { toast } from 'sonner';
import { usePref } from '@/lib/data';
import { Switch } from '@/components/ui/switch';
import {
  Page, PageHeader, PageBody, Panel, Tag, Dot, Mono, Ascii, Toolbar, Field, SelectField,
  DataTable, Row, Cell, Stat, StatGrid, Bar, Segmented, Empty, KV,
} from '@/components/os';
import { models, routingRules, routerToggles, routerSplit, routerDiagram } from '@/mock/models';
import { cn } from '@/lib/utils';

const KIND_TONE = { reasoning: 'violet', coding: 'brand', vision: 'info', small: 'neutral', embedding: 'ok', reranker: 'warn' } as const;

const ROUTER_ON: Record<string, boolean> = Object.fromEntries(routerToggles.map((t) => [t.id, t.on]));
const RULES_ON: Record<string, boolean> = Object.fromEntries(routingRules.map((r) => [r.id, r.enabled]));

export default function Models() {
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
