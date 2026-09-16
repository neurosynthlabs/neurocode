import { useMemo, useState } from 'react';
import { TriangleAlert, Rocket, Container as Box, KeyRound, Activity, Terminal, Loader2, RefreshCw, DatabaseBackup, GitMerge, Trash2, Check, X } from 'lucide-react';
import { toast } from 'sonner';
import { useData, useDecision } from '@/lib/data';
import { ApiError, api } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { useRemote } from '@/lib/remote';
import {
  fetchContainers, fetchDeliveries, fetchLogs, fetchOverview, fetchPipeline, fetchSecrets,
  type LocalService, type LogLevel, type OpsGateItem,
} from '@/lib/live/ops';
import { ago } from './code/format';
import { useProject } from '@/lib/project-context';
import { Button } from '@/components/ui/button';
import {
  Page, PageHeader, PageBody, Panel, Tag, Dot, Mono, Segmented, DataTable, Row, Cell,
  Stat, StatGrid, KV, Bar, Empty, SectionTitle, SelectField, StatusText, RiskPill,
} from '@/components/os';
import {
  environments, productionGate, deployments, deployNotes, containers,
  pipeline, pipelineMeta, logs, secrets, infraKv, healthChecks,
} from '@/mock/devops';
import { cn } from '@/lib/utils';

const DEP_TONE = { success: 'ok', failed: 'danger', running: 'info', rolled_back: 'warn', awaiting_approval: 'warn' } as const;

/** Live: this machine and the runs delivered on it. The demo keeps the worked example of a real estate. */
export default function DevOps() {
  const { mode } = useData();
  return mode === 'live' ? <LiveDevOps /> : <SampleDevOps />;
}

function SampleDevOps() {
  const [tab, setTab] = useState<'env' | 'deploys' | 'containers' | 'pipeline' | 'logs' | 'secrets'>('env');
  const [level, setLevel] = useState('all');
  // The production gate is yours alone, and final: saved as a decision so a reload cannot reopen it.
  const [verdict, decideGate] = useDecision('deploy:production');
  const gate = (verdict ?? 'pending') as 'pending' | 'approved' | 'cancelled';
  const { projectId: activeProject } = useProject();

  const shown = useMemo(() => (level === 'all' ? logs : logs.filter((l) => l.level === level)), [level]);

  return (
    <Page>
      <PageHeader
        title="DevOps"
        subtitle="Staging belongs to the agents. Production belongs to you — and the gate is not negotiable."
        actions={<Segmented
          options={[
            { id: 'env', label: 'Environments' }, { id: 'deploys', label: 'Deployments' },
            { id: 'containers', label: 'Containers' }, { id: 'pipeline', label: 'Pipeline' },
            { id: 'logs', label: 'Logs' }, { id: 'secrets', label: 'Secrets' },
          ]}
          value={tab} onChange={setTab} />}
      />

      <PageBody className="space-y-4">
        {tab === 'env' && (
          <>
            <div className="grid grid-cols-1 gap-3 stagger lg:grid-cols-3">
              {environments.map((e) => (
                <Panel key={e.id} className={cn(e.kind === 'production' && 'accent-top border-warn/35')}
                  eyebrow={e.kind} title={<span className="flex items-center gap-2">{e.name}<StatusText state={e.status} /></span>}
                  actions={e.requiresApproval ? <Tag tone="warn">gated</Tag> : <Tag tone="ok">agent-owned</Tag>}>
                  <Mono className="mb-2 block truncate">{e.url}</Mono>
                  <div className="grid grid-cols-2 gap-x-5">
                    <KV k="Version" v={e.version} mono />
                    <KV k="Deployed" v={e.deployedAt} />
                    <KV k="Uptime" v={e.uptime} />
                    <KV k="Requests" v={e.requests} />
                  </div>
                  <div className="mt-2.5 space-y-1.5 border-t border-line pt-2.5">
                    <div className="flex items-center gap-2"><span className="w-8 text-[11.5px] text-dim">CPU</span><Bar pct={e.cpu} /><span className="tnum w-8 text-right text-[11.5px]">{e.cpu}%</span></div>
                    <div className="flex items-center gap-2"><span className="w-8 text-[11.5px] text-dim">MEM</span><Bar pct={e.mem} /><span className="tnum w-8 text-right text-[11.5px]">{e.mem}%</span></div>
                    <div className="flex items-center gap-2"><span className="w-8 text-[11.5px] text-dim">ERR</span><Bar pct={Math.min(100, e.errorRate * 20)} tone={e.errorRate > 1 ? 'danger' : 'ok'} /><span className="tnum w-8 text-right text-[11.5px]">{e.errorRate}%</span></div>
                  </div>
                </Panel>
              ))}
            </div>

            <Panel className="border-danger/40 accent-top" eyebrow="Awaiting a human signature"
              title={<span className="flex items-center gap-2 text-[14px]"><TriangleAlert className="size-4 text-danger" />HIGH RISK — production deployment requires approval</span>}>
              <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                <div>
                  <KV k="Version" v={productionGate.version} mono />
                  <KV k="Target" v={productionGate.target} />
                  <SectionTitle className="mt-3 mb-1.5">What ships</SectionTitle>
                  <ul className="space-y-1">
                    {productionGate.contains.map((c) => (
                      <li key={c} className="flex items-start gap-1.5 text-[12.5px] text-ink-2"><span className="mt-1.5 size-1 shrink-0 rounded-full bg-brand" />{c}</li>
                    ))}
                  </ul>
                </div>
                <div>
                  <SectionTitle className="mb-1.5">Why this is dangerous</SectionTitle>
                  <ul className="space-y-1">
                    {productionGate.risks.map((r) => (
                      <li key={r} className="flex items-start gap-1.5 text-[12.5px] text-warn"><TriangleAlert className="mt-px size-3 shrink-0" />{r}</li>
                    ))}
                  </ul>
                  <SectionTitle className="mt-3 mb-1.5">Rollback</SectionTitle>
                  <p className="text-[12.5px] text-soft">{productionGate.rollback}</p>
                </div>
              </div>
              <div className="mt-3 flex items-center gap-2 border-t border-line pt-3">
                {gate === 'pending' ? (
                  <>
                    <Button size="sm" variant="outline" onClick={async () => { if (await decideGate('cancelled', { action: 'Production deploy cancelled', detail: 'Nothing shipped. The release stays on staging.', projectId: activeProject, level: 'warn' })) toast('Deployment cancelled — nothing shipped'); }}>Cancel</Button>
                    <Button size="sm" onClick={async () => { if (await decideGate('approved', { action: 'Production deploy approved', detail: 'Rolling restart across 3 nodes, rollback armed.', projectId: activeProject, level: 'ok' })) toast.success('Production deploy approved', { description: 'Rolling restart across 3 nodes, rollback armed.' }); }}>
                      <Rocket className="size-3.5" />Approve deployment
                    </Button>
                    <span className="ml-auto text-[12px] text-dim">Your signature is recorded against this version.</span>
                  </>
                ) : (
                  <Tag tone={gate === 'approved' ? 'ok' : 'neutral'}>{gate === 'approved' ? 'approved by you · rolling out' : 'cancelled by you'}</Tag>
                )}
              </div>
            </Panel>

            <Panel eyebrow="Continuous" title="Health checks" flush>
              <DataTable head={['Check', 'Environment', 'Target', 'Status', 'Latency', 'Last run', 'Note']}>
                {healthChecks.map((h) => (
                  <Row key={h.id}>
                    <Cell className="font-medium text-ink">{h.name}</Cell>
                    <Cell><Tag tone={h.env === 'production' ? 'warn' : 'neutral'}>{h.env}</Tag></Cell>
                    <Cell mono className="text-dim">{h.target}</Cell>
                    <Cell><StatusText state={h.status} /></Cell>
                    <Cell className="tnum">{h.latencyMs}ms</Cell>
                    <Cell className="text-dim">{h.lastRun}</Cell>
                    <Cell className="max-w-[360px] text-[12.5px] text-soft">{h.note}</Cell>
                  </Row>
                ))}
              </DataTable>
            </Panel>

            <Panel eyebrow="Infrastructure" title="Runtime" flush>
              <div className="grid grid-cols-1 gap-x-8 px-3.5 py-1.5 md:grid-cols-2 xl:grid-cols-3">
                {infraKv.map((i) => <KV key={i.k} k={i.k} v={i.v} />)}
              </div>
            </Panel>
          </>
        )}

        {tab === 'deploys' && (
          <>
            <StatGrid cols={4}>
              <Stat label="Deploys today" value={deployments.filter((d) => d.at.includes('today') || !d.at.includes('ago')).length} />
              <Stat label="Succeeded" value={deployments.filter((d) => d.status === 'success').length} tone="ok" />
              <Stat label="Rolled back" value={deployments.filter((d) => d.status === 'rolled_back').length} tone="warn" />
              <Stat label="Blocked on you" value={deployments.filter((d) => d.status === 'awaiting_approval').length} tone="danger" />
            </StatGrid>
            <Panel flush>
              <DataTable head={['Env', 'Version', 'Status', 'Triggered by', 'Duration', 'Commit', 'When']}>
                {deployments.map((d) => (
                  <Row key={d.id}>
                    <Cell><Tag tone={d.env === 'production' ? 'warn' : d.env === 'staging' ? 'info' : 'neutral'}>{d.env}</Tag></Cell>
                    <Cell mono>{d.version}</Cell>
                    <Cell><Tag tone={DEP_TONE[d.status]}>{d.status.replace('_', ' ')}</Tag></Cell>
                    <Cell className="text-[12.5px]">{d.by}</Cell>
                    <Cell className="tnum">{d.durationS}s</Cell>
                    <Cell mono className="text-dim">{d.commit}</Cell>
                    <Cell className="text-dim">{d.at}</Cell>
                  </Row>
                ))}
              </DataTable>
            </Panel>
            <Panel eyebrow="What actually happened" title="Deployment notes" flush>
              <div className="divide-y divide-line">
                {Object.entries(deployNotes).map(([id, text]) => {
                  const d = deployments.find((x) => x.id === id);
                  return (
                    <div key={id} className="flex items-start gap-2.5 px-3.5 py-2">
                      <Dot state={d?.status ?? 'info'} className="mt-1.5" />
                      <span className="min-w-0 flex-1">
                        <Mono>{d ? `${d.env} ${d.version}` : id}</Mono>
                        <span className="ml-2 text-[13px] text-ink-2">{text}</span>
                      </span>
                    </div>
                  );
                })}
              </div>
            </Panel>
          </>
        )}

        {tab === 'containers' && (
          <Panel eyebrow={`${containers.filter((c) => c.status === 'up').length} of ${containers.length} up`} title={<span className="flex items-center gap-1.5"><Box className="size-3.5 text-brand" />Containers</span>} flush>
            <DataTable head={['Name', 'Image', 'Status', 'CPU', 'Memory', 'Ports']}>
              {containers.map((c) => (
                <Row key={c.id}>
                  <Cell className="font-medium text-ink">{c.name}</Cell>
                  <Cell mono className="text-dim">{c.image}</Cell>
                  <Cell><StatusText state={c.status} /></Cell>
                  <Cell><span className="flex items-center gap-2"><Bar className="w-16" pct={c.cpu} /><span className="tnum text-[12px]">{c.cpu}%</span></span></Cell>
                  <Cell className="tnum">{c.mem}</Cell>
                  <Cell mono className="text-dim">{c.ports}</Cell>
                </Row>
              ))}
            </DataTable>
          </Panel>
        )}

        {tab === 'pipeline' && (
          <>
            <Panel eyebrow={`${pipelineMeta.ref} · ${pipelineMeta.trigger}`} title="CI pipeline"
              actions={<span className="text-[12.5px] text-dim">{pipelineMeta.runner} · {pipelineMeta.elapsed}</span>}>
              <div className="flex flex-wrap items-stretch gap-1.5">
                {pipeline.map((s, i) => (
                  <div key={s.id} className="flex items-center gap-1.5">
                    <div className={cn('min-w-[104px] rounded-sm border px-2.5 py-2',
                      s.state === 'pass' ? 'border-ok/30 bg-ok/8' :
                      s.state === 'fail' ? 'border-danger/35 bg-danger/8' :
                      s.state === 'running' ? 'sweep border-brand/40 bg-brand/8' : 'border-line bg-surface-2')}>
                      <div className="flex items-center gap-1.5">
                        <Dot state={s.state} pulse={s.state === 'running'} />
                        <span className="text-[12.5px] font-medium text-ink">{s.name}</span>
                      </div>
                      <div className="tnum mt-1 text-[11.5px] text-dim">{s.durationS ? `${s.durationS}s` : '—'}</div>
                    </div>
                    {i < pipeline.length - 1 && <span className="text-dim">›</span>}
                  </div>
                ))}
              </div>
              <div className="mt-3 divide-y divide-line border-t border-line pt-1">
                {pipeline.map((s) => (
                  <div key={s.id} className="flex items-center gap-3 py-1.5">
                    <span className="w-24 shrink-0 text-[12.5px] text-ink-2">{s.name}</span>
                    <span className="min-w-0 flex-1 truncate text-[12.5px] text-dim">{s.detail}</span>
                    <Tag tone={s.state === 'pass' ? 'ok' : s.state === 'fail' ? 'danger' : s.state === 'running' ? 'info' : 'neutral'}>{s.state}</Tag>
                  </div>
                ))}
              </div>
            </Panel>
          </>
        )}

        {tab === 'logs' && (
          <>
            <div className="flex items-center gap-2">
              <SelectField className="w-40" value={level} onChange={setLevel}
                options={[{ value: 'all', label: 'All levels' }, ...['info', 'ok', 'warn', 'err', 'debug'].map((l) => ({ value: l, label: l }))]} />
              <span className="ml-auto text-[12.5px] text-dim">{shown.length} of {logs.length} lines</span>
            </div>
            <Panel eyebrow="tail -f" title={<span className="flex items-center gap-1.5"><Terminal className="size-3.5 text-brand" />Application log</span>} flush>
              {shown.length === 0 ? <Empty title="Nothing at that level" /> : (
                <div className="max-h-[520px] overflow-y-auto bg-base px-3.5 py-2.5 font-mono text-[12.5px] leading-relaxed">
                  {shown.map((l) => (
                    <div key={l.id} className="flex gap-2.5">
                      <span className="shrink-0 text-dim">{l.t}</span>
                      <span className={cn('w-11 shrink-0 uppercase',
                        l.level === 'err' ? 'text-danger' : l.level === 'warn' ? 'text-warn' : l.level === 'ok' ? 'text-ok' : l.level === 'debug' ? 'text-dim' : 'text-info')}>{l.level}</span>
                      <span className="w-28 shrink-0 truncate text-violet">{l.source}</span>
                      <span className="min-w-0 text-ink-2">{l.text}</span>
                    </div>
                  ))}
                </div>
              )}
            </Panel>
          </>
        )}

        {tab === 'secrets' && (
          <>
            <Panel className="border-warn/30 accent-left" eyebrow="Rule" title={<span className="flex items-center gap-1.5"><KeyRound className="size-3.5 text-warn" />Agents never see a value</span>}>
              <p className="text-[13.5px] leading-relaxed text-ink-2">
                Secrets are referenced by name and resolved at process launch by the runtime. No agent, model or log line
                ever holds the plaintext — permission rule p14 denies reads outright, with no override flag.
              </p>
            </Panel>
            <Panel flush>
              <DataTable head={['Name', 'Scope', 'Store', 'Last rotated', 'Rotation', 'Who can read', 'Used by']}>
                {secrets.map((s) => (
                  <Row key={s.id}>
                    <Cell mono className="text-ink">{s.name}</Cell>
                    <Cell><Tag tone="neutral">{s.scope}</Tag></Cell>
                    <Cell className="text-[12.5px]">{s.store}</Cell>
                    <Cell className="text-dim">{s.lastRotated}</Cell>
                    <Cell className="tnum">{s.rotationDays}d</Cell>
                    <Cell className="text-[12.5px] text-soft">{s.readers.join(', ')}</Cell>
                    <Cell className="max-w-[300px] text-[12.5px] text-dim">{s.usedBy}</Cell>
                  </Row>
                ))}
              </DataTable>
            </Panel>
            <Panel eyebrow="Value column" title={<span className="flex items-center gap-1.5"><Activity className="size-3.5 text-dim" />Deliberately absent</span>}>
              <p className="text-[13px] text-dim">There is no value column, and there is no reveal button. That is the feature.</p>
            </Panel>
          </>
        )}
      </PageBody>
    </Page>
  );
}

/* ── live ─────────────────────────────────────────────────────── */

type LiveTab = 'services' | 'deliveries' | 'containers' | 'pipeline' | 'logs' | 'secrets';
const DELIVERY_TONE = { success: 'ok', failed: 'danger', running: 'info', awaiting_approval: 'warn', ready: 'info', cancelled: 'neutral', discarded: 'neutral' } as const;
const failure = (e: unknown) => (e instanceof ApiError ? e.message : 'The local API did not answer.');
const bytes = (n: number) => (n >= 1024 ** 3 ? `${(n / 1024 ** 3).toFixed(1)} GB` : `${Math.round(n / 1024 ** 2)} MB`);
const span = (s: number) => (s < 60 ? `${s}s` : s < 3600 ? `${Math.floor(s / 60)}m ${s % 60}s` : s < 86_400 ? `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m` : `${Math.floor(s / 86_400)}d ${Math.floor((s % 86_400) / 3600)}h`);

function Loading({ what }: { what: string }) {
  return <Empty icon={<Loader2 className="size-5 animate-spin" />} title={`Reading ${what}…`} />;
}

function Failed({ what, error, retry }: { what: string; error: string; retry: () => void }) {
  return <Empty title={`${what} did not load`} hint={error} action={<Button size="sm" variant="outline" onClick={retry}>Try again</Button>} />;
}

function LiveDevOps() {
  const [tab, setTab] = useState<LiveTab>('services');
  return (
    <Page>
      <PageHeader
        title="DevOps"
        subtitle="This machine, as it is right now: the services it runs, checks probed when you look, the work the agents delivered, and your signature on anything that reaches your checkout."
        actions={<Segmented
          options={[
            { id: 'services', label: 'Services' }, { id: 'deliveries', label: 'Deliveries' },
            { id: 'containers', label: 'Containers' }, { id: 'pipeline', label: 'Pipeline' },
            { id: 'logs', label: 'Logs' }, { id: 'secrets', label: 'Secrets' },
          ]}
          value={tab} onChange={setTab} />}
      />
      <PageBody className="space-y-4">
        {tab === 'services' && <LiveServices />}
        {tab === 'deliveries' && <LiveDeliveries />}
        {tab === 'containers' && <LiveContainers />}
        {tab === 'pipeline' && <LivePipeline />}
        {tab === 'logs' && <LiveLogs />}
        {tab === 'secrets' && <LiveSecrets />}
      </PageBody>
    </Page>
  );
}

function ServiceCard({ s }: { s: LocalService }) {
  return (
    <Panel eyebrow={s.id === 'api' ? 'this process' : 'on this machine'} title={<span className="flex items-center gap-2">{s.name}<StatusText state={s.status} /></span>}>
      <Mono className="mb-2 block truncate">{s.url}</Mono>
      <KV k="Version" v={s.version || '—'} mono />
      {s.startedAt && <KV k="Started" v={ago(s.startedAt)} />}
      {s.uptimeS !== null && <KV k="Uptime" v={span(s.uptimeS)} />}
      {s.cpuPct !== null && <KV k="CPU" v={`${s.cpuPct.toFixed(1)}%`} />}
      {s.rssBytes !== null && <KV k="Memory" v={bytes(s.rssBytes)} />}
      {s.facts.map((f) => <KV key={f.k} k={f.k} v={f.v} />)}
    </Panel>
  );
}

function LiveServices() {
  const { can } = useAuth();
  const { decide } = useData();
  const o = useRemote('ops:overview', fetchOverview);
  const [acting, setActing] = useState<string | null>(null);
  const [backingUp, setBackingUp] = useState(false);

  const act = async (item: OpsGateItem, what: 'approve' | 'deny' | 'merge' | 'discard') => {
    setActing(item.runRef);
    try {
      if (what === 'approve' || what === 'deny') {
        if (!item.approvalRef || !(await decide(item.approvalRef, what))) throw new ApiError('The decision was not saved.', 409);
        toast.success(what === 'approve' ? `${item.runRef} accepted` : `${item.runRef} refused`, { description: what === 'approve' ? 'Ready to merge.' : 'Its branch and worktree are being removed.' });
      } else if (what === 'merge') {
        const done = await api.mergeRun(item.runRef);
        if (done.merged) toast.success(`${item.runRef} merged into ${done.into}`, { description: done.undo ? `Undo: ${done.undo}` : undefined });
        else toast.error(`${item.runRef} collided, and the merge was undone`, { description: done.conflicts.join(', ') });
      } else {
        await api.discardRun(item.runRef);
        toast(`${item.runRef} discarded`, { description: 'The worktree and its branch are removed.' });
      }
    } catch (e) {
      toast.error('Nothing changed', { description: failure(e) });
    } finally {
      setActing(null);
      o.reload();
    }
  };

  const backup = async () => {
    setBackingUp(true);
    try {
      const made = await api.admin.backupDatabase();
      toast.success('Backup taken', { description: `${made.name} · ${bytes(made.bytes)}` });
      o.reload();
    } catch (e) {
      toast.error('No backup taken', { description: failure(e) });
    } finally {
      setBackingUp(false);
    }
  };

  if (o.error) return <Failed what="The overview" error={o.error} retry={o.reload} />;
  if (!o.data) return <Loading what="this machine" />;
  const { services, gate, checks, runtime } = o.data;

  return (
    <>
      <div className="grid grid-cols-1 gap-3 stagger md:grid-cols-2 xl:grid-cols-4">
        {services.map((s) => <ServiceCard key={s.id} s={s} />)}
      </div>

      <Panel className={cn(gate.length > 0 && 'accent-top border-warn/35')} eyebrow="Before anything reaches your checkout" title="Awaiting your signature">
        {gate.length === 0 ? (
          <Empty title="Nothing is waiting on you" hint="A run that stops at its hand-off, or an accepted run not merged yet, appears here with what it ships and how to undo it." />
        ) : (
          <div className="divide-y divide-line/60">
            {gate.map((g) => (
              <div key={g.runRef} className="py-3 first:pt-0 last:pb-0">
                <div className="flex flex-wrap items-center gap-2">
                  <Mono>{g.runRef}</Mono>
                  {g.risk && <RiskPill risk={g.risk} />}
                  <Tag tone={g.kind === 'handoff' ? 'warn' : 'info'}>{g.kind === 'handoff' ? 'at hand-off' : 'accepted, not merged'}</Tag>
                  <span className="text-[13px] text-ink-2">{g.project}</span>
                  <Mono className="truncate">{g.branch}</Mono>
                </div>
                <div className="mt-2.5 grid grid-cols-1 gap-4 lg:grid-cols-2">
                  <div>
                    <SectionTitle className="mb-1.5">What ships</SectionTitle>
                    <ul className="space-y-1">
                      {g.ships.map((x) => <li key={x} className="flex items-start gap-1.5 text-[12.5px] text-ink-2"><span className="mt-1.5 size-1 shrink-0 rounded-full bg-brand" />{x}</li>)}
                    </ul>
                  </div>
                  <div>
                    <SectionTitle className="mb-1.5">Why it could hurt</SectionTitle>
                    {g.dangers.length === 0 ? <p className="text-[12.5px] text-soft">No HIGH finding, no failed test, no collision.</p> : (
                      <ul className="space-y-1">
                        {g.dangers.map((x) => <li key={x} className="flex items-start gap-1.5 text-[12.5px] text-warn"><TriangleAlert className="mt-px size-3 shrink-0" />{x}</li>)}
                      </ul>
                    )}
                    <SectionTitle className="mt-3 mb-1.5">Undo</SectionTitle>
                    <p className="text-[12.5px] text-soft">{g.undo}</p>
                  </div>
                </div>
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  {g.kind === 'handoff' ? (
                    <>
                      <Button size="sm" variant="outline" disabled={acting === g.runRef || !can('approvals:decide')} title={can('approvals:decide') ? undefined : 'Needs approvals:decide'} onClick={() => void act(g, 'deny')}><X className="size-3.5" />Refuse</Button>
                      <Button size="sm" disabled={acting === g.runRef || !can('approvals:decide')} title={can('approvals:decide') ? undefined : 'Needs approvals:decide'} onClick={() => void act(g, 'approve')}><Check className="size-3.5" />Accept</Button>
                    </>
                  ) : (
                    <>
                      <Button size="sm" variant="outline" disabled={acting === g.runRef || !can('runs:run')} title={can('runs:run') ? undefined : 'Needs runs:run'} onClick={() => void act(g, 'discard')}><Trash2 className="size-3.5" />Discard</Button>
                      <Button size="sm" disabled={acting === g.runRef || !can('runs:merge')} title={can('runs:merge') ? undefined : 'Needs runs:merge'} onClick={() => void act(g, 'merge')}><GitMerge className="size-3.5" />Merge</Button>
                    </>
                  )}
                  {acting === g.runRef && <Loader2 className="size-3.5 animate-spin text-dim" />}
                </div>
              </div>
            ))}
          </div>
        )}
      </Panel>

      <Panel eyebrow={`Probed ${ago(o.data.at)}`} title="Health checks" flush
        actions={
          <div className="flex items-center gap-2">
            {can('workspace:admin') && (
              <Button size="xs" variant="outline" disabled={backingUp} onClick={() => void backup()}>
                {backingUp ? <Loader2 className="size-3 animate-spin" /> : <DatabaseBackup className="size-3" />}Take a backup
              </Button>
            )}
            <Button size="xs" variant="outline" onClick={o.reload}><RefreshCw className="size-3" />Re-run checks</Button>
          </div>
        }>
        <DataTable head={['Check', 'Target', 'Status', 'Latency', 'Note']}>
          {checks.map((h) => (
            <Row key={h.id}>
              <Cell className="font-medium text-ink">{h.name}</Cell>
              <Cell mono className="max-w-[240px] truncate text-dim">{h.target}</Cell>
              <Cell><StatusText state={h.status} /></Cell>
              <Cell className="tnum">{h.latencyMs === null ? '—' : `${h.latencyMs}ms`}</Cell>
              <Cell className="max-w-[420px] text-[12.5px] text-soft">{h.note}</Cell>
            </Row>
          ))}
        </DataTable>
      </Panel>

      <Panel eyebrow="Read from this machine" title="Runtime" flush>
        <div className="grid grid-cols-1 gap-x-8 px-3.5 py-1.5 md:grid-cols-2 xl:grid-cols-3">
          {runtime.map((i) => <KV key={i.k} k={i.k} v={i.v} />)}
        </div>
      </Panel>
    </>
  );
}

function LiveDeliveries() {
  const d = useRemote('ops:deliveries', fetchDeliveries);
  if (d.error) return <Failed what="Deliveries" error={d.error} retry={d.reload} />;
  if (!d.data) return <Loading what="deliveries" />;
  const { items, stats } = d.data;
  const noted = items.filter((x) => x.note);
  return (
    <>
      <StatGrid cols={4}>
        <Stat label="Merged today" value={stats.mergedToday} sub={`${stats.merged} merged in all`} tone="ok" />
        <Stat label="Failed" value={stats.failed} tone={stats.failed ? 'danger' : undefined} />
        <Stat label="Discarded" value={stats.discarded} sub="branch and worktree removed" />
        <Stat label="Blocked on you" value={stats.blockedOnYou} tone={stats.blockedOnYou ? 'warn' : undefined} sub="run approvals pending" />
      </StatGrid>
      <Panel flush>
        {items.length === 0 ? <Empty title="Nothing delivered yet" hint="Dispatch a plan: each run's branch, and what became of it, is listed here." /> : (
          <DataTable head={['Project', 'Branch', 'Status', 'By', 'Duration', 'Commit', 'When']}>
            {items.map((x) => (
              <Row key={x.id}>
                <Cell><span className="flex items-center gap-2"><Mono>{x.id}</Mono><span className="text-[12.5px]">{x.env}</span></span></Cell>
                <Cell mono className="max-w-[240px] truncate">{x.version}</Cell>
                <Cell><Tag tone={DELIVERY_TONE[x.status]}>{x.status.replace('_', ' ')}</Tag></Cell>
                <Cell className="text-[12.5px]">{x.by}</Cell>
                <Cell className="tnum">{x.durationS ? span(x.durationS) : '—'}</Cell>
                <Cell mono className="text-dim">{x.commit || '—'}</Cell>
                <Cell className="text-dim">{ago(x.at)}</Cell>
              </Row>
            ))}
          </DataTable>
        )}
      </Panel>
      {noted.length > 0 && (
        <Panel eyebrow="What each run said" title="Delivery notes" flush>
          <div className="divide-y divide-line">
            {noted.map((x) => (
              <div key={x.id} className="flex items-start gap-2.5 px-3.5 py-2">
                <Dot state={x.status} className="mt-1.5" />
                <span className="min-w-0 flex-1">
                  <Mono>{x.id}</Mono>
                  <span className="ml-2 text-[13px] text-ink-2">{x.note}</span>
                </span>
              </div>
            ))}
          </div>
        </Panel>
      )}
    </>
  );
}

function LiveContainers() {
  const c = useRemote('ops:containers', fetchContainers);
  if (c.error) return <Failed what="Containers" error={c.error} retry={c.reload} />;
  if (!c.data) return <Loading what="Docker" />;
  const { available, reason, containers: rows } = c.data;
  if (!available) return <Panel><Empty icon={<Box className="size-6" />} title="No containers to show" hint={reason} /></Panel>;
  return (
    <Panel eyebrow={`${rows.filter((x) => x.status === 'up').length} of ${rows.length} up · every container on this machine`} title={<span className="flex items-center gap-1.5"><Box className="size-3.5 text-brand" />Docker on this machine</span>} flush>
      {rows.length === 0 ? <Empty title="Docker is running, with no containers" /> : (
        <DataTable head={['Name', 'Image', 'Status', 'CPU', 'Memory', 'Ports']}>
          {rows.map((x) => (
            <Row key={x.id}>
              <Cell className="font-medium text-ink">{x.name}</Cell>
              <Cell mono className="text-dim">{x.image}</Cell>
              <Cell><StatusText state={x.status} /></Cell>
              <Cell><span className="flex items-center gap-2"><Bar className="w-16" pct={x.cpu} /><span className="tnum text-[12px]">{x.cpu}%</span></span></Cell>
              <Cell className="tnum">{x.mem}</Cell>
              <Cell mono className="text-dim">{x.ports}</Cell>
            </Row>
          ))}
        </DataTable>
      )}
    </Panel>
  );
}

function LivePipeline() {
  const p = useRemote('ops:pipeline', () => fetchPipeline());
  if (p.error) return <Failed what="The pipeline" error={p.error} retry={p.reload} />;
  if (!p.data) return <Loading what="the newest run" />;
  const { run, stages, workflow } = p.data;
  return (
    <>
      {run === null ? (
        <Panel><Empty title="No run yet" hint="The newest agent run's steps show here as its pipeline, one stage per step." /></Panel>
      ) : (
        <Panel eyebrow={`${run.ref} · ${run.trigger}`} title="Newest run"
          actions={<span className="text-[12.5px] text-dim">{run.runner} · {span(run.elapsedS)}</span>}>
          {stages.length === 0 ? <Empty title="No steps recorded" /> : (
            <>
              <div className="flex flex-wrap items-stretch gap-1.5">
                {stages.map((s, i) => (
                  <div key={s.id} className="flex items-center gap-1.5">
                    <div className={cn('min-w-[104px] rounded-sm border px-2.5 py-2',
                      s.state === 'pass' ? 'border-ok/30 bg-ok/8' :
                      s.state === 'fail' ? 'border-danger/35 bg-danger/8' :
                      s.state === 'running' ? 'sweep border-brand/40 bg-brand/8' :
                      s.state === 'waiting' ? 'border-warn/35 bg-warn/8' : 'border-line bg-surface-2')}>
                      <div className="flex items-center gap-1.5">
                        <Dot state={s.state} pulse={s.state === 'running'} />
                        <span className="max-w-[180px] truncate text-[12.5px] font-medium text-ink">{s.name}</span>
                      </div>
                      <div className="tnum mt-1 text-[11.5px] text-dim">{s.durationS ? `${s.durationS}s` : '—'}</div>
                    </div>
                    {i < stages.length - 1 && <span className="text-dim">›</span>}
                  </div>
                ))}
              </div>
              <div className="mt-3 divide-y divide-line border-t border-line pt-1">
                {stages.map((s) => (
                  <div key={s.id} className="flex items-center gap-3 py-1.5">
                    <span className="w-40 shrink-0 truncate text-[12.5px] text-ink-2">{s.name}</span>
                    <span className="min-w-0 flex-1 truncate text-[12.5px] text-dim">{s.detail}</span>
                    <Tag tone={s.state === 'pass' ? 'ok' : s.state === 'fail' ? 'danger' : s.state === 'running' ? 'info' : s.state === 'waiting' ? 'warn' : 'neutral'}>{s.state}</Tag>
                  </div>
                ))}
              </div>
            </>
          )}
        </Panel>
      )}
      <Panel eyebrow={workflow.path} title="CI workflow"
        actions={workflow.present && <Tag tone={workflow.pushed ? 'ok' : 'neutral'}>{workflow.pushed ? 'pushed' : workflow.tracked ? 'committed' : 'not committed'}</Tag>}>
        <p className="text-[13px] text-ink-2">{workflow.note}</p>
        {workflow.steps.length > 0 && (
          <ol className="mt-2.5 space-y-1 border-t border-line pt-2.5">
            {workflow.steps.map((s, i) => (
              <li key={`${i}:${s}`} className="flex gap-2.5 text-[12.5px] text-soft"><span className="tnum w-4 shrink-0 text-right font-mono text-dim">{i + 1}</span>{s}</li>
            ))}
          </ol>
        )}
      </Panel>
    </>
  );
}

function LiveLogs() {
  const [level, setLevel] = useState<'all' | LogLevel>('all');
  const [before, setBefore] = useState<string[]>([]);
  const cursor = before[before.length - 1] ?? null;
  const l = useRemote(`ops:logs:${level}:${cursor ?? ''}`, () => fetchLogs(level === 'all' ? null : level, cursor));
  const lines = l.data?.lines ?? [];
  return (
    <>
      <div className="flex flex-wrap items-center gap-2">
        <SelectField className="w-40" value={level} onChange={(v) => { setLevel(v as 'all' | LogLevel); setBefore([]); }}
          options={[{ value: 'all', label: 'All levels' }, ...(['info', 'ok', 'warn', 'err', 'debug'] as const).map((x) => ({ value: x, label: x }))]} />
        <span className="ml-auto text-[12.5px] text-dim">{lines.length} lines{cursor ? ` before ${new Date(cursor.split('|')[0]).toLocaleString()}` : ''}</span>
        {before.length > 0 && <Button size="xs" variant="outline" onClick={() => setBefore((b) => b.slice(0, -1))}>Newer</Button>}
        <Button size="xs" variant="outline" disabled={!l.data?.next} onClick={() => { const next = l.data?.next; if (next) setBefore((b) => [...b, next]); }}>Older</Button>
        <Button size="xs" variant="outline" onClick={l.reload}><RefreshCw className="size-3" />Refresh</Button>
      </div>
      <Panel eyebrow="Run output, activity and failed model calls" title={<span className="flex items-center gap-1.5"><Terminal className="size-3.5 text-brand" />Workspace log</span>} flush>
        {l.error ? <Failed what="The log" error={l.error} retry={l.reload} />
          : !l.data ? <Loading what="the log" />
            : lines.length === 0 ? <Empty title="Nothing at that level" /> : (
              <div className="max-h-[520px] overflow-y-auto bg-base px-3.5 py-2.5 font-mono text-[12.5px] leading-relaxed">
                {lines.map((x) => (
                  <div key={x.id} className="flex gap-2.5">
                    <span className="shrink-0 text-dim">{new Date(x.t).toLocaleTimeString()}</span>
                    <span className={cn('w-11 shrink-0 uppercase',
                      x.level === 'err' ? 'text-danger' : x.level === 'warn' ? 'text-warn' : x.level === 'ok' ? 'text-ok' : x.level === 'debug' ? 'text-dim' : 'text-info')}>{x.level}</span>
                    <span className="w-28 shrink-0 truncate text-violet">{x.source}</span>
                    <span className="min-w-0 break-words text-ink-2">{x.text}</span>
                  </div>
                ))}
              </div>
            )}
      </Panel>
    </>
  );
}

function LiveSecrets() {
  const { can } = useAuth();
  const allowed = can('workspace:admin');
  const s = useRemote(allowed ? 'ops:secrets' : null, fetchSecrets);
  return (
    <>
      <Panel className="border-warn/30 accent-left" eyebrow="Rule" title={<span className="flex items-center gap-1.5"><KeyRound className="size-3.5 text-warn" />Named, never shown</span>}>
        <p className="text-[13.5px] leading-relaxed text-ink-2">
          Model keys live in server/secrets.json (mode 0600) or the environment, and the database password in its URL.
          This list says where each one is kept and whether it is set. No value and no mask of one ever leaves the API.
        </p>
      </Panel>
      {!allowed ? (
        <Panel><Empty icon={<KeyRound className="size-6" />} title="Only a workspace admin can see this list" hint="It needs workspace:admin, like Admin → AI providers." /></Panel>
      ) : s.error ? <Failed what="Secrets" error={s.error} retry={s.reload} />
        : !s.data ? <Loading what="where the keys are kept" /> : (
          <Panel flush>
            <DataTable head={['Name', 'Store', 'State', 'Last set here', 'Who can replace it', 'Used by']}>
              {s.data.map((x) => (
                <Row key={x.id}>
                  <Cell mono className="text-ink">{x.name}
                    {x.note && <span className="mt-0.5 block max-w-[280px] font-sans text-[11.5px] whitespace-normal text-warn">{x.note}</span>}
                  </Cell>
                  <Cell className="text-[12.5px]">{x.store}</Cell>
                  <Cell>{x.rejected ? <Tag tone="danger">refused by provider</Tag> : x.set ? <Tag tone="ok">set</Tag> : <Tag tone="neutral">not set</Tag>}</Cell>
                  <Cell className="text-dim">{x.lastSetAt ? ago(x.lastSetAt) : '—'}</Cell>
                  <Cell className="text-[12.5px] text-soft">{x.replaceableBy.join(', ') || '—'}</Cell>
                  <Cell className="max-w-[300px] text-[12.5px] text-dim">{x.usedBy}</Cell>
                </Row>
              ))}
            </DataTable>
          </Panel>
        )}
      <Panel eyebrow="Value column" title={<span className="flex items-center gap-1.5"><Activity className="size-3.5 text-dim" />Deliberately absent</span>}>
        <p className="text-[13px] text-dim">There is no value column, no mask and no reveal button. Replace a key in Admin → AI providers.</p>
      </Panel>
    </>
  );
}
