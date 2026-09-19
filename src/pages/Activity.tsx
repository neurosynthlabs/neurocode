import { useEffect, useMemo, useState } from 'react';
import { Search, User, Bot, Cpu, Webhook, Pause, Play } from 'lucide-react';
import { Switch } from '@/components/ui/switch';
import {
  Page, PageHeader, PageBody, Panel, Tag, Dot, Mono, Segmented, Stat, StatGrid, Empty, KV,
} from '@/components/os';
import { request } from '@/lib/api';
import { useData } from '@/lib/data';
import { useRemote } from '@/lib/remote';
import { useAuth } from '@/lib/auth';
import { useProject } from '@/lib/project-context';
import { extensions, type LiveHooks } from '@/lib/live/extensions';
import { clock, dayKey, dayLabel, projectLabel } from '@/lib/live/work';
import { cn } from '@/lib/utils';
import type { ActivityEvent } from '@/types';

/* The kinds of actor the server records. Hooks and tools are never written to this log — Claude Code
   keeps no readable record of a hook firing — so they are not offered as filters that would always be empty. */
const KIND_ICON = { human: User, agent: Bot, system: Cpu };
const KIND_TONE = { human: 'brand', agent: 'ok', system: 'violet' } as const;
const KINDS = ['human', 'agent', 'system'] as const;
const newest = (events: ActivityEvent[]) => events.reduce((n, e) => Math.max(n, Number(e.id) || 0), 0);
/** The feed holds the newest rows only (the server's ceiling), so no figure on this page is counted from it. */
const FEED = 500;

/** GET /activity/summary: the figures over the whole log, counted by the server. `through` is the newest
    event they include; anything the stream brought after it is added here, and nothing twice. */
interface ActivitySummary {
  total: number;
  today: number;
  mine: number;
  through: number;
  dayStart: string;
  byKind: Record<string, number>;
  agents: { name: string; events: number }[];
}

export default function Activity() {
  const { activity, projects } = useData();
  const { user } = useAuth();
  const { projectId, project } = useProject();
  // What was already in the log when the page opened. Anything after it arrived on the stream, and is marked.
  const [seen] = useState(() => newest(activity));
  // Kept with the project it was read for, so another project's name never sits over these counts.
  const [hooks, setHooks] = useState<{ pid: string; data: LiveHooks } | null>(null);
  const summary = useRemote('activity:summary', () => request<ActivitySummary>('/activity/summary'));
  // Pausing freezes the list where it is. New events keep arriving underneath and are counted.
  const [frozen, setFrozen] = useState<ActivityEvent[] | null>(null);
  const [proj, setProj] = useState('all');
  const [kind, setKind] = useState<'all' | ActivityEvent['actorKind']>('all');
  const [level, setLevel] = useState<'all' | ActivityEvent['level']>('all');
  const [q, setQ] = useState('');

  const all = frozen ?? activity;
  const unseen = frozen ? activity.length - frozen.length : 0;

  useEffect(() => {
    if (!projectId) return;
    let live = true;
    extensions.hooks(projectId).then(
      (found) => { if (live) setHooks({ pid: projectId, data: found }); },
      (e: unknown) => { console.error('[NeuroCode] GET /extensions/hooks failed:', e); if (live) setHooks(null); },
    );
    return () => { live = false; };
  }, [projectId]);

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
      const d = dayKey(e.at);
      m.set(d, [...(m.get(d) ?? []), e]);
    });
    return [...m.values()].map((events) => [dayLabel(events[0].at), events] as const);
  }, [list]);

  // Every signed-in person is recorded as a human actor, so "yours" is the ones under your name.
  const mine = useMemo(() => all.filter((e) => e.actorKind === 'human' && e.actor === user?.name), [all, user]);

  // The server's figures, plus whatever the stream brought after the newest event they counted.
  const figures = useMemo(() => {
    const s = summary.data;
    if (!s) return null;
    const after = all.filter((e) => Number(e.id) > s.through);
    const kinds = new Map(Object.entries(s.byKind));
    const agents = new Map(s.agents.map((a) => [a.name, a.events]));
    after.forEach((e) => {
      kinds.set(e.actorKind, (kinds.get(e.actorKind) ?? 0) + 1);
      if (e.actorKind === 'agent') agents.set(e.actor, (agents.get(e.actor) ?? 0) + 1);
    });
    const start = Date.parse(s.dayStart);
    return {
      total: s.total + after.length,
      kinds,
      busiest: [...agents.entries()].sort((a, b) => b[1] - a[1]).slice(0, 6),
      today: s.today + after.filter((e) => Date.parse(e.at) >= start).length,
      mine: s.mine + after.filter((e) => e.actorKind === 'human' && e.actor === user?.name).length,
    };
  }, [summary.data, all, user]);
  const waiting = summary.error ?? 'counting…';
  const cut = activity.length >= FEED;

  return (
    <Page>
      <PageHeader
        title="Activity"
        subtitle="Every decision, run and approval — in order. This is the audit trail and the reason the system feels alive."
        actions={
          <label className="flex items-center gap-2 text-[13px] text-soft">
            <Switch checked={!frozen} onCheckedChange={(on) => setFrozen(on ? null : activity)} />
            {frozen
              ? <><Pause className="size-3" />paused{unseen > 0 && <Tag tone="brand">{unseen} new</Tag>}</>
              : <><Play className="size-3" />live</>}
          </label>
        }
      >
        <div className="flex flex-wrap items-center gap-2 pb-3">
          <div className="flex h-9 w-64 items-center gap-2 rounded-lg border border-line bg-surface-2 px-2.5 focus-within:border-brand">
            <Search className="size-3.5 shrink-0 text-dim" />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search the log…"
              className="min-w-0 flex-1 bg-transparent text-[13px] text-ink placeholder:text-dim focus-visible:outline-none" />
          </div>
          <select value={proj} onChange={(e) => setProj(e.target.value)} className="h-9 rounded-lg border border-line-strong bg-surface-2 px-2.5 text-[13px] text-ink-2">
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
          <span className="ml-auto text-[12.5px] text-dim">
            {list.length} of {cut ? `the latest ${all.length}` : all.length} events{cut && figures ? ` · ${figures.total.toLocaleString()} in the whole log` : ''}
          </span>
        </div>
      </PageHeader>

      <PageBody>
        <div className="grid grid-cols-12 gap-4">
          {/* Timeline */}
          <div className="col-span-12 xl:col-span-8">
            <Panel flush>
              {all.length === 0 ? (
                <Empty title="The log is empty" hint="Every change anyone makes — a task moved, a plan dispatched, an approval decided — is recorded here." />
              ) : list.length === 0 ? <Empty title="Nothing matches" hint="Widen the filters or clear the search." /> : (
                <div>
                  {grouped.map(([day, events]) => (
                    <div key={day}>
                      <div className="sticky top-0 z-10 flex items-center justify-between border-y border-line bg-surface-2 px-3.5 py-1.5">
                        <span className="eyebrow">{day}</span>
                        <span className="tnum text-[12px] text-dim">{events.length} events</span>
                      </div>
                      <div className="divide-y divide-line/60">
                        {events.map((e) => {
                          const Icon = e.actorKind in KIND_ICON ? KIND_ICON[e.actorKind as keyof typeof KIND_ICON] : null;
                          return (
                            <div key={e.id} className={cn('flex items-start gap-3 px-3.5 py-2', e.level === 'err' && 'bg-danger/5', Number(e.id) > seen && 'animate-slide-up bg-brand/5')}>
                              <span className="tnum mt-px w-14 shrink-0 font-mono text-[11.5px] text-dim">{clock(e.at)}</span>
                              <span className={cn('mt-px shrink-0',
                                e.actorKind === 'human' ? 'text-brand' : e.actorKind === 'system' ? 'text-violet' : 'text-ok')}>
                                {Icon && <Icon className="size-3.5" />}
                              </span>
                              <span className="hidden w-36 shrink-0 truncate text-[12.5px] text-soft sm:block">{e.actor}</span>
                              <span className="min-w-0 flex-1">
                                <span className="flex flex-wrap items-center gap-1.5">
                                  <Dot state={e.level === 'err' ? 'error' : e.level} />
                                  <span className="text-[13px] font-medium text-ink">{e.action}</span>
                                  {e.taskRef && <Mono>{e.taskRef}</Mono>}
                                </span>
                                <span className="block text-[12.5px] text-dim"><span className="text-soft sm:hidden">{e.actor} · </span>{e.detail}</span>
                              </span>
                              <span className="eyebrow hidden shrink-0 md:inline">{projectLabel(projects, e.projectId)}</span>
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
              <Stat label="Events today" value={figures ? figures.today : '—'} sub={figures ? 'since midnight UTC, every project' : waiting} />
              <Stat label="Your actions" value={figures ? figures.mine : '—'} tone="brand" sub={figures ? `as ${user?.name ?? 'you'}, all time` : waiting} />
            </StatGrid>

            <Panel eyebrow="Who did the work" title="By actor kind" flush>
              <div className="divide-y divide-line">
                {KINDS.map((k) => {
                  const Icon = KIND_ICON[k];
                  const n = figures ? figures.kinds.get(k) ?? 0 : '—';
                  return (
                    <button key={k} onClick={() => setKind(kind === k ? 'all' : k)}
                      className={cn('flex w-full items-center gap-2.5 px-3.5 py-2 text-left hover:bg-surface-2', kind === k && 'bg-surface-2')}>
                      <Icon className="size-3.5 text-dim" />
                      <span className="flex-1 text-[13px] text-ink-2 capitalize">{k}</span>
                      <Tag tone={KIND_TONE[k]}>{n}</Tag>
                    </button>
                  );
                })}
              </div>
            </Panel>

            <Panel eyebrow="Busiest agents" title="Event count" flush>
              {!figures ? (
                <p className="px-3.5 py-2.5 text-[12.5px] text-dim">{waiting}</p>
              ) : figures.busiest.length === 0 ? (
                <p className="px-3.5 py-2.5 text-[12.5px] text-dim">No agent has done anything yet.</p>
              ) : <div className="divide-y divide-line">
                {figures.busiest.map(([name, n]) => (
                  <div key={name} className="flex items-center gap-2.5 px-3.5 py-1.5">
                    <Bot className="size-3 text-ok" />
                    <span className="flex-1 truncate text-[13px] text-ink-2">{name}</span>
                    <span className="tnum text-[12.5px] text-soft">{n}</span>
                  </div>
                ))}
              </div>}
            </Panel>

            <Panel eyebrow={cut ? 'What you did, in the latest events' : 'What you did'} title="Your actions" flush>
              {mine.length === 0 ? (
                <p className="px-3.5 py-2.5 text-[12.5px] text-dim">Nothing you have done is in the log yet.</p>
              ) : <div className="divide-y divide-line">
                {mine.slice(0, 8).map((e) => (
                  <div key={e.id} className="px-3.5 py-2">
                    <div className="flex items-center gap-2">
                      <span className="tnum font-mono text-[11.5px] text-dim">{clock(e.at)}</span>
                      <span className="text-[13px] font-medium text-ink">{e.action}</span>
                    </div>
                    <p className="mt-0.5 truncate text-[12px] text-dim">{e.detail}</p>
                  </div>
                ))}
              </div>}
            </Panel>

            {project && hooks && hooks.pid === projectId && (
              <Panel eyebrow={`Hooks · ${project.name}`} title={<span className="flex items-center gap-1.5"><Webhook className="size-3.5 text-warn" />Deterministic automation</span>} flush>
                <div className="px-3.5 py-2">
                  <KV k="Configured" v={hooks.data.hooks.length} />
                  <KV k="Can refuse an action" v={hooks.data.hooks.filter((h) => h.blocking).length} />
                  <p className="mt-1.5 text-[12px] text-dim">Claude Code keeps no readable record of a hook firing, so none appears in this log.</p>
                </div>
              </Panel>
            )}
          </div>
        </div>
      </PageBody>
    </Page>
  );
}
