import { useEffect, useMemo, useRef, useState } from 'react';
import { Pause, Play, Square, FolderGit2, ArrowDownToLine, GitMerge } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/switch';
import {
  Page, PageHeader, PageBody, Tag, Dot, Mono, BlockBar, ListRow, KV,
  Stat, StatGrid, Segmented, Empty,
} from '@/components/os';
import { runs as seedRuns, streamPool } from '@/mock/runs';
import { useData } from '@/lib/data';
import { cn } from '@/lib/utils';
import type { RunLogLine } from '@/types';
import { LiveRuns } from './runs/LiveRuns';

/** Signed in, these are real worktrees; the demo keeps the worked example of a parallel batch. */
export default function Runs() {
  const { mode } = useData();
  return mode === 'live' ? <LiveRuns /> : <SampleRuns />;
}

const LEVEL_TONE: Record<RunLogLine['level'], string> = {
  info: 'text-ink-2', ok: 'text-ok', warn: 'text-warn', err: 'text-danger', tool: 'text-brand',
};
const LEVEL_MARK: Record<RunLogLine['level'], string> = { info: '·', ok: '✓', warn: '!', err: '✗', tool: '›' };

const clock = (base: string, step: number) => {
  const [h, m, s] = base.split(':').map(Number);
  const t = h * 3600 + m * 60 + s + step * 7;
  return [Math.floor(t / 3600) % 24, Math.floor(t / 60) % 60, t % 60]
    .map((n) => String(n).padStart(2, '0')).join(':');
};

function SampleRuns() {
  const [live, setLive] = useState(true);
  const [follow, setFollow] = useState(true);
  const [levels, setLevels] = useState<'all' | RunLogLine['level']>('all');
  const [sel, setSel] = useState(seedRuns[0].id);
  const [tick, setTick] = useState(0);
  const [extra, setExtra] = useState<Record<string, RunLogLine[]>>({});
  const [progress, setProgress] = useState<Record<string, number>>(
    () => Object.fromEntries(seedRuns.map((r) => [r.id, r.progress])),
  );
  const logRef = useRef<HTMLDivElement>(null);

  /* ── Simulated liveness: advance progress and append tool output ── */
  useEffect(() => {
    if (!live) return;
    const id = window.setInterval(() => {
      setTick((t) => {
        const next = t + 1;
        setProgress((p) => {
          const out = { ...p };
          seedRuns.forEach((r) => {
            if (r.status !== 'running') return;
            const cur = out[r.id] ?? r.progress;
            if (cur < 100) out[r.id] = Math.min(100, cur + (r.id.charCodeAt(5) % 3) + 1);
          });
          return out;
        });
        setExtra((prev) => {
          const out = { ...prev };
          seedRuns.forEach((r) => {
            if (r.status !== 'running') return;
            const pool = streamPool[r.agentId];
            if (!pool?.length) return;
            const line = pool[next % pool.length];
            const cur = out[r.id] ?? [];
            if (cur.length > 60) return;
            out[r.id] = [...cur, { t: clock(r.startedAt, next), level: line.level, text: line.text }];
          });
          return out;
        });
        return next;
      });
    }, 1500);
    return () => window.clearInterval(id);
  }, [live]);

  const run = useMemo(() => seedRuns.find((r) => r.id === sel) ?? seedRuns[0], [sel]);
  const log = useMemo(() => {
    const all = [...run.log, ...(extra[run.id] ?? [])];
    return levels === 'all' ? all : all.filter((l) => l.level === levels);
  }, [run, extra, levels]);

  useEffect(() => {
    if (follow && logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [log.length, follow]);

  const running = seedRuns.filter((r) => r.status === 'running');
  const totalTokens = seedRuns.reduce((n, r) => n + r.tokensIn + r.tokensOut, 0);

  return (
    <Page>
      <PageHeader
        title="Live Runs"
        subtitle="Every agent works in its own git worktree, so nothing they do can collide. This is the raw output."
        actions={
          <>
            <label className="flex items-center gap-2 text-[13px] text-soft">
              <Switch checked={live} onCheckedChange={setLive} />stream
            </label>
            <Button size="sm" variant="outline" onClick={() => toast('Killing all runs requires a confirmation in the real system')}>
              <Square className="size-3.5" />Stop all
            </Button>
          </>
        }
      >
        <div className="flex flex-wrap items-center gap-3 pb-3">
          <Mono tone="brand">TASK-492</Mono>
          <span className="text-[13px] text-ink-2">Fix invoice tax calculation — interstate CGST/SGST reversal</span>
          <Tag tone="warn">plan step 6 of 8 · run regression</Tag>
          <span className="ml-auto flex items-center gap-1.5 text-[12.5px] text-dim">
            <GitMerge className="size-3.5" />merge gate waits for your approval after review
          </span>
        </div>
      </PageHeader>

      <PageBody className="flex h-full flex-col gap-3 p-0">
        <div className="shrink-0 px-4 pt-4 sm:px-6">
          <StatGrid cols={5}>
            <Stat label="Running" value={running.length} tone="ok" sub={`${seedRuns.length} runs in this task`} />
            <Stat label="Worktrees" value={new Set(seedRuns.map((r) => r.worktree)).size} sub="isolated, never shared" />
            <Stat label="Tokens" value={`${(totalTokens / 1_000_000).toFixed(2)}M`} sub="in + out, this task" />
            <Stat label="Cost" value={`$${seedRuns.reduce((n, r) => n + r.cost, 0).toFixed(2)}`} tone="brand" sub="local models carry most of it" />
            <Stat label="Stream" value={live ? 'live' : 'paused'} tone={live ? 'ok' : 'neutral'} sub={`${tick} ticks`} />
          </StatGrid>
        </div>

        <div className="flex min-h-0 flex-1 flex-col gap-3 px-4 pb-4 sm:px-6 sm:pb-5 md:flex-row">
          {/* Run list */}
          <div className="flex w-full shrink-0 max-h-[42vh] md:max-h-none md:w-[340px] flex-col rounded-md border border-line bg-surface overflow-y-auto">
            <div className="shrink-0 border-b border-line px-3.5 py-2.5">
              <div className="eyebrow">Parallel agents</div>
            </div>
            <div className="no-scrollbar min-h-0 flex-1 overflow-y-auto">
              {seedRuns.map((r) => {
                const pct = progress[r.id] ?? r.progress;
                return (
                  <ListRow key={r.id} active={r.id === sel} onClick={() => setSel(r.id)}>
                    <div className="flex items-center gap-2">
                      <Dot state={r.status} pulse={r.status === 'running' && live} />
                      <span className="truncate text-[13.5px] font-medium text-ink">{r.agentName}</span>
                      <span className="ml-auto tnum text-[12px] text-soft">{pct}%</span>
                    </div>
                    <div className="mt-1 flex items-center gap-2">
                      <BlockBar pct={pct} width={16} />
                    </div>
                    <p className="mt-1 truncate text-[11.5px] text-dim">{r.step}</p>
                    <div className="mt-1 flex items-center gap-2 text-[11px] text-dim">
                      <Mono>{r.worktree}</Mono>
                      <span className="ml-auto">{r.elapsed}</span>
                    </div>
                  </ListRow>
                );
              })}
            </div>
          </div>

          {/* Terminal */}
          <div className="flex min-w-0 flex-1 flex-col rounded-md border border-line bg-surface">
            <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-line px-3.5 py-2.5">
              <div className="flex min-w-0 items-center gap-2">
                <Dot state={run.status} pulse={run.status === 'running' && live} />
                <span className="truncate text-[13.5px] font-semibold text-ink">{run.agentName}</span>
                <Mono>{run.model}</Mono>
                <Tag tone="neutral">{run.taskRef}</Tag>
              </div>
              <div className="flex items-center gap-1.5">
                <Segmented
                  options={[{ id: 'all', label: 'all' }, { id: 'tool', label: 'tool' }, { id: 'ok', label: 'ok' }, { id: 'warn', label: 'warn' }, { id: 'err', label: 'err' }]}
                  value={levels}
                  onChange={(v) => setLevels(v as typeof levels)}
                />
                <Button size="icon-xs" variant={follow ? 'default' : 'outline'} onClick={() => setFollow((f) => !f)} title="Auto-scroll">
                  <ArrowDownToLine className="size-3" />
                </Button>
              </div>
            </div>

            <div ref={logRef} className="min-h-0 flex-1 overflow-y-auto bg-base px-3.5 py-2.5 font-mono text-[12.5px] leading-[1.6]">
              {log.length === 0 ? <Empty title="No output at that level" hint="Switch the filter back to all." /> : log.map((l, i) => (
                <div key={i} className="flex gap-2.5">
                  <span className="shrink-0 text-dim">{l.t}</span>
                  <span className={cn('w-2.5 shrink-0 text-center', LEVEL_TONE[l.level])}>{LEVEL_MARK[l.level]}</span>
                  <span className={cn('min-w-0 break-words', LEVEL_TONE[l.level])}>{l.text}</span>
                </div>
              ))}
              {live && run.status === 'running' && (
                <div className="flex gap-2.5 text-dim">
                  <span className="shrink-0">{clock(run.startedAt, tick + 1)}</span>
                  <span className="w-2.5 shrink-0 text-center animate-pulse-dot">▊</span>
                </div>
              )}
            </div>

            <div className="shrink-0 border-t border-line px-3.5 py-2.5">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 xl:grid-cols-4">
                <KV k="Worktree" v={run.worktree} mono />
                <KV k="Started" v={run.startedAt} />
                <KV k="Tokens" v={`${(run.tokensIn / 1000).toFixed(0)}k in · ${(run.tokensOut / 1000).toFixed(0)}k out`} />
                <KV k="Cost" v={run.cost === 0 ? 'free (local)' : `$${run.cost.toFixed(2)}`} />
              </div>
              <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
                <div className="flex flex-wrap gap-1">
                  {run.filesTouched.map((f) => <Mono key={f}>{f}</Mono>)}
                </div>
                <div className="flex shrink-0 gap-1.5">
                  <Button size="xs" variant="outline" onClick={() => toast(`${run.agentName} paused`)}><Pause className="size-3" />Pause</Button>
                  <Button size="xs" variant="outline" onClick={() => toast(`${run.agentName} resumed`)}><Play className="size-3" />Resume</Button>
                  <Button size="xs" variant="destructive" onClick={() => toast(`${run.agentName} killed — worktree kept for inspection`)}><Square className="size-3" />Kill</Button>
                  <Button size="xs" variant="ghost" onClick={() => toast(`Opening ${run.worktree}`)}><FolderGit2 className="size-3" />Worktree</Button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </PageBody>
    </Page>
  );
}
