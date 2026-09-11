import { useMemo, useState } from 'react';
import { TriangleAlert, Rocket, Container as Box, KeyRound, Activity, Terminal } from 'lucide-react';
import { toast } from 'sonner';
import { useDecision } from '@/lib/data';
import { useProject } from '@/lib/project-context';
import { Button } from '@/components/ui/button';
import {
  Page, PageHeader, PageBody, Panel, Tag, Dot, Mono, Segmented, DataTable, Row, Cell,
  Stat, StatGrid, KV, Bar, Empty, SectionTitle, SelectField, StatusText,
} from '@/components/os';
import {
  environments, productionGate, deployments, deployNotes, containers,
  pipeline, pipelineMeta, logs, secrets, infraKv, healthChecks,
} from '@/mock/devops';
import { cn } from '@/lib/utils';

const DEP_TONE = { success: 'ok', failed: 'danger', running: 'info', rolled_back: 'warn', awaiting_approval: 'warn' } as const;

export default function DevOps() {
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
