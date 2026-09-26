import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { ChevronDown, ChevronRight, CircleAlert, CircleCheck, CircleX, Info, ListChecks, Loader2, Play, Square, TriangleAlert } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { About, Empty, Tag, cx, type Tone } from '@/components/os';
import { ApiError } from '@/lib/api';
import {
  announce, diagnosticsApi, type CheckDoc, type CheckTool, type CheckWhere, type Checkers, type Problem, type Severity,
} from '@/lib/live/diagnostics';
import { sourcesApi, type ProjectSource } from '@/lib/live/sources';

/* The Problems tab: press Check and the project's own checkers run — only those it declares and this machine
   has — and what they print is read into problems, grouped by file. A click opens the file at the line. A
   checker the project declares but this machine lacks is named with what installs it, never skipped in silence,
   and "no problems" is only said when every checker that ran said so. */

const reason = (e: unknown) => (e instanceof ApiError ? e.message : 'The local API did not answer.');
/** The most problems one page shows; the rest are reached with the filters. */
const PAGE = 1000;
const POLL_MS = 700;

const SEV_ICON: Record<Severity, typeof CircleX> = { error: CircleX, warning: TriangleAlert, info: Info };
const SEV_TONE: Record<Severity, string> = { error: 'text-danger', warning: 'text-warn', info: 'text-info' };
const SEV_WORD: Record<Severity, [string, string]> = { error: ['error', 'errors'], warning: ['warning', 'warnings'], info: ['note', 'notes'] };
const TOOL_TONE: Record<CheckTool['status'], Tone> = {
  waiting: 'neutral', running: 'info', passed: 'ok', problems: 'warn', failed: 'danger', timeout: 'danger', cancelled: 'neutral', error: 'danger',
};
const TOOL_WORD: Record<CheckTool['status'], string> = {
  waiting: 'Waiting', running: 'Running', passed: 'Passed', problems: 'Problems', failed: 'Failed', timeout: 'Timed out', cancelled: 'Cancelled', error: 'Did not start',
};

const plural = (n: number, s: Severity) => `${n.toLocaleString()} ${SEV_WORD[s][n === 1 ? 0 : 1]}`;
const seconds = (ms: number | null) => (ms === null ? '' : ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`);
const names = (list: string[]) => (list.length <= 1 ? list.join('') : `${list.slice(0, -1).join(', ')} and ${list[list.length - 1]}`);

/** Which of the project's sources can be checked: those on this machine. */
const checkable = (sources: ProjectSource[]) =>
  [...sources].filter((s) => s.status === 'active' && s.root).sort((a, b) => (a.primary ? -1 : b.primary ? 1 : a.position - b.position));

export function ProblemsPanel({ projectId, cwd, openFile }: {
  /** The project the Workbench shows, or null while it shows a folder of the machine. */
  projectId: string | null;
  /** The folder shown when there is no project. */
  cwd: string | null;
  openFile: (path: string, line?: number) => void;
}) {
  const [sources, setSources] = useState<ProjectSource[] | null>(null);
  const [source, setSource] = useState<string | null>(null);
  const [plan, setPlan] = useState<Checkers | null>(null);
  const [planError, setPlanError] = useState<string | null>(null);
  const [check, setCheck] = useState<CheckDoc | null>(null);
  const [severity, setSeverity] = useState<Severity | null>(null);
  const [tool, setTool] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [folded, setFolded] = useState<Set<string>>(() => new Set());
  const [shownOutput, setShownOutput] = useState<string | null>(null);

  // The project's sources, when there is more than one to choose from.
  useEffect(() => {
    if (!projectId) return;
    let current = true;
    sourcesApi.list(projectId).then(
      (list) => { if (current) setSources(checkable(list)); },
      (e: unknown) => { if (current) { setSources([]); console.warn('[NeuroCode] the sources were not read for Problems:', e); } },
    );
    return () => { current = false; };
  }, [projectId]);

  const target: CheckWhere | null = useMemo(() => {
    if (projectId) return { projectId, source: source ?? undefined };
    return cwd ? { folder: cwd } : null;
  }, [projectId, source, cwd]);
  const targetKey = target ? JSON.stringify(target) : null;

  // What a check here would run, and the newest check of this folder.
  const [loaded, setLoaded] = useState<string | null>(null);
  useEffect(() => {
    if (!target) return;
    let current = true;
    void (async () => {
      try {
        const found = await diagnosticsApi.checkers(target);
        if (!current) return;
        setPlan(found);
        setPlanError(null);
        const recent = await diagnosticsApi.recent('folder' in target ? target : { projectId: target.projectId });
        if (!current) return;
        const mine = recent.find((c) => c.target.folder === found.target.folder) ?? null;
        setCheck(mine ? await diagnosticsApi.check(mine.id, { limit: PAGE }) : null);
      } catch (e) {
        if (current) { setPlan(null); setPlanError(reason(e)); }
      } finally {
        if (current) setLoaded(targetKey);
      }
    })();
    return () => { current = false; };
    // targetKey stands for target.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targetKey]);

  // A check that is running is read again until it ends; then everyone who shows problems hears of it.
  const checkId = check?.id ?? null;
  const running = check?.status === 'running';
  const filters = useRef({ severity, tool });
  useEffect(() => { filters.current = { severity, tool }; });
  useEffect(() => {
    if (!checkId || !running) return;
    let current = true;
    const timer = window.setInterval(() => {
      diagnosticsApi.check(checkId, { limit: PAGE, ...filters.current }).then(async (doc) => {
        if (!current) return;
        setCheck(doc);
        if (doc.status !== 'running') await announce(target, true);
      }, (e: unknown) => console.warn('[NeuroCode] the check was not read:', e));
    }, POLL_MS);
    return () => { current = false; window.clearInterval(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [checkId, running]);

  // A filter reads the check again on the server, which holds up to 5,000 problems.
  const refilter = async (next: { severity?: Severity | null; tool?: string | null }) => {
    const s = next.severity === undefined ? severity : next.severity;
    const t = next.tool === undefined ? tool : next.tool;
    setSeverity(s);
    setTool(t);
    if (!check) return;
    try {
      setCheck(await diagnosticsApi.check(check.id, { limit: PAGE, severity: s, tool: t }));
    } catch (e) {
      toast.error('The problems were not read', { description: reason(e) });
    }
  };

  const start = async () => {
    if (!target) return;
    setBusy(true);
    try {
      const doc = await diagnosticsApi.start(target);
      setSeverity(null);
      setTool(null);
      setFolded(new Set());
      setCheck(doc);
      await announce(target, false);
    } catch (e) {
      toast.error('The check did not start', { description: reason(e) });
    } finally {
      setBusy(false);
    }
  };
  const cancel = async () => {
    if (!check) return;
    setBusy(true);
    try {
      await diagnosticsApi.cancel(check.id);
      setCheck(await diagnosticsApi.check(check.id, { limit: PAGE, severity, tool }));
      await announce(target, true);
    } catch (e) {
      toast.error('The check was not stopped', { description: reason(e) });
    } finally {
      setBusy(false);
    }
  };

  const groups = useMemo(() => {
    const by = new Map<string, { file: string; path: string; items: Problem[] }>();
    for (const p of check?.problems ?? []) {
      const g = by.get(p.path) ?? { file: p.file, path: p.path, items: [] };
      g.items.push(p);
      by.set(p.path, g);
    }
    return [...by.values()];
  }, [check]);

  if (!target) {
    return <Empty icon={<ListChecks className="size-6" />} title="Nothing to check yet"
      hint="Open a project or folder on this machine to run its checkers." />;
  }
  if (planError && loaded === targetKey) {
    return <Empty icon={<CircleAlert className="size-6" />} title="The checkers were not read" hint={planError} />;
  }
  if (!plan || loaded !== targetKey) {
    return <Empty icon={<Loader2 className="size-5 animate-spin" />} title="Reading what this folder declares…" />;
  }

  const tools = check?.tools ?? [];
  const toolNames = [...new Set(tools.map((t) => t.tool))];
  const missing = check?.missing ?? plan.missing;
  const quiet = check && check.status === 'done' && check.total === 0 && tools.every((t) => t.status === 'passed');

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* The bar: where, what runs, the filters, and Check. */}
      <div className="flex shrink-0 flex-wrap items-center gap-x-2 gap-y-1.5 border-b border-line/60 px-3 py-1.5">
        {projectId && sources && sources.length > 1 && (
          <select value={source ?? ''} onChange={(e) => { setSource(e.target.value || null); setCheck(null); }} aria-label="Which folder to check"
            className="h-7 max-w-[160px] rounded-md border border-line-strong bg-surface-2 px-1.5 text-[12.5px] text-ink-2">
            {sources.map((s) => <option key={s.label} value={s.primary ? '' : s.label} className="bg-surface">{s.label}</option>)}
          </select>
        )}
        <span className="min-w-0 flex-1 truncate text-[12px] text-soft" title={plan.checkers.map((c) => c.command).join('\n')}>
          {plan.checkers.length ? <>Runs <span className="text-ink-2">{names(plan.checkers.map((c) => c.label))}</span> in <span className="font-mono text-[11.5px]">{plan.target.name}</span></>
            : <>No checker in <span className="font-mono text-[11.5px]">{plan.target.name}</span></>}
        </span>
        {check && check.status !== 'running' && check.total > 0 && (
          <div role="group" aria-label="Filter by severity" className="flex items-center gap-0.5">
            <FilterChip on={severity === null} onClick={() => void refilter({ severity: null })}>All</FilterChip>
            {(['error', 'warning', 'info'] as Severity[]).filter((s) => check.counts[s] > 0).map((s) => {
              const Icon = SEV_ICON[s];
              return (
                <FilterChip key={s} on={severity === s} onClick={() => void refilter({ severity: severity === s ? null : s })} label={plural(check.counts[s], s)}>
                  <Icon className={cx('size-3', SEV_TONE[s])} /><span className="tnum">{check.counts[s].toLocaleString()}</span>
                </FilterChip>
              );
            })}
          </div>
        )}
        {check && check.status !== 'running' && toolNames.length > 1 && check.total > 0 && (
          <select value={tool ?? ''} onChange={(e) => void refilter({ tool: e.target.value || null })} aria-label="Filter by checker"
            className="h-7 rounded-md border border-line-strong bg-surface-2 px-1.5 text-[12.5px] text-ink-2">
            <option value="" className="bg-surface">Every checker</option>
            {toolNames.map((t) => <option key={t} value={t} className="bg-surface">{t}</option>)}
          </select>
        )}
        {running ? (
          <Button size="xs" variant="outline" disabled={busy} onClick={() => void cancel()}><Square />Cancel</Button>
        ) : (
          <Button size="xs" disabled={busy || !plan.checkers.length} onClick={() => void start()}>
            {busy ? <Loader2 className="animate-spin" /> : <Play />}Check
          </Button>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {!plan.checkers.length && !check ? (
          <Empty icon={<ListChecks className="size-6" />} title={`No checker to run in ${plan.target.name}`}
            hint={plan.missing.length ? plan.missing.map((m) => m.why).join(' ') : 'This folder declares no checker.'}
            action={plan.missing.length ? undefined : (
              <About label="What counts as a checker">
                <p>A tsconfig, an ESLint config, or oxlint, ruff, mypy or pyright configured.</p>
                <p>Also a go.mod, a Cargo.toml, a Gradle or Maven build, or a .NET project.</p>
              </About>
            )} />
        ) : !check ? (
          <Empty icon={<ListChecks className="size-6" />} title="Not checked yet"
            hint={`Check runs ${names(plan.checkers.map((c) => c.label))}, as this project installed them.`}
            action={<Button size="sm" variant="outline" disabled={busy} onClick={() => void start()}><Play className="size-3.5" />Check</Button>} />
        ) : (
          <>
            {/* Each checker: how it went. */}
            <div className="flex flex-wrap gap-1.5 px-3 pt-2 pb-1">
              {tools.map((t) => (
                <button key={t.label} type="button" title={t.command}
                  onClick={() => setShownOutput((now) => (now === t.label ? null : t.label))}
                  className="inline-flex items-center gap-1.5 rounded-md border border-line/70 px-2 py-0.5 text-[12px] text-ink-2 hover:bg-surface-2">
                  {t.status === 'running' && <Loader2 className="size-3 animate-spin text-info" />}
                  <span className="font-medium">{t.label}</span>
                  <Tag tone={TOOL_TONE[t.status]}>{t.status === 'problems' ? `${t.problems.toLocaleString()} found` : TOOL_WORD[t.status]}</Tag>
                  {t.ms !== null && <span className="text-dim tnum">{seconds(t.ms)}</span>}
                </button>
              ))}
            </div>
            {tools.filter((t) => t.label === shownOutput || ((t.status === 'failed' || t.status === 'error' || t.status === 'timeout') && t.note)).map((t) => (
              <div key={t.label} className="mx-3 mt-1 rounded-md border border-line/70 bg-surface-2/60 px-2.5 py-1.5 text-[12px]">
                <div className="text-ink-2"><span className="font-mono text-[11.5px]">{t.command}</span></div>
                {t.note && <div className="mt-0.5 text-soft">{t.note}</div>}
                {t.output.length > 0 && <pre className="mt-1 max-h-32 overflow-auto font-mono text-[11.5px] leading-relaxed whitespace-pre-wrap text-soft">{t.output.join('\n')}</pre>}
              </div>
            ))}
            {missing.length > 0 && (
              <p className="px-3 pt-1 text-[12px] leading-relaxed text-dim">Not run: {missing.map((m) => m.why).join(' ')}</p>
            )}

            {check.status === 'running' && groups.length === 0 ? (
              <div className="flex items-center gap-2 px-3 py-4 text-[12.5px] text-dim"><Loader2 className="size-3.5 animate-spin" />Checking {check.target.name}…</div>
            ) : quiet ? (
              <div className="flex items-center gap-2 px-3 py-4 text-[13px] text-ink-2">
                <CircleCheck className="size-4 text-ok" />No problems — {names(tools.map((t) => t.label))} found nothing.
              </div>
            ) : check.status !== 'running' && check.total === 0 ? (
              <p className="px-3 py-4 text-[12.5px] text-dim">
                {check.status === 'cancelled' ? 'Cancelled before the checkers finished; nothing they printed named a problem.'
                  : 'No problem read from their output; see how each went above.'}
              </p>
            ) : (
              <div className="py-1">
                {groups.map((g) => {
                  const open = !folded.has(g.path);
                  const cut = g.file.lastIndexOf('/');
                  return (
                    <div key={g.path}>
                      <button type="button" aria-expanded={open}
                        onClick={() => setFolded((was) => { const next = new Set(was); if (open) next.add(g.path); else next.delete(g.path); return next; })}
                        className="flex w-full items-center gap-1.5 px-2 py-1 text-left hover:bg-surface-2/60">
                        {open ? <ChevronDown className="size-3.5 shrink-0 text-dim" /> : <ChevronRight className="size-3.5 shrink-0 text-dim" />}
                        <span className="shrink-0 text-[12.5px] font-medium text-ink">{g.file.slice(cut + 1)}</span>
                        <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-dim" title={g.path}>{cut > 0 ? g.file.slice(0, cut) : ''}</span>
                        <span className="shrink-0 rounded-full bg-surface-2 px-1.5 text-[11px] text-soft tnum">{g.items.length}</span>
                      </button>
                      {open && g.items.map((p, i) => {
                        const Icon = SEV_ICON[p.severity];
                        return (
                          <button key={`${p.line}:${p.col}:${i}`} type="button" onClick={() => openFile(p.path, p.line)}
                            title={p.message}
                            className="flex w-full items-start gap-2 py-1 pr-3 pl-7 text-left hover:bg-surface-2/60">
                            <Icon className={cx('mt-0.5 size-3.5 shrink-0', SEV_TONE[p.severity])} aria-label={p.severity} />
                            <span className="min-w-0 flex-1 text-[12.5px] leading-snug text-ink-2 [overflow-wrap:anywhere]">
                              {p.message.split('\n')[0]}
                              <span className="ml-1.5 text-[11.5px] text-dim">{p.tool}{p.code ? ` · ${p.code}` : ''}</span>
                            </span>
                            <span className="shrink-0 font-mono text-[11px] text-dim tnum">{p.line}:{p.col}</span>
                          </button>
                        );
                      })}
                    </div>
                  );
                })}
                {(check.matching > check.problems.length || check.capped) && (
                  <p className="px-3 py-2 text-[12px] text-dim">
                    {check.matching > check.problems.length ? `Showing ${check.problems.length.toLocaleString()} of ${check.matching.toLocaleString()}; filter to narrow. ` : ''}
                    {check.capped ? `The checkers named ${check.total.toLocaleString()} problems; the first ${check.kept.toLocaleString()} are kept.` : ''}
                  </p>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function FilterChip({ on, onClick, label, children }: { on: boolean; onClick: () => void; label?: string; children: ReactNode }) {
  return (
    <button type="button" aria-pressed={on} aria-label={label} onClick={onClick}
      className={cx('inline-flex h-6 items-center gap-1 rounded-md px-1.5 text-[12px] transition-colors', on ? 'bg-surface-2 text-ink' : 'text-soft hover:text-ink')}>
      {children}
    </button>
  );
}
