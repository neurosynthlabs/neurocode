import { useMemo, useState } from 'react';
import { Search, Sparkles, Zap, Gauge, Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import { useData, usePref } from '@/lib/data';
import { useAuth } from '@/lib/auth';
import { useProject } from '@/lib/project-context';
import { useRemote } from '@/lib/remote';
import { extensions, type LiveSkill } from '@/lib/live/extensions';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/switch';
import {
  Page, PageHeader, PageBody, Panel, Tag, Mono, ListRow, Toolbar, Field, SelectField,
  Stat, StatGrid, KV, Empty, SectionTitle, DataTable, Row, Cell,
} from '@/components/os';
import { skills, skillScopes, sessionsToday } from '@/mock/skills';
import { agentName } from '@/mock/agents';
import { projectName } from '@/mock/projects';
import { cn } from '@/lib/utils';
import { ago, tokens as fmtTokens } from './code/format';

const SKILLS_ON: Record<string, boolean> = Object.fromEntries(skills.map((s) => [s.id, s.enabled]));

/** With the local API, the skills really on this machine; in the demo, the worked example. */
export default function Skills() {
  const { mode } = useData();
  return mode === 'live' ? <LiveSkills /> : <SampleSkills />;
}

function SampleSkills() {
  const [q, setQ] = useState('');
  const [scope, setScope] = useState('all');
  const [only, setOnly] = useState('all');
  const [sel, setSel] = useState(skills[0].id);
  const [on, setOn] = usePref('skills.enabled', SKILLS_ON);

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
          <span className="ml-auto text-[12.5px] text-dim">{list.length} of {skills.length}</span>
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
          <p className="max-w-4xl text-[13.5px] leading-relaxed text-ink-2">
            Loading every rule into every agent burns the context window on instructions that will never apply to the task
            at hand. A skill declares the conditions under which it becomes relevant; the orchestrator matches those
            deterministically and injects only the matching bodies. Twenty-two skills exist here — a typical turn loads two.
          </p>
        </Panel>

        <div className="flex min-h-[560px] flex-col gap-3 md:flex-row">
          <div className="no-scrollbar w-full shrink-0 max-h-[42vh] md:max-h-none md:w-[320px] overflow-y-auto rounded-md border border-line bg-surface">
            {list.length === 0 ? <Empty title="No skill matches" /> : list.map((x) => (
              <ListRow key={x.id} active={x.id === s.id} onClick={() => setSel(x.id)}>
                <div className="flex items-center gap-2">
                  <span className={cn('size-1.5 shrink-0 rounded-full', on[x.id] ? 'bg-ok' : 'bg-dim')} />
                  <span className="truncate text-[13.5px] font-medium text-ink">{x.name}</span>
                  <span className="ml-auto shrink-0 tnum text-[11.5px] text-dim">{x.loads24h}</span>
                </div>
                <Mono className="mt-1 block truncate">{x.slug}</Mono>
                <p className="mt-1 line-clamp-1 text-[11.5px] text-dim">{x.description}</p>
              </ListRow>
            ))}
          </div>

          <div className="min-w-0 flex-1 space-y-3">
            <Panel
              eyebrow={`${s.scope} · ${s.project === 'all' ? 'every project' : projectName(s.project)} · v${s.version}`}
              title={<span className="flex items-center gap-2">{s.name}<Mono tone="brand">{s.slug}</Mono></span>}
              actions={<Switch checked={on[s.id]} onCheckedChange={(v) => { setOn({ ...on, [s.id]: v }, `Skill ${s.name} ${v ? 'enabled' : 'disabled'}`); toast(`${s.name} ${v ? 'enabled' : 'disabled'}`); }} />}
            >
              <p className="text-[13.5px] leading-relaxed text-ink-2">{s.description}</p>
              <div className="mt-3 grid grid-cols-1 sm:grid-cols-2 gap-x-6 border-t border-line pt-2.5 xl:grid-cols-4">
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
                        <span className="text-[12.5px] text-ink-2">{r.when}</span>
                      </div>
                      <div className="mt-1 flex items-start gap-2">
                        <span className="eyebrow mt-0.5 w-9 shrink-0 text-brand">then</span>
                        <span className="text-[12.5px] text-soft">{r.then}</span>
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

/* Live: every skill a NeuroCode session on this project can see — the workspace's (~/.claude/skills), the
   project's (.claude/skills) and those of plugins enabled in Claude Code — read off disk on each load. */

/** Found skills are on until someone switches one off, so the map starts empty and a missing key means on. */
const NONE_OFF: Record<string, boolean> = {};

function LiveSkills() {
  const { can } = useAuth();
  const { project } = useProject();
  const [q, setQ] = useState('');
  const [scope, setScope] = useState('all');
  const [only, setOnly] = useState('all');
  const [picked, setPicked] = useState<string | null>(null);
  const [on, setOn] = usePref('skills.enabled', NONE_OFF);
  const r = useRemote(`skills:${project.id}`, () => extensions.skills(project.id));
  const isOn = (id: string) => on[id] !== false;

  const all = useMemo(() => r.data?.skills ?? [], [r.data]);
  const list = useMemo(() => {
    const t = q.trim().toLowerCase();
    return all.filter((s) =>
      (scope === 'all' || s.scope === scope) &&
      (only === 'all' || (only === 'on' ? on[s.id] !== false : on[s.id] === false)) &&
      (!t || (s.name + s.slug + s.description + s.triggers.join(' ')).toLowerCase().includes(t)));
  }, [all, q, scope, only, on]);
  const s = list.find((x) => x.id === picked) ?? list[0] ?? null;

  const sessions = r.data?.sessions24h ?? 0;
  const enabled = all.filter((x) => isOn(x.id));
  const standing = enabled.reduce((n, x) => n + x.tokens, 0);
  const loads = all.reduce((n, x) => n + x.loads24h, 0);
  const loaded = all.reduce((n, x) => n + x.tokens * x.loads24h, 0);
  const ifAlwaysOn = standing * sessions;

  return (
    <Page>
      <PageHeader
        title="Skills"
        subtitle={`Read from SKILL.md files on this machine: the workspace's, ${project.name}'s and those of plugins enabled in Claude Code. A NeuroCode session sees one line per enabled skill and reads the body only when it asks for it.`}
        actions={r.data && <Tag tone="ok"><Sparkles className="size-3" />{enabled.length} of {all.length} enabled</Tag>}
      >
        <Toolbar>
          <Field className="w-64" value={q} onChange={setQ} icon={<Search className="size-3.5" />} placeholder="Search skills, slugs, triggers…" onClear={() => setQ('')} />
          <SelectField className="w-36" value={scope} onChange={setScope}
            options={['all', 'global', 'project', 'plugin'].map((v) => ({ value: v, label: v === 'all' ? 'Any scope' : v }))} />
          <SelectField className="w-32" value={only} onChange={setOnly}
            options={[{ value: 'all', label: 'All' }, { value: 'on', label: 'Enabled' }, { value: 'off', label: 'Disabled' }]} />
          <span className="ml-auto text-[12.5px] text-dim">{list.length} of {all.length}</span>
        </Toolbar>
      </PageHeader>

      {r.error ? (
        <PageBody><Empty title="The skills did not load" hint={r.error} action={<Button size="sm" variant="outline" onClick={r.reload}>Try again</Button>} /></PageBody>
      ) : !r.data ? (
        <PageBody><Empty icon={<Loader2 className="size-5 animate-spin" />} title="Reading skills off disk…" /></PageBody>
      ) : all.length === 0 ? (
        <PageBody>
          <Empty icon={<Sparkles className="size-6" />} title="No skills on this machine"
            hint={`Nothing was found in ${r.data.roots.map((x) => `${x.path}${x.exists ? '' : ' (missing)'}`).join(', ')}. A skill is a folder with a SKILL.md in one of them.`} />
        </PageBody>
      ) : (
        <PageBody className="space-y-4">
          <StatGrid cols={5}>
            <Stat label="Skills" value={all.length} sub={`${enabled.length} enabled`} icon={<Sparkles className="size-3" />} />
            <Stat label="Loads 24h" value={loads.toLocaleString()} sub={`across ${sessions} session${sessions === 1 ? '' : 's'} on ${project.name}`} icon={<Zap className="size-3" />} />
            <Stat label="If always loaded" value={fmtTokens(ifAlwaysOn)} tone="danger" sub={sessions ? `${fmtTokens(standing)} tokens × ${sessions} sessions` : `${fmtTokens(standing)} tokens a session`} />
            <Stat label="Actually loaded" value={fmtTokens(loaded)} tone="ok" sub="tokens sessions read in 24h" />
            <Stat label="Context saved" value={sessions && loads && ifAlwaysOn ? `${Math.round((1 - loaded / ifAlwaysOn) * 100)}%` : '—'} tone={loads ? 'ok' : undefined}
              sub={!sessions ? 'no session in 24h to compare' : !loads ? 'no skill loaded in 24h' : 'estimated tokens (characters ÷ 4)'} icon={<Gauge className="size-3" />} />
          </StatGrid>

          <Panel className="accent-left" eyebrow="How sessions use them" title="A description in the prompt, the body only on request">
            <p className="max-w-4xl text-[13.5px] leading-relaxed text-ink-2">
              {all.length} skills are here. {sessions === 0 || loads === 0
                ? `No NeuroCode session on ${project.name} has loaded one in the last 24 hours.`
                : `${sessions} session${sessions === 1 ? '' : 's'} on ${project.name} loaded ${(loads / sessions).toFixed(1)} on average in the last 24 hours.`}
              {' '}The model reads each enabled skill's description and decides for itself when to load one; nothing here runs a skill.
              Switching one off leaves it out of NeuroCode sessions — Claude Code keeps its own.
            </p>
            {r.data.unreadable.length > 0 && (
              <p className="mt-2 text-[12.5px] text-warn">Front matter could not be read in {r.data.unreadable.join(', ')}.</p>
            )}
          </Panel>

          <div className="flex min-h-[560px] flex-col gap-3 md:flex-row">
            <div className="no-scrollbar w-full shrink-0 max-h-[42vh] md:max-h-none md:w-[320px] overflow-y-auto rounded-md border border-line bg-surface">
              {list.length === 0 ? <Empty title="No skill matches" /> : list.map((x) => (
                <ListRow key={x.id} active={x.id === s?.id} onClick={() => setPicked(x.id)}>
                  <div className="flex items-center gap-2">
                    <span className={cn('size-1.5 shrink-0 rounded-full', isOn(x.id) ? 'bg-ok' : 'bg-dim')} />
                    <span className="truncate text-[13.5px] font-medium text-ink">{x.name}</span>
                    <span className="ml-auto shrink-0 tnum text-[11.5px] text-dim">{x.loads24h}</span>
                  </div>
                  <Mono className="mt-1 block truncate">{x.slug}</Mono>
                  <p className="mt-1 line-clamp-1 text-[11.5px] text-dim">{x.description}</p>
                </ListRow>
              ))}
            </div>

            {s ? (
              <LiveSkillDetail key={s.id} skill={s} projectId={project.id} projectName={project.name} sessions={sessions}
                on={isOn(s.id)} canSwitch={can('settings:write')}
                onSwitch={(v) => { setOn({ ...on, [s.id]: v }, `Skill ${s.name} ${v ? 'enabled' : 'disabled'} in NeuroCode sessions`); toast(`${s.name} ${v ? 'enabled' : 'disabled'}`, { description: 'In NeuroCode sessions. Claude Code is not affected.' }); }} />
            ) : <div className="min-w-0 flex-1"><Empty title="Nothing selected" hint="Widen the filters to pick a skill." /></div>}
          </div>

          <SectionTitle>Cost of always-on vs loaded on request</SectionTitle>
          <Panel flush>
            <DataTable head={['Skill', 'Scope', 'Tokens', 'Loads 24h', 'Cost if always on', 'Cost as loaded']}>
              {[...all].sort((a, b) => b.tokens - a.tokens).slice(0, 10).map((x) => (
                <Row key={x.id}>
                  <Cell className="font-medium text-ink">{x.name}</Cell>
                  <Cell><Tag tone="neutral">{x.scope}</Tag></Cell>
                  <Cell className="tnum">{x.tokens.toLocaleString()}</Cell>
                  <Cell className="tnum">{x.loads24h}</Cell>
                  <Cell className="tnum text-danger">{fmtTokens(x.tokens * sessions)}</Cell>
                  <Cell className="tnum text-ok">{fmtTokens(x.tokens * x.loads24h)}</Cell>
                </Row>
              ))}
            </DataTable>
          </Panel>
        </PageBody>
      )}
    </Page>
  );
}

function LiveSkillDetail({ skill: s, projectId, projectName: name, sessions, on, canSwitch, onSwitch }: {
  skill: LiveSkill; projectId: string; projectName: string; sessions: number; on: boolean; canSwitch: boolean;
  onSwitch: (on: boolean) => void;
}) {
  const d = useRemote(`skill:${projectId}:${s.id}:${sessions}`, () => extensions.skill(projectId, s.id));
  const version = d.data?.version || s.version;
  const author = d.data?.author || s.author;
  return (
    <div className="min-w-0 flex-1 space-y-3">
      <Panel
        eyebrow={`${s.scope} · ${s.project === 'all' ? 'every project' : name}${version ? ` · ${/^\d/.test(version) ? 'v' : ''}${version}` : ''}`}
        title={<span className="flex items-center gap-2">{s.name}<Mono tone="brand">{s.slug}</Mono></span>}
        actions={<Switch checked={on} disabled={!canSwitch} aria-label={`Use ${s.name} in NeuroCode sessions`} onCheckedChange={onSwitch} />}
      >
        <p className="text-[13.5px] leading-relaxed text-ink-2">{s.description || <span className="text-dim">No description in its front matter.</span>}</p>
        <div className="mt-3 grid grid-cols-1 sm:grid-cols-2 gap-x-6 border-t border-line pt-2.5 xl:grid-cols-4">
          <KV k="Context cost" v={`${s.tokens.toLocaleString()} tokens (est.)`} />
          <KV k="Loads 24h" v={s.loads24h} />
          <KV k="Loads, all time" v={s.invocations.toLocaleString()} />
          <KV k="Last used" v={s.lastUsed ? ago(s.lastUsed) : 'never here'} />
          <KV k="Author" v={author || <span className="text-dim">not stated</span>} />
          <KV k="Source" v={s.source} mono />
        </div>
      </Panel>

      <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
        <Panel eyebrow="What decides when it loads" title="Trigger rules">
          <p className="text-[13px] text-soft">This skill declares no rules — it is offered to the model by its description, and the model decides.</p>
        </Panel>

        <div className="space-y-3">
          <Panel eyebrow={`${s.triggers.length} phrases`} title="Trigger surface">
            {s.triggers.length
              ? <div className="flex flex-wrap gap-1">{s.triggers.map((t) => <Mono key={t}>{t}</Mono>)}</div>
              : <p className="text-[13px] text-dim">None declared in its front matter.</p>}
          </Panel>
          <Panel eyebrow={`${s.tools.length} allowed`} title="Tools it needs">
            {s.tools.length
              ? <div className="flex flex-wrap gap-1">{s.tools.map((t) => <Tag key={t} tone="ok">{t}</Tag>)}</div>
              : <p className="text-[13px] text-dim">It names no allowed tools.</p>}
          </Panel>
          <Panel eyebrow="Loaded in sessions by" title="Loaded by">
            {s.usedBy.length
              ? <div className="flex flex-wrap gap-1">{s.usedBy.map((a) => <Tag key={a} tone="neutral">{a}</Tag>)}</div>
              : <p className="text-[13px] text-dim">No NeuroCode session has loaded this skill yet.</p>}
          </Panel>
        </div>
      </div>

      <Panel eyebrow={d.data?.truncated ? 'What a session reads — cut off here at 64 KB' : 'What a session reads'} title="Instruction body">
        {d.error ? <p className="text-[13px] text-danger">{d.error}</p>
          : !d.data ? <p className="flex items-center gap-2 text-[13px] text-dim"><Loader2 className="size-3.5 animate-spin" />Reading the file…</p>
            : <pre className="ascii max-h-[320px] overflow-auto rounded-sm border border-line bg-base p-3.5 whitespace-pre-wrap">{d.data.body}</pre>}
      </Panel>
    </div>
  );
}
