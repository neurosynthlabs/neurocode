import { useMemo, useState } from 'react';
import { ShieldCheck, ShieldAlert, ShieldX, Lock, Check, X, Search, Box } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import {
  Page, PageHeader, PageBody, Panel, Tag, RiskPill, Mono, Toolbar, Field, SelectField,
  DataTable, Row, Cell, Stat, StatGrid, KV, Empty, SectionTitle, Segmented,
} from '@/components/os';
import { permissionRules } from '@/mock/permissions';
import { useData } from '@/lib/data';
import { projectName } from '@/mock/projects';
import { cn } from '@/lib/utils';

const TIERS = [
  { tier: 'LOW', tone: 'ok' as const, icon: ShieldCheck, verdict: 'automatic',
    what: 'read files · search · run tests · write documentation · inspect git · query a snapshot replica',
    why: 'Reading and verifying cost nothing and cannot destroy anything. Gating them would only make the OS slower and dumber.' },
  { tier: 'MEDIUM', tone: 'warn' as const, icon: ShieldAlert, verdict: 'reviewer gate',
    what: 'code changes inside a worktree · new dependencies · config edits · staging deploys · creating an external ticket',
    why: 'Reversible, but visible to other people or to the build. The Code Reviewer must pass it before it can land.' },
  { tier: 'HIGH', tone: 'danger' as const, icon: ShieldX, verdict: 'YOU must approve',
    what: 'database migrations · production deploys · data mutations · security configuration · outbound messages',
    why: 'Consequences leave the machine. No agent signs these — the signature is yours, every time.' },
];

const EFFECT_TONE = { allow: 'ok', ask: 'warn', deny: 'danger' } as const;

export default function Permissions() {
  const [tab, setTab] = useState<'inbox' | 'rules' | 'sandbox'>('inbox');
  const [effect, setEffect] = useState('all');
  const [tool, setTool] = useState('all');
  const [q, setQ] = useState('');
  const { approvals, decide: record } = useData();

  const tools = useMemo(() => ['all', ...Array.from(new Set(permissionRules.map((r) => r.tool)))], []);
  const rules = useMemo(() => {
    const t = q.trim().toLowerCase();
    return permissionRules.filter((r) =>
      (effect === 'all' || r.effect === effect) &&
      (tool === 'all' || r.tool === tool) &&
      (!t || (r.pattern + r.note + r.tool).toLowerCase().includes(t)));
  }, [effect, tool, q]);

  const pending = approvals.filter((a) => a.status === 'pending');
  const resolved = approvals.filter((a) => a.status !== 'pending');

  const decide = async (ref: string, v: 'approve' | 'deny') => {
    if (!(await record(ref, v))) return;
    if (v === 'approve') toast.success(`${ref} approved`, { description: 'Your signature is recorded against this action.' });
    else toast(`${ref} denied`, { description: 'The agent is told why, and will not retry silently.' });
  };

  return (
    <Page>
      <PageHeader
        title="Permissions"
        subtitle="What an agent may do alone, what needs a reviewer, and what will always stop at your desk."
        actions={<Segmented options={[
          { id: 'inbox', label: `Inbox (${pending.length})` },
          { id: 'rules', label: `Rules (${permissionRules.length})` },
          { id: 'sandbox', label: 'Sandbox' },
        ]} value={tab} onChange={setTab} />}
      />

      <PageBody className="space-y-4">
        <div className="grid grid-cols-1 gap-3 stagger lg:grid-cols-3">
          {TIERS.map((t) => {
            const I = t.icon;
            const n = permissionRules.filter((r) => (t.tier === 'HIGH' ? r.risk === 'HIGH' || r.risk === 'CRITICAL' : r.risk === t.tier)).length;
            return (
              <Panel key={t.tier} className={cn('accent-top', t.tone === 'danger' && 'border-danger/35', t.tone === 'warn' && 'border-warn/30')}
                eyebrow={`${n} rules`} title={<span className="flex items-center gap-2"><I className={cn('size-4', `text-${t.tone}`)} />{t.tier} risk</span>}
                actions={<Tag tone={t.tone}>{t.verdict}</Tag>}>
                <p className="text-[11.5px] text-ink-2">{t.what}</p>
                <p className="mt-2 border-t border-line pt-2 text-[11.5px] text-dim">{t.why}</p>
              </Panel>
            );
          })}
        </div>

        {tab === 'inbox' && (
          <>
            <StatGrid cols={4}>
              <Stat label="Waiting on you" value={pending.length} tone={pending.length ? 'warn' : 'ok'} sub="nothing moves until you decide" />
              <Stat label="Approved 7d" value={14} tone="ok" sub="median decision 6m 12s" />
              <Stat label="Denied 7d" value={3} tone="danger" sub="each one wrote a memory fact" />
              <Stat label="Auto-allowed 24h" value={permissionRules.filter((r) => r.effect === 'allow').reduce((n, r) => n + r.hits24h, 0).toLocaleString()} sub="never reached you" />
            </StatGrid>

            {pending.length === 0 ? (
              <Empty icon={<ShieldCheck className="size-6" />} title="Nothing is waiting on you" hint="Every gated action has been decided. Agents are free to keep working." />
            ) : (
              <div className="space-y-3 stagger">
                {pending.map((a) => (
                  <Panel key={a.id} className={cn('accent-left', a.risk === 'CRITICAL' ? 'border-danger/40' : 'border-warn/35')}
                    eyebrow={`${a.agent} · ${a.tool} · requested ${a.requestedAt}`}
                    title={<span className="flex flex-wrap items-center gap-2"><Mono tone="brand">{a.ref}</Mono>{a.title}</span>}
                    actions={<span className="flex items-center gap-2"><Tag tone="neutral">{projectName(a.projectId)}</Tag><RiskPill risk={a.risk} /></span>}>
                    <SectionTitle>What will run</SectionTitle>
                    <pre className="ascii rounded-sm border border-line bg-base p-3 whitespace-pre-wrap">{a.payload}</pre>
                    <SectionTitle className="mt-3">Why the agent is asking</SectionTitle>
                    <p className="text-[12.5px] leading-relaxed text-ink-2">{a.reason}</p>
                    <div className="mt-3 flex items-center gap-2 border-t border-line pt-3">
                      <Button size="sm" onClick={() => decide(a.ref, 'approve')}><Check className="size-3.5" />Approve</Button>
                      <Button size="sm" variant="destructive" onClick={() => decide(a.ref, 'deny')}><X className="size-3.5" />Deny</Button>
                      <span className="ml-auto text-[11px] text-dim">Approving records your name against this exact payload.</span>
                    </div>
                  </Panel>
                ))}
              </div>
            )}

            {resolved.length > 0 && (
              <>
                <SectionTitle>Decided</SectionTitle>
                <Panel flush>
                  <DataTable head={['Ref', 'Title', 'Agent', 'Tool', 'Risk', 'Outcome']}>
                    {resolved.map((a) => {
                      const st = a.status;
                      return (
                        <Row key={a.id}>
                          <Cell mono>{a.ref}</Cell>
                          <Cell className="max-w-[420px] text-ink">{a.title}</Cell>
                          <Cell className="text-[11.5px]">{a.agent}</Cell>
                          <Cell mono className="text-dim">{a.tool}</Cell>
                          <Cell><RiskPill risk={a.risk} bare /></Cell>
                          <Cell><Tag tone={st === 'approved' ? 'ok' : 'danger'}>{st}</Tag></Cell>
                        </Row>
                      );
                    })}
                  </DataTable>
                </Panel>
              </>
            )}
          </>
        )}

        {tab === 'rules' && (
          <>
            <Toolbar>
              <Field className="w-64" value={q} onChange={setQ} icon={<Search className="size-3.5" />} placeholder="Search patterns, tools, notes…" onClear={() => setQ('')} />
              <SelectField className="w-36" value={effect} onChange={setEffect}
                options={[{ value: 'all', label: 'Any effect' }, { value: 'allow', label: 'allow' }, { value: 'ask', label: 'ask' }, { value: 'deny', label: 'deny' }]} />
              <SelectField className="w-40" value={tool} onChange={setTool} options={tools.map((t) => ({ value: t, label: t === 'all' ? 'Any tool' : t }))} />
              <span className="ml-auto text-[11.5px] text-dim">{rules.length} of {permissionRules.length}</span>
            </Toolbar>
            <Panel flush>
              {rules.length === 0 ? <Empty title="No rule matches" /> : (
                <DataTable head={['Pattern', 'Tool', 'Effect', 'Risk', 'Scope', 'Hits 24h', 'Why']}>
                  {rules.map((r) => (
                    <Row key={r.id} className={cn(r.effect === 'deny' && 'bg-danger/4')}>
                      <Cell mono className="text-ink">{r.pattern}</Cell>
                      <Cell><Tag tone="neutral">{r.tool}</Tag></Cell>
                      <Cell>
                        <span className={cn('inline-flex items-center gap-1.5', r.effect === 'deny' && 'font-semibold')}>
                          {r.effect === 'deny' && <Lock className="size-3 text-danger" />}
                          <Tag tone={EFFECT_TONE[r.effect]}>{r.effect}</Tag>
                        </span>
                      </Cell>
                      <Cell><RiskPill risk={r.risk} bare /></Cell>
                      <Cell className="text-dim">{r.scope}</Cell>
                      <Cell className="tnum">{r.hits24h.toLocaleString()}</Cell>
                      <Cell className="max-w-[440px] text-[11.5px] text-soft">{r.note}</Cell>
                    </Row>
                  ))}
                </DataTable>
              )}
            </Panel>
            <Panel className="border-danger/35" eyebrow="No override flag exists" title={<span className="flex items-center gap-1.5"><Lock className="size-3.5 text-danger" />Hard denials</span>} flush>
              <div className="divide-y divide-line">
                {permissionRules.filter((r) => r.effect === 'deny').map((r) => (
                  <div key={r.id} className="flex items-start gap-2.5 px-3.5 py-2">
                    <ShieldX className="mt-px size-3.5 shrink-0 text-danger" />
                    <span className="min-w-0 flex-1">
                      <Mono>{r.pattern}</Mono>
                      <span className="mt-0.5 block text-[11.5px] text-soft">{r.note}</span>
                    </span>
                    <span className="shrink-0 tnum text-[11px] text-dim">{r.hits24h} attempts</span>
                  </div>
                ))}
              </div>
            </Panel>
          </>
        )}

        {tab === 'sandbox' && (
          <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
            <Panel eyebrow="Isolation" title={<span className="flex items-center gap-1.5"><Box className="size-3.5 text-brand" />What runs where</span>}>
              <KV k="Code edits" v="inside the agent's own git worktree, never a shared checkout" />
              <KV k="Test runs" v="ephemeral container, network egress denied by default" />
              <KV k="Database reads" v="snapshot replica refreshed nightly — never the primary" />
              <KV k="Database writes" v="primary, but only through an approved migration with a proven rollback" />
              <KV k="Browser automation" v="headless container, no host filesystem mount" />
              <KV k="Terminal" v="project root only; parent directories are not reachable" />
            </Panel>
            <Panel eyebrow="Egress" title="What never leaves this machine">
              <div className="space-y-1.5">
                {[
                  'Source code — no repository content is sent to a remote model unless that model is explicitly routed for the task',
                  'Secrets — resolved at process launch, never rendered into a prompt or a log line',
                  'Customer data — the snapshot replica is masked; production rows never reach an agent',
                  'The memory store — Qdrant and Postgres are local, and there is no sync target configured',
                ].map((t) => (
                  <p key={t} className="flex items-start gap-1.5 text-[11.5px] text-ink-2">
                    <ShieldCheck className="mt-px size-3.5 shrink-0 text-ok" />{t}
                  </p>
                ))}
              </div>
              <p className="mt-3 border-t border-line pt-2.5 text-[11px] text-dim">
                71% of calls are served by local weights, so most work never crosses the network at all — a privacy
                property that happens to also be the cheap one.
              </p>
            </Panel>
          </div>
        )}
      </PageBody>
    </Page>
  );
}
