import { useMemo, useState } from 'react';
import { Search, Sparkles, Zap, Gauge } from 'lucide-react';
import { toast } from 'sonner';
import { Switch } from '@/components/ui/switch';
import {
  Page, PageHeader, PageBody, Panel, Tag, Mono, ListRow, Toolbar, Field, SelectField,
  Stat, StatGrid, KV, Empty, SectionTitle, DataTable, Row, Cell,
} from '@/components/os';
import { skills, skillScopes, sessionsToday } from '@/mock/skills';
import { agentName } from '@/mock/agents';
import { projectName } from '@/mock/projects';
import { cn } from '@/lib/utils';

export default function Skills() {
  const [q, setQ] = useState('');
  const [scope, setScope] = useState('all');
  const [only, setOnly] = useState('all');
  const [sel, setSel] = useState(skills[0].id);
  const [on, setOn] = useState<Record<string, boolean>>(() => Object.fromEntries(skills.map((s) => [s.id, s.enabled])));

  const list = useMemo(() => {
    const t = q.trim().toLowerCase();
    return skills.filter((s) =>
      (scope === 'all' || s.scope === scope) &&
      (only === 'all' || (only === 'on' ? on[s.id] : !on[s.id])) &&
      (!t || (s.name + s.slug + s.description + s.triggers.join(' ')).toLowerCase().includes(t)));
  }, [q, scope, only, on]);

  const s = useMemo(() => list.find((x) => x.id === sel) ?? list[0] ?? skills[0], [list, sel]);
  const enabledCount = skills.filter((x) => on[x.id]).length;
  const loadedTokens = skills.filter((x) => on[x.id]).reduce((n, x) => n + x.tokens, 0);
  const firedTokens = skills.filter((x) => on[x.id] && x.loads24h > 0).reduce((n, x) => n + x.tokens, 0);

  return (
    <Page>
      <PageHeader
        title="Skills"
        subtitle="Procedures an agent loads only when a trigger matches — so the context window carries what the task needs and nothing else."
        actions={<Tag tone="ok"><Sparkles className="size-3" />{enabledCount} of {skills.length} enabled</Tag>}
      >
        <Toolbar>
          <Field className="w-64" value={q} onChange={setQ} icon={<Search className="size-3.5" />} placeholder="Search skills, slugs, triggers…" onClear={() => setQ('')} />
          <SelectField className="w-36" value={scope} onChange={setScope}
            options={skillScopes.map((v) => ({ value: v, label: v === 'all' ? 'Any scope' : v }))} />
          <SelectField className="w-32" value={only} onChange={setOnly}
            options={[{ value: 'all', label: 'All' }, { value: 'on', label: 'Enabled' }, { value: 'off', label: 'Disabled' }]} />
          <span className="ml-auto text-[11.5px] text-dim">{list.length} of {skills.length}</span>
        </Toolbar>
      </PageHeader>

      <PageBody className="space-y-4">
        <StatGrid cols={5}>
          <Stat label="Skills" value={skills.length} sub={`${enabledCount} enabled`} icon={<Sparkles className="size-3" />} />
          <Stat label="Loads 24h" value={skills.reduce((n, x) => n + x.loads24h, 0).toLocaleString()} sub={`across ${sessionsToday} sessions`} icon={<Zap className="size-3" />} />
          <Stat label="If always loaded" value={`${(loadedTokens / 1000).toFixed(1)}k`} tone="danger" sub="tokens of standing context" />
          <Stat label="Actually loaded" value={`${(firedTokens / 1000).toFixed(1)}k`} tone="ok" sub="only what triggered" />
          <Stat label="Context saved" value={`${Math.round((1 - firedTokens / Math.max(loadedTokens, 1)) * 100)}%`} tone="ok" sub="per average turn" icon={<Gauge className="size-3" />} />
        </StatGrid>

        <Panel className="accent-left" eyebrow="Why this exists" title="A skill is not a prompt — it is a trigger plus a procedure">
          <p className="max-w-4xl text-[12.5px] leading-relaxed text-ink-2">
            Loading every rule into every agent burns the context window on instructions that will never apply to the task
            at hand. A skill declares the conditions under which it becomes relevant; the orchestrator matches those
            deterministically and injects only the matching bodies. Twenty-two skills exist here — a typical turn loads two.
          </p>
        </Panel>

        <div className="flex min-h-[560px] gap-3">
          <div className="no-scrollbar w-[320px] shrink-0 overflow-y-auto rounded-md border border-line bg-surface">
            {list.length === 0 ? <Empty title="No skill matches" /> : list.map((x) => (
              <ListRow key={x.id} active={x.id === s.id} onClick={() => setSel(x.id)}>
                <div className="flex items-center gap-2">
                  <span className={cn('size-1.5 shrink-0 rounded-full', on[x.id] ? 'bg-ok' : 'bg-dim')} />
                  <span className="truncate text-[12.5px] font-medium text-ink">{x.name}</span>
                  <span className="ml-auto shrink-0 tnum text-[10.5px] text-dim">{x.loads24h}</span>
                </div>
                <Mono className="mt-1 block truncate">{x.slug}</Mono>
                <p className="mt-1 line-clamp-1 text-[10.5px] text-dim">{x.description}</p>
              </ListRow>
            ))}
          </div>

          <div className="min-w-0 flex-1 space-y-3">
            <Panel
              eyebrow={`${s.scope} · ${s.project === 'all' ? 'every project' : projectName(s.project)} · v${s.version}`}
              title={<span className="flex items-center gap-2">{s.name}<Mono tone="brand">{s.slug}</Mono></span>}
              actions={<Switch checked={on[s.id]} onCheckedChange={(v) => { setOn((m) => ({ ...m, [s.id]: v })); toast(`${s.name} ${v ? 'enabled' : 'disabled'}`); }} />}
            >
              <p className="text-[12.5px] leading-relaxed text-ink-2">{s.description}</p>
              <div className="mt-3 grid grid-cols-2 gap-x-6 border-t border-line pt-2.5 md:grid-cols-4">
                <KV k="Context cost" v={`${s.tokens.toLocaleString()} tokens`} />
                <KV k="Loads 24h" v={s.loads24h} />
                <KV k="Invocations" v={s.invocations.toLocaleString()} />
                <KV k="Last used" v={s.lastUsed} />
                <KV k="Author" v={s.author} />
                <KV k="Source" v={s.source} mono />
              </div>
            </Panel>

            <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
              <Panel eyebrow="Deterministic — no model decides this" title="Trigger rules" flush>
                <div className="divide-y divide-line">
                  {s.rules.map((r, i) => (
                    <div key={i} className="px-3.5 py-2">
                      <div className="flex items-start gap-2">
                        <span className="eyebrow mt-0.5 w-9 shrink-0">when</span>
                        <span className="text-[11.5px] text-ink-2">{r.when}</span>
                      </div>
                      <div className="mt-1 flex items-start gap-2">
                        <span className="eyebrow mt-0.5 w-9 shrink-0 text-brand">then</span>
                        <span className="text-[11.5px] text-soft">{r.then}</span>
                      </div>
                    </div>
                  ))}
                </div>
              </Panel>

              <div className="space-y-3">
                <Panel eyebrow={`${s.triggers.length} phrases`} title="Trigger surface">
                  <div className="flex flex-wrap gap-1">{s.triggers.map((t) => <Mono key={t}>{t}</Mono>)}</div>
                </Panel>
                <Panel eyebrow={`${s.tools.length} required`} title="Tools it needs">
                  <div className="flex flex-wrap gap-1">{s.tools.map((t) => <Tag key={t} tone="ok">{t}</Tag>)}</div>
                </Panel>
                <Panel eyebrow={`${s.usedBy.length} agents`} title="Loaded by">
                  <div className="flex flex-wrap gap-1">{s.usedBy.map((a) => <Tag key={a} tone="neutral">{agentName(a)}</Tag>)}</div>
                </Panel>
              </div>
            </div>

            <Panel eyebrow="What the agent actually reads" title="Instruction body">
              <pre className="ascii max-h-[320px] overflow-auto rounded-sm border border-line bg-base p-3.5 whitespace-pre-wrap">{s.body}</pre>
            </Panel>
          </div>
        </div>

        <SectionTitle>Cost of always-on vs trigger-loaded</SectionTitle>
        <Panel flush>
          <DataTable head={['Skill', 'Scope', 'Tokens', 'Loads 24h', 'Cost if always on', 'Cost as loaded']}>
            {[...skills].sort((a, b) => b.tokens - a.tokens).slice(0, 10).map((x) => (
              <Row key={x.id}>
                <Cell className="font-medium text-ink">{x.name}</Cell>
                <Cell><Tag tone="neutral">{x.scope}</Tag></Cell>
                <Cell className="tnum">{x.tokens.toLocaleString()}</Cell>
                <Cell className="tnum">{x.loads24h}</Cell>
                <Cell className="tnum text-danger">{(x.tokens * sessionsToday / 1000).toFixed(0)}k</Cell>
                <Cell className="tnum text-ok">{(x.tokens * x.loads24h / 1000).toFixed(1)}k</Cell>
              </Row>
            ))}
          </DataTable>
        </Panel>
      </PageBody>
    </Page>
  );
}
