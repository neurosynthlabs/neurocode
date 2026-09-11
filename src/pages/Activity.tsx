import { useMemo, useState } from 'react';
import { Search, User, Bot, Cpu, Webhook, Wrench, Pause, Play } from 'lucide-react';
import { Switch } from '@/components/ui/switch';
import {
  Page, PageHeader, PageBody, Panel, Tag, Dot, Mono, Segmented, Stat, StatGrid, Empty, KV,
} from '@/components/os';
import { dayOf, timeOf } from '@/mock/activity-extra';
import { useData } from '@/lib/data';
import { projects, projectName } from '@/mock/projects';
import { cn } from '@/lib/utils';
import type { ActivityEvent } from '@/types';

const KIND_ICON = { human: User, agent: Bot, system: Cpu, hook: Webhook, tool: Wrench };
const KIND_TONE = { human: 'brand', agent: 'ok', system: 'violet', hook: 'warn', tool: 'info' } as const;
const KINDS = ['human', 'agent', 'system', 'hook', 'tool'] as const;

export default function Activity() {
  const { activity } = useData();
  // Pausing freezes the list where it is. New events keep arriving underneath and are counted.
  const [frozen, setFrozen] = useState<ActivityEvent[] | null>(null);
  const [proj, setProj] = useState('all');
  const [kind, setKind] = useState<'all' | ActivityEvent['actorKind']>('all');
  const [level, setLevel] = useState<'all' | ActivityEvent['level']>('all');
  const [q, setQ] = useState('');

  const all = frozen ?? activity;
  const unseen = frozen ? activity.length - frozen.length : 0;

  const list = useMemo(() => {
    const s = q.trim().toLowerCase();
    return all.filter((e) => {
      if (proj !== 'all' && e.projectId !== proj) return false;
      if (kind !== 'all' && e.actorKind !== kind) return false;
      if (level !== 'all' && e.level !== level) return false;
      if (!s) return true;
      return (e.actor + e.action + e.detail + (e.taskRef ?? '')).toLowerCase().includes(s);
    });
  }, [all, proj, kind, level, q]);

  const grouped = useMemo(() => {
    const m = new Map<string, ActivityEvent[]>();
    list.forEach((e) => {
      const d = dayOf(e.t);
      m.set(d, [...(m.get(d) ?? []), e]);
    });
    return [...m.entries()];
  }, [list]);

  const counts = useMemo(() => {
    const m = new Map<string, number>();
    all.forEach((e) => m.set(e.actorKind, (m.get(e.actorKind) ?? 0) + 1));
    return m;
  }, [all]);

  const busiest = useMemo(() => {
    const m = new Map<string, number>();
    all.filter((e) => e.actorKind === 'agent').forEach((e) => m.set(e.actor, (m.get(e.actor) ?? 0) + 1));
    return [...m.entries()].sort((a, b) => b[1] - a[1]).slice(0, 6);
  }, [all]);

  const mine = useMemo(() => all.filter((e) => e.actorKind === 'human'), [all]);

  return (
    <Page>
      <PageHeader
        title="Activity"
        subtitle="Every decision, tool call, hook firing and approval — in order. This is the audit trail and the reason the system feels alive."
        actions={
          <label className="flex items-center gap-2 text-[12px] text-soft">
            <Switch checked={!frozen} onCheckedChange={(on) => setFrozen(on ? null : activity)} />
            {frozen
              ? <><Pause className="size-3" />paused{unseen > 0 && <Tag tone="brand">{unseen} new</Tag>}</>
              : <><Play className="size-3" />live</>}
          </label>
        }
      >
        <div className="flex flex-wrap items-center gap-2 pb-3">
          <div className="flex h-7 w-64 items-center gap-2 rounded-sm border border-line bg-surface-2 px-2.5 focus-within:border-brand">
            <Search className="size-3.5 shrink-0 text-dim" />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search the log…"
              className="min-w-0 flex-1 bg-transparent text-[12px] text-ink placeholder:text-dim focus-visible:outline-none" />
          </div>
          <select value={proj} onChange={(e) => setProj(e.target.value)} className="h-7 rounded-sm border border-line-strong bg-surface-2 px-2 text-[12px] text-ink-2">
            <option value="all" className="bg-surface">All projects</option>
            {projects.map((p) => <option key={p.id} value={p.id} className="bg-surface">{p.name}</option>)}
          </select>
          <Segmented
            options={[{ id: 'all', label: 'all' }, ...KINDS.map((k) => ({ id: k, label: k }))]}
            value={kind}
            onChange={(v) => setKind(v as typeof kind)}
          />
          <Segmented
            options={[{ id: 'all', label: 'any' }, { id: 'ok', label: 'ok' }, { id: 'info', label: 'info' }, { id: 'warn', label: 'warn' }, { id: 'err', label: 'err' }]}
            value={level}
            onChange={(v) => setLevel(v as typeof level)}
          />
          <span className="ml-auto text-[11.5px] text-dim">{list.length} of {all.length} events</span>
        </div>
      </PageHeader>

      <PageBody>
        <div className="grid grid-cols-12 gap-4">
          {/* Timeline */}
          <div className="col-span-12 xl:col-span-8">
            <Panel flush>
              {list.length === 0 ? <Empty title="Nothing matches" hint="Widen the filters or clear the search." /> : (
                <div>
                  {grouped.map(([day, events]) => (
                    <div key={day}>
                      <div className="sticky top-0 z-10 flex items-center justify-between border-y border-line bg-surface-2 px-3.5 py-1.5">
                        <span className="eyebrow">{day}</span>
                        <span className="tnum text-[11px] text-dim">{events.length} events</span>
                      </div>
                      <div className="divide-y divide-line/60">
                        {events.map((e) => {
                          const Icon = KIND_ICON[e.actorKind];
                          return (
                            <div key={e.id} className={cn('flex items-start gap-3 px-3.5 py-2', e.level === 'err' && 'bg-danger/5', /^(live|local)-/.test(e.id) && 'animate-slide-up bg-brand/5')}>
                              <span className="tnum mt-px w-14 shrink-0 font-mono text-[10.5px] text-dim">{timeOf(e.t)}</span>
                              <span className={cn('mt-px shrink-0',
                                e.actorKind === 'human' ? 'text-brand' : e.actorKind === 'hook' ? 'text-warn' : e.actorKind === 'tool' ? 'text-info' : e.actorKind === 'system' ? 'text-violet' : 'text-ok')}>
                                <Icon className="size-3.5" />
                              </span>
                              <span className="w-36 shrink-0 truncate text-[11.5px] text-soft">{e.actor}</span>
                              <span className="min-w-0 flex-1">
                                <span className="flex flex-wrap items-center gap-1.5">
                                  <Dot state={e.level === 'err' ? 'error' : e.level} />
                                  <span className="text-[12px] font-medium text-ink">{e.action}</span>
                                  {e.taskRef && <Mono>{e.taskRef}</Mono>}
                                </span>
                                <span className="block text-[11.5px] text-dim">{e.detail}</span>
                              </span>
                              <span className="eyebrow shrink-0">{projectName(e.projectId)}</span>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </Panel>
          </div>

          {/* Rail */}
          <div className="col-span-12 space-y-4 xl:col-span-4">
            <StatGrid cols={2}>
              <Stat label="Events today" value={all.filter((e) => dayOf(e.t) === 'Today').length} sub="across every project" />
              <Stat label="Your actions" value={mine.length} tone="brand" sub="the scarce resource" />
            </StatGrid>

            <Panel eyebrow="Who did the work" title="By actor kind" flush>
              <div className="divide-y divide-line">
                {KINDS.map((k) => {
                  const Icon = KIND_ICON[k];
                  const n = counts.get(k) ?? 0;
                  return (
                    <button key={k} onClick={() => setKind(kind === k ? 'all' : k)}
                      className={cn('flex w-full items-center gap-2.5 px-3.5 py-2 text-left hover:bg-surface-2', kind === k && 'bg-surface-2')}>
                      <Icon className="size-3.5 text-dim" />
                      <span className="flex-1 text-[12px] text-ink-2 capitalize">{k}</span>
                      <Tag tone={KIND_TONE[k]}>{n}</Tag>
                    </button>
                  );
                })}
              </div>
            </Panel>

            <Panel eyebrow="Busiest agents" title="Event count" flush>
              <div className="divide-y divide-line">
                {busiest.map(([name, n]) => (
                  <div key={name} className="flex items-center gap-2.5 px-3.5 py-1.5">
                    <Bot className="size-3 text-ok" />
                    <span className="flex-1 truncate text-[12px] text-ink-2">{name}</span>
                    <span className="tnum text-[11.5px] text-soft">{n}</span>
                  </div>
                ))}
              </div>
            </Panel>

            <Panel eyebrow="Only you can do these" title="Human actions" flush>
              <div className="divide-y divide-line">
                {mine.slice(0, 8).map((e) => (
                  <div key={e.id} className="px-3.5 py-2">
                    <div className="flex items-center gap-2">
                      <span className="tnum font-mono text-[10.5px] text-dim">{timeOf(e.t)}</span>
                      <span className="text-[12px] font-medium text-ink">{e.action}</span>
                    </div>
                    <p className="mt-0.5 truncate text-[11px] text-dim">{e.detail}</p>
                  </div>
                ))}
              </div>
            </Panel>

            <Panel eyebrow="Hooks" title="Deterministic automation" flush>
              <div className="px-3.5 py-2">
                <KV k="Fired today" v={all.filter((e) => e.actorKind === 'hook').length} />
                <KV k="Blocked a tool call" v={<span className="text-warn">3</span>} />
                <KV k="Median overhead" v="41 ms" />
              </div>
            </Panel>
          </div>
        </div>
      </PageBody>
    </Page>
  );
}
