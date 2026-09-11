import { useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { ExternalLink, RefreshCw, AlertTriangle, Scale, ShieldAlert, Search } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Page, PageHeader, PageBody, Panel, Stat, StatGrid, Tag, RiskPill, Dot, Mono,
  DataTable, Row, Cell, MeterRow, Segmented, KV, BlockBar, Empty, SectionTitle,
} from '@/components/os';
import { projects, getProject } from '@/mock/projects';
import { getModules, rulesByProject, adrsByProject, riskyByProject, infraByProject } from '@/mock/modules';
import { useData } from '@/lib/data';
import { agentName } from '@/mock/agents';
import { cn } from '@/lib/utils';

type Tab = 'modules' | 'rules' | 'decisions' | 'risk';

const RENEWAL_TONE = { legacy: 'neutral', 'in-renewal': 'warn', renewed: 'ok' } as const;
const ADR_TONE = { accepted: 'ok', proposed: 'warn', superseded: 'neutral', rejected: 'danger' } as const;
const SEV_TONE = { blocker: 'danger', major: 'warn', minor: 'neutral' } as const;

export default function ProjectOverview() {
  const { projectId } = useParams();
  const nav = useNavigate();
  const p = useMemo(() => (projectId && projects.some((x) => x.id === projectId) ? getProject(projectId) : projects[0]), [projectId]);
  const [tab, setTab] = useState<Tab>('modules');
  const [q, setQ] = useState('');

  const modules = useMemo(() => {
    const list = getModules(p.id);
    const s = q.trim().toLowerCase();
    return s ? list.filter((m) => (m.name + m.path + m.note + m.tables.join(' ')).toLowerCase().includes(s)) : list;
  }, [p.id, q]);

  const rules = rulesByProject[p.id] ?? rulesByProject.erp;
  const adrs = adrsByProject[p.id] ?? adrsByProject.erp;
  const risky = riskyByProject[p.id] ?? riskyByProject.erp;
  const infra = infraByProject[p.id] ?? infraByProject.erp;
  const { tasks } = useData();
  const myTasks = useMemo(() => tasks.filter((t) => t.projectId === p.id), [tasks, p.id]);

  return (
    <Page>
      <PageHeader
        title={p.name}
        subtitle={p.description}
        actions={
          <>
            <Button size="sm" variant="outline" onClick={() => nav('/code')}><ExternalLink className="size-3.5" />Code intelligence</Button>
            <Button size="sm" variant="outline"><RefreshCw className="size-3.5" />Re-onboard</Button>
          </>
        }
      >
        <div className="flex flex-wrap items-center gap-3 pb-3">
          <span className="flex items-center gap-1.5"><Dot state={p.status} pulse={p.status === 'active'} /><span className="text-[12px] text-ink-2 capitalize">{p.status}</span></span>
          <Mono>{p.codename}</Mono>
          <Mono>{p.repo}</Mono>
          <div className="flex flex-wrap gap-1">
            {p.stack.map((s) => <span key={s} className="rounded-xs border border-line bg-surface-2 px-1.5 py-px text-[10.5px] text-ink-2">{s}</span>)}
          </div>
        </div>
      </PageHeader>

      <PageBody className="space-y-4">
        {/* Understanding */}
        <div className="grid grid-cols-12 gap-3">
          <Panel eyebrow="How well the OS knows this codebase" title={`Project understood: ${p.understoodPct}%`} className="col-span-12 xl:col-span-5">
            {p.coverage.map((c) => <MeterRow key={c.label} label={c.label} pct={c.pct} />)}
            <p className="mt-2 border-t border-line pt-2 text-[11px] text-dim">
              Understanding is measured against what the OS can prove — parsed symbols, mapped tables, cited decisions —
              not against how much it has read.
            </p>
          </Panel>

          <div className="col-span-12 space-y-3 xl:col-span-7">
            <StatGrid cols={4}>
              <Stat label="Tasks" value={p.work.tasks} sub={`${myTasks.filter((t) => t.status === 'done').length} done all-time`} />
              <Stat label="Running" value={p.work.running} tone="ok" sub="agents in worktrees" />
              <Stat label="In review" value={p.work.review} tone="warn" sub="awaiting reviewer or you" />
              <Stat label="Blocked" value={p.work.blocked} tone={p.work.blocked ? 'danger' : 'neutral'} sub="need a human decision" />
            </StatGrid>
            <Panel eyebrow="Stack & infrastructure" title="Environment" flush>
              <div className="grid grid-cols-1 gap-x-6 px-3.5 py-1.5 md:grid-cols-2">
                {infra.map((i) => <KV key={i.k} k={i.k} v={i.v} mono={i.mono} />)}
              </div>
            </Panel>
          </div>
        </div>

        {/* Tabs */}
        <div className="flex flex-wrap items-center gap-2">
          <Segmented
            options={[
              { id: 'modules', label: `Modules (${getModules(p.id).length})` },
              { id: 'rules', label: `Rules (${rules.length})` },
              { id: 'decisions', label: `Decisions (${adrs.length})` },
              { id: 'risk', label: `Risky areas (${risky.length})` },
            ]}
            value={tab}
            onChange={setTab}
          />
          {tab === 'modules' && (
            <div className="flex h-7 w-72 items-center gap-2 rounded-sm border border-line bg-surface-2 px-2.5 focus-within:border-brand">
              <Search className="size-3.5 shrink-0 text-dim" />
              <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter modules, paths, tables…"
                className="min-w-0 flex-1 bg-transparent text-[12px] text-ink placeholder:text-dim focus-visible:outline-none" />
            </div>
          )}
        </div>

        {tab === 'modules' && (
          <Panel flush>
            {modules.length === 0 ? <Empty title="No module matches" hint="Try a table name like MST_TAX." /> : (
              <DataTable head={['Module', 'Path', 'LOC', 'Understood', 'Coverage', 'Risk', 'Renewal', 'Owner', 'Bugs']}>
                {modules.map((m) => (
                  <Row key={m.id} onClick={() => nav('/code')}>
                    <Cell className="font-medium text-ink">
                      {m.name}
                      <span className="mt-0.5 block max-w-[420px] truncate text-[11px] font-normal text-dim">{m.note}</span>
                    </Cell>
                    <Cell mono className="text-dim">{m.path}</Cell>
                    <Cell className="tnum">{(m.loc / 1000).toFixed(1)}k</Cell>
                    <Cell><span className="flex items-center gap-2"><BlockBar pct={m.understood} width={8} /><span className="tnum text-[11px]">{m.understood}%</span></span></Cell>
                    <Cell><span className="flex items-center gap-2"><BlockBar pct={m.coverage} width={8} /><span className="tnum text-[11px]">{m.coverage}%</span></span></Cell>
                    <Cell><RiskPill risk={m.risk} bare /></Cell>
                    <Cell><Tag tone={RENEWAL_TONE[m.renewal]}>{m.renewal}</Tag></Cell>
                    <Cell className="text-[11.5px]">{agentName(m.owner)}</Cell>
                    <Cell className={cn('tnum', m.openBugs > 3 ? 'text-danger' : m.openBugs ? 'text-warn' : 'text-dim')}>{m.openBugs}</Cell>
                  </Row>
                ))}
              </DataTable>
            )}
          </Panel>
        )}

        {tab === 'rules' && (
          <Panel eyebrow="Enforced automatically — a violation stops the pipeline" title="Project rules" flush>
            <div className="divide-y divide-line">
              {rules.map((r) => (
                <div key={r.id} className="px-3.5 py-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <Tag tone={SEV_TONE[r.severity]}>{r.severity}</Tag>
                    <span className="text-[12.5px] font-medium text-ink">{r.rule}</span>
                    {r.violations24h > 0 && <span className="tnum text-[11px] text-warn">{r.violations24h} caught in 24h</span>}
                  </div>
                  <p className="mt-1 text-[11.5px] text-soft">{r.detail}</p>
                  <p className="mt-1 flex items-center gap-1.5 text-[11px] text-dim"><ShieldAlert className="size-3" />{r.enforcedBy}</p>
                </div>
              ))}
            </div>
          </Panel>
        )}

        {tab === 'decisions' && (
          <Panel eyebrow="Rejected decisions are kept — they are memory too" title="Architecture decision records" flush>
            <div className="divide-y divide-line">
              {adrs.map((a) => (
                <div key={a.id} className="px-3.5 py-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <Mono tone="brand">{a.ref}</Mono>
                    <span className="text-[12.5px] font-medium text-ink">{a.title}</span>
                    <Tag tone={ADR_TONE[a.status]}>{a.status}</Tag>
                    {a.supersedes && <span className="text-[11px] text-dim">supersedes {a.supersedes}</span>}
                  </div>
                  <p className="mt-1 text-[11.5px] text-soft">{a.summary}</p>
                  <p className="mt-1 flex items-center gap-1.5 text-[11px] text-dim"><Scale className="size-3" />{a.by} · {a.at}</p>
                </div>
              ))}
            </div>
          </Panel>
        )}

        {tab === 'risk' && (
          <Panel eyebrow="Ranked by churn × complexity × incident history" title="Risky areas" flush>
            <DataTable head={['Path', 'Risk', 'Churn', 'Complexity', 'Why it is risky', 'Last incident']}>
              {risky.map((r) => (
                <Row key={r.id}>
                  <Cell mono className="text-ink-2">{r.path}</Cell>
                  <Cell><RiskPill risk={r.risk} bare /></Cell>
                  <Cell className="tnum">{r.churn}</Cell>
                  <Cell><span className="flex items-center gap-2"><BlockBar pct={r.complexity} width={8} tone={r.complexity > 80 ? 'danger' : 'warn'} /><span className="tnum text-[11px]">{r.complexity}</span></span></Cell>
                  <Cell className="max-w-[460px] text-[11.5px] text-soft">{r.reason}</Cell>
                  <Cell className="text-dim">{r.lastIncident}</Cell>
                </Row>
              ))}
            </DataTable>
            <div className="flex items-start gap-2 border-t border-line px-3.5 py-2.5">
              <AlertTriangle className="mt-px size-3.5 shrink-0 text-warn" />
              <p className="text-[11.5px] text-soft">
                <span className="text-ink-2">Risk here is advisory, not a blocker.</span> The OS uses it to decide how many
                verification passes a change needs — a CRITICAL path gets three independent reviewers instead of one.
              </p>
            </div>
          </Panel>
        )}

        <SectionTitle>Active tasks in {p.name}</SectionTitle>
        <Panel flush>
          <DataTable head={['Ref', 'Task', 'Status', 'Priority', 'Risk', 'Layers', 'Agents', 'Progress']}>
            {myTasks.map((t) => (
              <Row key={t.id} onClick={() => nav('/tasks')}>
                <Cell mono>{t.ref}</Cell>
                <Cell className="font-medium text-ink">{t.title}</Cell>
                <Cell><span className="flex items-center gap-1.5"><Dot state={t.status} pulse={t.status === 'in_progress'} /><span className="capitalize">{t.status.replace('_', ' ')}</span></span></Cell>
                <Cell><Tag tone={t.priority === 'URGENT' ? 'danger' : t.priority === 'HIGH' ? 'warn' : 'neutral'}>{t.priority}</Tag></Cell>
                <Cell><RiskPill risk={t.risk} bare /></Cell>
                <Cell className="text-[11.5px] text-dim">{t.layers.join(' · ')}</Cell>
                <Cell className="text-[11.5px]">{t.agents.length ? t.agents.map(agentName).join(', ') : '—'}</Cell>
                <Cell><span className="flex items-center gap-2"><BlockBar pct={t.progress} width={10} /><span className="tnum text-[11px]">{t.progress}%</span></span></Cell>
              </Row>
            ))}
          </DataTable>
        </Panel>
      </PageBody>
    </Page>
  );
}
