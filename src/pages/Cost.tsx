import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Coins, HardDrive, Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Page, PageHeader, PageBody, Panel, Tag, Mono, Segmented, DataTable, Row, Cell,
  Stat, StatGrid, Bar, Empty, SectionTitle,
} from '@/components/os';
import { useAccess, agentName } from '@/lib/access';
import { FEATURE_LABEL, fetchUsage, type CacheLine, type Priced, type SpendDay, type SpendReport } from '@/lib/live/usage';
import { useRemote } from '@/lib/remote';
import { cn } from '@/lib/utils';
import { ago, tokens } from '@/pages/code/format';

/* Everything here is the AI gateway's ledger, as GET /usage sums it: one line per model call and per
   offline answer, each priced at its lane's declared price. Nothing is projected, and nothing is
   compared against a model nobody called. */

const feature = (id: string) => FEATURE_LABEL[id] ?? id;

/** Dollars as the ledger knows them: small sums keep their fractions of a cent, and an unpriced line says so. */
function usd(line: Priced): string {
  if (line.costUsd === null) return 'unpriced';
  const n = line.costUsd;
  const text = n === 0 ? '$0' : n < 0.01 ? `$${n.toFixed(4)}` : `$${n.toFixed(2)}`;
  return line.costComplete ? text : `≥ ${text}`;
}

/* The ledger's days are UTC days: the server buckets every call by its UTC date, whatever zone the
   database or the browser is in. So every key made here is a UTC date too — a day made in local time
   would miss the server's by one for part of every day (5.5 hours of it in India) and draw that
   day's calls as a zero. The screen says "UTC days" so nobody reads them as their own. */

/** 'YYYY-MM-DD' of the UTC day `back` days before today's. */
const utcDay = (back = 0): string => {
  const now = new Date();
  return new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() - back)).toISOString().slice(0, 10);
};

/** The window as UTC calendar days, oldest first. A day with no call spent nothing, so it is a real zero. */
function everyDay(r: SpendReport): SpendDay[] {
  const seen = new Map(r.byDay.map((d) => [d.day, d]));
  return Array.from({ length: r.days }, (_, i) => {
    const day = utcDay(r.days - 1 - i);
    return seen.get(day) ?? { day, calls: 0, model: 0, offline: 0, tokens: 0, costUsd: 0, costComplete: true };
  });
}

/** This UTC calendar month, summed from the days in the window, which is always long enough to reach the 1st. */
function thisMonth(r: SpendReport): Priced & { calls: number } {
  const month = utcDay().slice(0, 7);
  const days = r.byDay.filter((d) => d.day.startsWith(month));
  const priced = days.filter((d) => d.costUsd !== null);
  return {
    calls: days.reduce((n, d) => n + d.calls, 0),
    costUsd: days.length && !priced.length ? null : priced.reduce((n, d) => n + (d.costUsd ?? 0), 0),
    costComplete: days.every((d) => d.costComplete),
  };
}

export default function Cost() {
  const nav = useNavigate();
  // Thirty days, or as many as it takes to reach the 1st of the (UTC) month on the 31st.
  const days = Math.max(30, new Date().getUTCDate());
  const u = useRemote(`usage:${days}`, () => fetchUsage(days));

  return (
    <Page>
      <PageHeader
        title="Cost & Usage"
        subtitle="What each AI call cost, from the gateway’s ledger."
        about={<>
          <p>One line per model call and per offline answer, each priced at its lane’s declared price.</p>
          <p>Nothing is projected or estimated. Days are UTC days, whatever your time zone.</p>
        </>}
      />
      {u.error ? (
        <PageBody><Empty title="The usage ledger did not load" hint={u.error} action={<Button size="sm" variant="outline" onClick={u.reload}>Try again</Button>} /></PageBody>
      ) : !u.data ? (
        <PageBody><Empty icon={<Loader2 className="size-5 animate-spin" />} title="Reading the ledger…" /></PageBody>
      ) : u.data.totals.calls === 0 && u.data.recent.length === 0 ? (
        <PageBody>
          <Empty
            icon={<Coins className="size-5" />}
            title="No AI call yet"
            hint="Every model call is counted here, with its cost."
            action={<Button size="sm" variant="outline" onClick={() => nav('/')}>Open Command Center</Button>}
          />
        </PageBody>
      ) : (
        <Report r={u.data} />
      )}
    </Page>
  );
}

function Report({ r }: { r: SpendReport }) {
  const t = r.totals;
  const month = thisMonth(r);
  return (
    <PageBody className="space-y-4">
      <SectionTitle right={<span className="text-[12.5px] text-dim">last {r.days} days</span>}>Spend</SectionTitle>
      <StatGrid cols={5}>
        <Stat label="This month" value={usd(month)} tone="brand" sub={`${month.calls.toLocaleString()} call${month.calls === 1 ? '' : 's'} since the 1st (UTC)`} icon={<Coins className="size-3" />} />
        <Stat label={`Last ${r.days} days`} value={usd(t)} sub={`${t.calls.toLocaleString()} calls · ${t.modelCalls.toLocaleString()} to a model`} />
        <Stat label="Tokens" value={tokens(t.tokensIn + t.tokensOut)} sub={`${tokens(t.tokensIn)} in · ${tokens(t.tokensOut)} out`} />
        <Stat label="Failed model calls" value={t.failures} tone={t.failures ? 'warn' : 'ok'} sub="each handed to the next lane" />
        <Stat label="Offline share" value={`${t.calls ? Math.round((100 * t.offline) / t.calls) : 0}%`} tone="ok" sub={`${t.offline.toLocaleString()} by the rules, no model`} icon={<HardDrive className="size-3" />} />
      </StatGrid>
      {!t.costComplete && (
        <p className="text-[12.5px] text-warn">
          Some lanes have no price, so ≥ marks a floor.
        </p>
      )}

      {t.calls > 0 && <DailySpend r={r} />}
      {t.calls > 0 && <Breakdown r={r} />}
      {t.calls > 0 && <CacheAndReasoning r={r} />}
      {t.calls > 0 && <Costliest r={r} />}
      <Latest r={r} />
    </PageBody>
  );
}

const W = 560, H = 116;

function DailySpend({ r }: { r: SpendReport }) {
  const [hover, setHover] = useState<number | null>(null);
  const list = useMemo(() => everyDay(r), [r]);
  // When every call ran on a free lane there is no dollar to draw, so the bars count calls and say so.
  const dollars = list.some((d) => (d.costUsd ?? 0) > 0);
  const value = (d: SpendDay) => (dollars ? d.costUsd ?? 0 : d.calls);
  const max = Math.max(...list.map(value), dollars ? 0.0001 : 1);
  const bw = (W - (list.length - 1) * 3) / list.length;
  const day = hover !== null ? list[hover] : null;

  return (
    <Panel
      eyebrow={dollars ? `${r.days} UTC days · dollars per day` : `${r.days} UTC days · all free, so calls`}
      title="Daily spend"
      actions={day
        ? <span className="text-[12.5px] text-soft"><Mono>{day.day} UTC</Mono> {usd(day)} · {day.calls} call{day.calls === 1 ? '' : 's'} · {day.offline} offline</span>
        : <span className="text-[12px] text-dim">hover a bar</span>}
    >
      <svg width={W} height={H} className="w-full" viewBox={`0 0 ${W} ${H}`} onMouseLeave={() => setHover(null)}>
        {[0.25, 0.5, 0.75, 1].map((f) => (
          <line key={f} x1="0" y1={H - 18 - f * (H - 26)} x2={W} y2={H - 18 - f * (H - 26)} stroke="var(--os-line)" strokeDasharray="2 4" />
        ))}
        {list.map((d, i) => {
          const x = i * (bw + 3);
          const total = (value(d) / max) * (H - 26);
          // Counting calls, the offline answers sit at the foot of the bar; in dollars they are nothing to draw.
          const offline = dollars ? 0 : (d.offline / max) * (H - 26);
          const on = hover === i;
          const label = i === 0 || i === list.length - 1 || d.day.endsWith('-01');
          return (
            <g key={d.day} onMouseEnter={() => setHover(i)}>
              <rect x={x} y={0} width={bw} height={H} fill="transparent" />
              {total - offline > 0 && (
                <rect x={x} y={H - 18 - total} width={bw} height={total - offline} rx="2"
                  fill={dollars && !d.costComplete ? 'var(--os-warn)' : 'var(--os-brand)'} opacity={on ? 1 : 0.78} />
              )}
              {offline > 0 && <rect x={x} y={H - 18 - offline} width={bw} height={offline} rx="2" fill="var(--os-ok)" opacity={on ? 1 : 0.78} />}
              {label && (
                <text x={x + bw / 2} y={H - 5} fontSize="8.5" textAnchor="middle" fill={on ? 'var(--os-ink)' : 'var(--os-dim)'}>
                  {d.day.slice(5)}
                </text>
              )}
            </g>
          );
        })}
      </svg>
      <div className="mt-2 flex flex-wrap items-center gap-4 border-t border-line pt-2 text-[11.5px] text-dim">
        <span className="flex items-center gap-1.5"><span className="size-2 rounded-xs bg-brand" />{dollars ? 'spend' : 'calls to a model'}</span>
        {!dollars && <span className="flex items-center gap-1.5"><span className="size-2 rounded-xs bg-ok" />offline answers</span>}
        {dollars && !r.totals.costComplete && <span className="flex items-center gap-1.5"><span className="size-2 rounded-xs bg-warn" />unpriced calls, drawn at the floor</span>}
      </div>
    </Panel>
  );
}

type Split = 'agent' | 'project' | 'feature' | 'lane';

interface Line { key: string; label: string; calls: number; tokensIn: number; tokensOut: number; cost?: string; failures?: number }

function lines(r: SpendReport, split: Split): Line[] {
  switch (split) {
    case 'agent':
      return r.byAgent.map((a) => ({ key: a.agent, label: a.name, calls: a.calls, tokensIn: a.tokensIn, tokensOut: a.tokensOut, cost: usd(a) }));
    case 'project':
      return r.byProject.map((p) => ({ key: p.projectId ?? '', label: p.projectName ?? 'Workspace · no project', calls: p.calls, tokensIn: p.tokensIn, tokensOut: p.tokensOut, cost: usd(p) }));
    case 'feature':
      return r.byFeature.map((f) => ({ key: f.feature, label: feature(f.feature), calls: f.calls, tokensIn: f.tokensIn, tokensOut: f.tokensOut, failures: f.failures }));
    case 'lane':
      return r.byProvider.map((p) => ({ key: `${p.provider}:${p.model}`, label: p.provider === 'rules' ? 'Offline rules' : `${p.provider} · ${p.model}`, calls: p.calls, tokensIn: p.tokensIn, tokensOut: p.tokensOut, failures: p.failures }));
  }
}

const SPLIT_HEAD: Record<Split, string> = { agent: 'Agent', project: 'Project', feature: 'Feature', lane: 'Answered by' };

function Breakdown({ r }: { r: SpendReport }) {
  const nav = useNavigate();
  const [split, setSplit] = useState<Split>('agent');
  const all = r.totals.tokensIn + r.totals.tokensOut;
  const share = (n: number) => (all ? Math.round((100 * n) / all) : 0);
  const rows = lines(r, split);
  // Dollars are grouped by agent and by project on the server; a feature or a lane is counted, not priced.
  const priced = split === 'agent' || split === 'project';

  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Segmented
          options={[{ id: 'agent', label: 'By agent' }, { id: 'project', label: 'By project' }, { id: 'feature', label: 'By feature' }, { id: 'lane', label: 'By lane' }]}
          value={split} onChange={setSplit}
        />
        <span className="ml-auto text-[12.5px] text-dim">
          {split === 'agent' ? 'agent calls only, not a person’s own' : `${tokens(all)} tokens in the window`}
        </span>
      </div>
      <Panel flush>
        {rows.length === 0 ? (
          <Empty
            title="No agent made a model call in this window"
            hint="Dispatch a plan to see what agents spend."
            action={<Button size="sm" variant="outline" onClick={() => nav('/plans')}>Open Plans</Button>}
          />
        ) : (
          <DataTable head={[SPLIT_HEAD[split], 'Calls', 'Tokens in', 'Tokens out', priced ? 'Cost' : 'Failed', 'Share of tokens']}>
            {rows.map((b) => (
              <Row key={b.key}>
                <Cell className="font-medium text-ink">{b.label}</Cell>
                <Cell className="tnum">{b.calls.toLocaleString()}</Cell>
                <Cell className="tnum">{tokens(b.tokensIn)}</Cell>
                <Cell className="tnum">{tokens(b.tokensOut)}</Cell>
                {priced
                  ? <Cell className={cn('tnum', b.cost === 'unpriced' ? 'text-dim' : 'text-ink')}>{b.cost}</Cell>
                  : <Cell className={cn('tnum', b.failures && 'text-warn')}>{b.failures ? b.failures.toLocaleString() : 'none'}</Cell>}
                <Cell><span className="flex items-center gap-2"><Bar className="w-24" pct={share(b.tokensIn + b.tokensOut)} /><span className="tnum text-[12px]">{share(b.tokensIn + b.tokensOut)}%</span></span></Cell>
              </Row>
            ))}
          </DataTable>
        )}
      </Panel>
    </section>
  );
}

/** Dollars a cache saved: a sum over priced calls, unknown when none of them had a price. */
function saved(line: CacheLine): string {
  if (line.savedUsd === null) return 'unpriced';
  const n = line.savedUsd;
  const text = n === 0 ? '$0' : n < 0.01 ? `$${n.toFixed(4)}` : `$${n.toFixed(2)}`;
  return line.costComplete ? text : `≥ ${text}`;
}

const share = (line: CacheLine) => (line.cacheShare === null ? null : Math.round(line.cacheShare * 100));

/* What a provider's prompt cache took off the bill, and what the models spent reasoning — both as the
   providers reported them. A lane that reports no cache hits shows none; nothing here is estimated. */
function CacheAndReasoning({ r }: { r: SpendReport }) {
  const [by, setBy] = useState<'lane' | 'feature'>('lane');
  const all = r.cache.totals;
  if (!all) return null;
  const rows: (CacheLine & { key: string; label: string })[] = by === 'lane'
    ? r.cache.byLane.map((l) => ({ ...l, key: l.lane, label: l.lane }))
    : r.cache.byFeature.map((f) => ({ ...f, key: f.feature, label: feature(f.feature) }));
  const hit = share(all);
  return (
    <section className="space-y-3">
      <SectionTitle right={<span className="text-[12.5px] text-dim">model calls · as providers reported</span>}>Prompt cache and reasoning</SectionTitle>
      <StatGrid cols={3}>
        <Stat label="Served from cache" value={hit === null ? '—' : `${hit}%`} tone={hit ? 'ok' : undefined}
          sub={`${tokens(all.tokensCached)} of ${tokens(all.tokensIn)} input tokens`} />
        <Stat label="Saved by the cache" value={saved(all)} tone={all.savedUsd ? 'ok' : undefined}
          sub="vs the same tokens uncached" />
        <Stat label="Reasoning" value={tokens(all.tokensReasoning)}
          sub={all.tokensOut ? `${Math.round((100 * all.tokensReasoning) / all.tokensOut)}% of ${tokens(all.tokensOut)} output tokens · paid, not shown` : 'no output tokens'} />
      </StatGrid>
      <div className="flex flex-wrap items-center gap-2">
        <Segmented options={[{ id: 'lane', label: 'By lane' }, { id: 'feature', label: 'By feature' }]} value={by} onChange={setBy} />
        {all.tokensCached === 0 && <span className="ml-auto text-[12.5px] text-dim">No provider reported a cache hit in this window.</span>}
      </div>
      <Panel flush>
        <DataTable head={[by === 'lane' ? 'Lane' : 'Feature', 'Calls', 'Input', 'From cache', 'Saved', 'Reasoning', 'Cost']}>
          {rows.map((line) => {
            const pct = share(line);
            return (
              <Row key={line.key}>
                <Cell className="font-medium text-ink">{line.label}</Cell>
                <Cell className="tnum">{line.calls.toLocaleString()}</Cell>
                <Cell className="tnum">{tokens(line.tokensIn)}</Cell>
                <Cell>{pct === null ? <span className="text-dim">—</span> : (
                  <span className="flex items-center gap-2"><Bar className="w-20" pct={pct} tone="ok" /><span className="tnum text-[12px]">{pct}%</span></span>
                )}</Cell>
                <Cell className={cn('tnum', line.savedUsd ? 'text-ok' : line.savedUsd === null ? 'text-dim' : 'text-ink-2')}>{saved(line)}</Cell>
                <Cell className="tnum">{line.tokensReasoning ? tokens(line.tokensReasoning) : <span className="text-dim">none</span>}</Cell>
                <Cell className={cn('tnum', line.costUsd === null ? 'text-dim' : 'text-ink')}>{usd(line)}</Cell>
              </Row>
            );
          })}
        </DataTable>
      </Panel>
    </section>
  );
}

function Costliest({ r }: { r: SpendReport }) {
  const { catalogue } = useAccess();
  const anyCost = r.costliest.some((c) => (c.costUsd ?? 0) > 0);
  return (
    <Panel
      flush
      title="Costliest calls"
      eyebrow={anyCost ? `the ${r.costliest.length} most expensive` : 'no known cost · largest by tokens'}
    >
      <DataTable head={['When', 'Call', 'Run', 'Answered by', 'Tokens', 'Cost']}>
        {r.costliest.map((c, i) => (
          <Row key={`${c.at}:${i}`}>
            <Cell className="whitespace-nowrap text-soft">{ago(c.at)}</Cell>
            <Cell className="font-medium text-ink">
              {feature(c.feature)}
              {c.agent && <span className="mt-0.5 block text-[12px] font-normal text-dim">{agentName(catalogue, c.agent)}</span>}
            </Cell>
            <Cell>
              {c.runRef ? (
                <span className="flex flex-wrap items-center gap-1"><Mono>{c.runRef}</Mono>{c.taskRef && <Mono tone="brand">{c.taskRef}</Mono>}</span>
              ) : <span className="text-[12.5px] text-dim">not in a run</span>}
            </Cell>
            <Cell className={cn('text-[13px]', c.lane === 'rules' ? 'text-ok' : 'text-ink-2')}>{c.lane === 'rules' ? 'offline rules' : `${c.lane} · ${c.model}`}</Cell>
            <Cell className="tnum">{tokens(c.tokensIn + c.tokensOut)}</Cell>
            <Cell className={cn('tnum', c.costUsd === null ? 'text-dim' : 'text-ink')}>{usd({ costUsd: c.costUsd, costComplete: true })}</Cell>
          </Row>
        ))}
      </DataTable>
    </Panel>
  );
}

function Latest({ r }: { r: SpendReport }) {
  const { recent, byPerson } = r;
  if (!recent.length) return null;
  return (
    <Panel flush title="Latest calls" eyebrow={`newest first${byPerson ? ` · ${byPerson.length} ${byPerson.length === 1 ? 'person' : 'people'} used AI` : ''}`}>
      <DataTable head={['When', 'Feature', 'Answered by', 'Tokens', 'Time', ...(byPerson ? ['By'] : [])]}>
        {recent.map((c, i) => (
          <Row key={`${c.at}:${i}`}>
            <Cell className="whitespace-nowrap text-soft">{ago(c.at)}</Cell>
            <Cell className="text-ink">{feature(c.feature)}</Cell>
            <Cell>
              <span className="flex items-center gap-1.5" title={c.error || undefined}>
                {!c.ok && <Tag tone="warn">failed</Tag>}
                <span className={cn('text-[13px]', c.provider === 'rules' ? 'text-ok' : 'text-ink-2')}>{c.provider === 'rules' ? 'offline rules' : c.model}</span>
              </span>
            </Cell>
            <Cell className="tnum">{c.tokensIn + c.tokensOut ? tokens(c.tokensIn + c.tokensOut) : 'none'}</Cell>
            <Cell className="tnum">{c.ms} ms</Cell>
            {byPerson && <Cell className="text-soft">{c.by ?? 'Nobody signed in'}</Cell>}
          </Row>
        ))}
      </DataTable>
    </Panel>
  );
}
