import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import {
  ArrowDownToLine, ArrowUpFromLine, Bug, ChevronDown, ChevronRight, CircleDot, Loader2, Pause, Pencil, Play, Plus,
  Redo2, RotateCcw, SendHorizontal, Square, X,
} from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Empty, Tag, cx } from '@/components/os';
import { ApiError } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import {
  debug, type DebugEvent, type DebugFrame, type DebugOutput, type DebugScope, type DebugSessionDoc, type DebugVariable,
  type StepCommand,
} from '@/lib/live/debug';
import { runConfigs, type RunConfig } from '@/lib/live/terminal';
import { RunConfigDialog } from '@/components/workbench/RunPanel';

/* The Debug tab: launch a project's program under a real debugger (debugpy for Python, node's inspector
   for Node), stop at the Workbench's breakpoints, step, read the call stack and variables, watch
   expressions and evaluate in a console. It talks to the editor through the Workbench's contract:
   breakpoints come in as a prop, where the program stopped goes out through onPausedAt, and a frame
   opens its file with openFile. */

export interface DebugPanelProps {
  projectId: string | null;
  /** The Workbench's breakpoints, by absolute path. Changing them while a session runs sends them at once. */
  breakpoints: Record<string, number[]>;
  /** Where the program is stopped (the chosen frame), or null while it runs or when nothing is debugged. */
  onPausedAt: (at: { path: string; line: number } | null) => void;
  openFile: (path: string, line?: number) => void;
  /** The file open in the editor, offered as "This file" when it is a Python or JavaScript file. */
  currentFile?: string | null;
}

type Line = { kind: 'out'; category: string; text: string } | { kind: 'in' | 'result' | 'error'; text: string };

const reason = (e: unknown) => (e instanceof ApiError ? e.message : 'The local API did not answer.');
const LIVE = new Set(['starting', 'running', 'paused']);
const MAX_LINES = 3000;
const RETRY_MS = [1000, 2000, 4000, 8000];
const DEBUGGABLE = /\.(py|js|mjs|cjs)$/;
const base = (path: string | null) => (path ? path.split('/').pop() ?? path : '');
const outLines = (output: DebugOutput[]): Line[] => output.map((o) => ({ kind: 'out', category: o.category, text: o.text }));
const sameLines = (a: number[] = [], b: number[] = []) => a.length === b.length && a.every((n, i) => n === b[i]);

function watchKey(projectId: string) { return `nc.debug.watch.${projectId}`; }
function readWatches(projectId: string): string[] {
  try {
    const raw = localStorage.getItem(watchKey(projectId));
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter((x): x is string => typeof x === 'string') : [];
  } catch {
    return [];
  }
}
function keepWatches(projectId: string, list: string[]) {
  try { localStorage.setItem(watchKey(projectId), JSON.stringify(list)); } catch { /* private mode: they last this visit */ }
}

/* ── the pieces ──────────────────────────────────────────────────── */

function Section({ title, right, children, className }: { title: string; right?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={cx('flex min-h-0 flex-col', className)}>
      <div className="flex shrink-0 items-center gap-2 px-3 pt-2 pb-1">
        <h3 className="flex-1 text-[12.5px] font-medium text-ink-2">{title}</h3>
        {right}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-1 pb-2">{children}</div>
    </section>
  );
}

function Quiet({ children }: { children: ReactNode }) {
  return <p className="px-2 py-1.5 text-[12.5px] leading-relaxed text-dim">{children}</p>;
}

/** One variable, its children read only when it is opened. */
function VariableNode({ sessionId, variable, depth }: { sessionId: string; variable: DebugVariable; depth: number }) {
  const [open, setOpen] = useState(false);
  const [children, setChildren] = useState<DebugVariable[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const toggle = async () => {
    if (!variable.ref) return;
    const next = !open;
    setOpen(next);
    if (next && children === null) {
      try {
        setChildren((await debug.variables(sessionId, variable.ref)).variables);
      } catch (e) {
        setError(reason(e));
      }
    }
  };
  return (
    <div>
      <button type="button" onClick={() => void toggle()} style={{ paddingLeft: 6 + depth * 14 }}
        className={cx('flex w-full items-baseline gap-1.5 rounded-md py-0.5 pr-2 text-left font-mono text-[12px]', variable.ref ? 'hover:bg-surface-2' : 'cursor-default')}>
        <span className="w-3 shrink-0 self-center text-dim">{variable.ref ? (open ? <ChevronDown className="size-3" /> : <ChevronRight className="size-3" />) : null}</span>
        <span className="shrink-0 text-info">{variable.name}</span>
        <span className="min-w-0 truncate text-ink-2" title={variable.value}>{variable.value}</span>
        {variable.type && <span className="ml-auto shrink-0 pl-2 text-[11px] text-dim">{variable.type}</span>}
      </button>
      {open && (error ? <Quiet>{error}</Quiet>
        : children === null ? <div style={{ paddingLeft: 26 + depth * 14 }}><Loader2 className="size-3 animate-spin text-dim" /></div>
          : children.length === 0 ? <div style={{ paddingLeft: 26 + depth * 14 }} className="text-[12px] text-dim">empty</div>
            : children.map((c, i) => <VariableNode key={`${c.name}:${i}`} sessionId={sessionId} variable={c} depth={depth + 1} />))}
    </div>
  );
}

/** A frame's scopes; the first that is cheap to read is opened. */
function Variables({ sessionId, frameId }: { sessionId: string; frameId: number }) {
  const [scopes, setScopes] = useState<{ frame: number; list: DebugScope[] | null; error: string | null } | null>(null);
  useEffect(() => {
    let current = true;
    debug.scopes(sessionId, frameId).then(
      (r) => { if (current) setScopes({ frame: frameId, list: r.scopes, error: null }); },
      (e: unknown) => { if (current) setScopes({ frame: frameId, list: null, error: reason(e) }); },
    );
    return () => { current = false; };
  }, [sessionId, frameId]);
  if (!scopes || scopes.frame !== frameId) return <div className="px-2 py-1"><Loader2 className="size-3.5 animate-spin text-dim" /></div>;
  if (scopes.error || !scopes.list) return <Quiet>{scopes.error}</Quiet>;
  if (scopes.list.length === 0) return <Quiet>This frame has no variables.</Quiet>;
  const first = scopes.list.findIndex((s) => !s.expensive);
  return (
    <>
      {scopes.list.map((s, i) => (
        <ScopeNode key={`${s.name}:${s.ref}`} sessionId={sessionId} scope={s} initiallyOpen={i === first} />
      ))}
    </>
  );
}

function ScopeNode({ sessionId, scope, initiallyOpen }: { sessionId: string; scope: DebugScope; initiallyOpen: boolean }) {
  const [open, setOpen] = useState(initiallyOpen);
  const [vars, setVars] = useState<{ list: DebugVariable[] | null; error: string | null } | null>(null);
  useEffect(() => {
    if (!open || vars) return;
    let current = true;
    debug.variables(sessionId, scope.ref).then(
      (r) => { if (current) setVars({ list: r.variables, error: null }); },
      (e: unknown) => { if (current) setVars({ list: null, error: reason(e) }); },
    );
    return () => { current = false; };
  }, [open, vars, sessionId, scope.ref]);
  return (
    <div>
      <button type="button" onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-1.5 rounded-md px-1.5 py-1 text-left text-[12.5px] font-medium text-ink-2 hover:bg-surface-2">
        {open ? <ChevronDown className="size-3 text-dim" /> : <ChevronRight className="size-3 text-dim" />}{scope.name}
      </button>
      {open && (!vars ? <div className="pl-6"><Loader2 className="size-3 animate-spin text-dim" /></div>
        : vars.error || !vars.list ? <Quiet>{vars.error}</Quiet>
          : vars.list.length === 0 ? <Quiet>Nothing in this scope.</Quiet>
            : vars.list.map((v, i) => <VariableNode key={`${v.name}:${i}`} sessionId={sessionId} variable={v} depth={1} />))}
    </div>
  );
}

/** Expressions evaluated in the chosen frame every time the program stops. Kept per project in this browser. */
function Watches({ projectId, sessionId, frameId, pauseKey }: {
  projectId: string; sessionId: string | null; frameId: number | null; pauseKey: string | null;
}) {
  const [list, setList] = useState<string[]>(() => readWatches(projectId));
  const [draft, setDraft] = useState('');
  const [results, setResults] = useState<{ key: string; values: Record<string, { result: string; error: boolean }> } | null>(null);
  const evalKey = sessionId && pauseKey && frameId !== null ? `${pauseKey}:${frameId}:${list.join('\u0000')}` : null;

  useEffect(() => {
    if (!evalKey || !sessionId || frameId === null) return;
    let current = true;
    Promise.all(list.map((expr) => debug.evaluate(sessionId, expr, frameId, 'watch').then(
      (r) => [expr, { result: r.result, error: r.type === 'error' }] as const,
      (e: unknown) => [expr, { result: reason(e), error: true }] as const,
    ))).then((pairs) => { if (current) setResults({ key: evalKey, values: Object.fromEntries(pairs) }); });
    return () => { current = false; };
  }, [evalKey, sessionId, frameId, list]);

  const change = (next: string[]) => { setList(next); keepWatches(projectId, next); };
  const values = results && results.key === evalKey ? results.values : null;
  return (
    <div>
      {list.map((expr) => {
        const v = values?.[expr];
        return (
          <div key={expr} className="group flex items-baseline gap-1.5 rounded-md py-0.5 pr-1 pl-2 font-mono text-[12px] hover:bg-surface-2">
            <span className="shrink-0 text-violet">{expr}</span>
            <span className={cx('min-w-0 flex-1 truncate', v?.error ? 'text-danger' : 'text-ink-2')} title={v?.result}>
              {!evalKey ? <span className="font-sans text-dim">not available while running</span> : v ? v.result : '…'}
            </span>
            <button type="button" aria-label={`Stop watching ${expr}`} onClick={() => change(list.filter((x) => x !== expr))}
              className="grid size-5 shrink-0 place-items-center self-center rounded text-dim opacity-0 group-hover:opacity-100 hover:text-ink focus:opacity-100">
              <X className="size-3" />
            </button>
          </div>
        );
      })}
      <form className="px-1 pt-1" onSubmit={(e) => { e.preventDefault(); const x = draft.trim(); if (x && !list.includes(x)) change([...list, x]); setDraft(''); }}>
        <Input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="Add an expression to watch" aria-label="Add an expression to watch"
          className="h-7 font-mono text-[12px]" />
      </form>
    </div>
  );
}

/* ── the Debug tab ───────────────────────────────────────────────── */

/** The Debug tab of the Workbench's bottom panel. */
export function DebugPanel({ projectId, breakpoints, onPausedAt, openFile, currentFile = null }: DebugPanelProps) {
  const { machine } = useAuth();
  const allowed = machine;
  const [configs, setConfigs] = useState<RunConfig[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  const [target, setTarget] = useState<string>('');
  const [session, setSession] = useState<DebugSessionDoc | null>(null);
  const [lines, setLines] = useState<Line[]>([]);
  const [pick, setPick] = useState<{ key: string; index: number } | null>(null);
  const [starting, setStarting] = useState(false);
  const [acting, setActing] = useState<string | null>(null);
  const [linkLost, setLinkLost] = useState(false);
  const [editing, setEditing] = useState<RunConfig | null>(null);
  const [dialog, setDialog] = useState(false);
  const [repl, setRepl] = useState('');
  const sent = useRef<Record<string, number[]>>({});
  const status = useRef<string>('');
  const paused = useRef(onPausedAt);
  const opener = useRef(openFile);
  const consoleEnd = useRef<HTMLDivElement>(null);
  useEffect(() => { paused.current = onPausedAt; opener.current = openFile; status.current = session?.status ?? ''; });

  // The project's debug configurations, and a session of yours still running here (after a reload).
  useEffect(() => {
    if (!allowed || !projectId) return;
    let current = true;
    Promise.all([runConfigs.list(projectId, 'debug'), debug.list(projectId)]).then(
      ([list, sessions]) => {
        if (!current) return;
        setConfigs(list);
        setLoadError(null);
        const live = [...sessions].reverse().find((s) => LIVE.has(s.status));
        if (live) setSession((now) => now ?? live);
      },
      (e: unknown) => { if (current) setLoadError(reason(e)); },
    );
    return () => { current = false; };
  }, [allowed, projectId, version]);

  // The session's events. The first state after (re)connecting carries the output so far; later ones do not.
  const sessionId = session?.id ?? null;
  useEffect(() => {
    if (!sessionId) return;
    let ws: WebSocket | null = null;
    let timer: number | undefined;
    let attempt = 0;
    let over = false;
    const connect = () => {
      let first = true;
      const socket = new WebSocket(debug.socket(sessionId));
      ws = socket;
      socket.onopen = () => { attempt = 0; setLinkLost(false); };
      socket.onmessage = (event: MessageEvent<string>) => {
        let e: DebugEvent;
        try { e = JSON.parse(event.data) as DebugEvent; } catch { return; }
        if (e.type === 'state') {
          setSession(e.session);
          if (first) setLines(outLines(e.session.output));
          first = false;
        } else if (e.type === 'output') {
          setLines((now) => [...now, { kind: 'out' as const, category: e.category, text: e.text }].slice(-MAX_LINES));
        } else {
          debug.get(sessionId).then((s) => { setSession(s); setLines(outLines(s.output)); }, () => undefined);
        }
      };
      socket.onclose = (event) => {
        if (over || ws !== socket) return;
        if (event.code >= 4400 || !LIVE.has(status.current)) return;
        setLinkLost(true);
        timer = window.setTimeout(connect, RETRY_MS[Math.min(attempt++, RETRY_MS.length - 1)]);
      };
    };
    connect();
    return () => { over = true; window.clearTimeout(timer); ws?.close(); };
  }, [sessionId]);

  const live = !!session && LIVE.has(session.status);
  const isPaused = session?.status === 'paused';
  const pauseKey = isPaused && session ? `${session.id}:${session.restarts}:${session.stopped?.reason}:${session.frames[0]?.path}:${session.frames[0]?.line}` : null;
  const frameIndex = pick && pick.key === pauseKey ? pick.index : 0;
  const frame: DebugFrame | null = isPaused && session ? session.frames[frameIndex] ?? null : null;
  const at = frame?.path && frame.line ? { path: frame.path, line: frame.line } : null;

  // Where the editor highlights: the chosen frame while paused, nothing otherwise — and nothing once gone.
  const atPath = at?.path ?? null;
  const atLine = at?.line ?? null;
  useEffect(() => { paused.current(atPath && atLine ? { path: atPath, line: atLine } : null); }, [atPath, atLine]);
  useEffect(() => () => paused.current(null), []);
  // A new stop takes the editor to it, as any debugger does.
  const top = isPaused && session ? session.frames[0] : null;
  const topPath = top?.path ?? null;
  const topLine = top?.line ?? null;
  useEffect(() => {
    if (pauseKey && topPath) opener.current(topPath, topLine ?? undefined);
  }, [pauseKey, topPath, topLine]);

  // Breakpoints toggled in the editor while the program runs reach the debugger at once.
  useEffect(() => {
    if (!sessionId || !live) return;
    const paths = new Set([...Object.keys(breakpoints), ...Object.keys(sent.current)]);
    for (const path of paths) {
      const lines = [...(breakpoints[path] ?? [])].sort((a, b) => a - b);
      if (sameLines(lines, sent.current[path])) continue;
      sent.current = { ...sent.current, [path]: lines };
      debug.setBreakpoints(sessionId, path, lines).catch((e: unknown) => toast.error(`Breakpoints in ${base(path)} were not set`, { description: reason(e) }));
    }
  }, [breakpoints, sessionId, live]);

  useEffect(() => { consoleEnd.current?.scrollIntoView({ block: 'end' }); }, [lines.length]);

  const choices = useMemo(() => {
    const list = (configs ?? []).map((c) => ({ id: `config:${c.id}`, label: c.name, config: c as RunConfig | null }));
    if (currentFile && DEBUGGABLE.test(currentFile)) list.push({ id: 'file', label: `This file · ${base(currentFile)}`, config: null });
    return list;
  }, [configs, currentFile]);
  const chosen = choices.find((c) => c.id === target) ?? choices[0] ?? null;

  const start = async () => {
    if (!projectId || !chosen) return;
    setStarting(true);
    const marks = Object.fromEntries(Object.entries(breakpoints).map(([p, l]) => [p, [...l].sort((a, b) => a - b)]));
    try {
      const made = await debug.start(projectId, chosen.config ? { runConfigId: chosen.config.id, breakpoints: marks }
        : { program: currentFile ?? '', breakpoints: marks });
      sent.current = marks;
      setLines(outLines(made.output));
      setSession(made);
    } catch (e) {
      toast.error('The debugger did not start', { description: reason(e) });
    } finally {
      setStarting(false);
    }
  };

  const act = async (name: string, run: () => Promise<unknown>) => {
    setActing(name);
    try { await run(); } catch (e) { toast.error('The debugger refused', { description: reason(e) }); } finally { setActing(null); }
  };
  const step = (name: StepCommand) => {
    if (!session) return;
    void act(name, () => debug.step(session.id, name, session.stopped?.threadId));
  };
  const stop = () => { if (session) void act('stop', () => debug.terminate(session.id)); };
  const restart = () => {
    if (!session) return;
    sent.current = Object.fromEntries(Object.entries(breakpoints).map(([p, l]) => [p, [...l].sort((a, b) => a - b)]));
    void act('restart', () => debug.restart(session.id));
  };
  const close = () => {
    if (!session) return;
    const id = session.id;
    setSession(null);
    setLines([]);
    debug.end(id).catch(() => undefined);
  };

  // F5 continue, Shift+F5 stop, F10 step over, F11 step into, Shift+F11 step out — only while debugging,
  // so F5 still reloads the page otherwise.
  useEffect(() => {
    if (!live) return;
    const onKey = (e: KeyboardEvent) => {
      const keys: Record<string, (() => void) | undefined> = {
        F5: e.shiftKey ? stop : isPaused ? () => step('continue') : undefined,
        F10: isPaused ? () => step('next') : undefined,
        F11: isPaused ? () => step(e.shiftKey ? 'stepOut' : 'stepIn') : undefined,
      };
      const run = keys[e.key];
      if (!run) return;
      e.preventDefault();
      run();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  const evaluate = async () => {
    const expr = repl.trim();
    if (!expr || !session) return;
    setRepl('');
    setLines((now) => [...now, { kind: 'in' as const, text: expr }]);
    try {
      const r = await debug.evaluate(session.id, expr, frame?.id ?? null, 'repl');
      setLines((now) => [...now, { kind: r.type === 'error' ? 'error' as const : 'result' as const, text: r.result }]);
    } catch (e) {
      setLines((now) => [...now, { kind: 'error' as const, text: reason(e) }]);
    }
  };

  if (!allowed) {
    return <Empty icon={<Bug className="size-6" />} title="Debugging needs the machine:access permission"
      hint="The debugger runs programs on the machine the API runs on, so only an Owner holds it unless an Owner grants it." />;
  }
  if (!projectId) {
    return <Empty icon={<Bug className="size-6" />} title="Debugging belongs to a project"
      hint="Choose a project in the top bar, or onboard this folder as a project, to debug its programs." />;
  }
  if (loadError && !configs) {
    return <Empty title="The debug configurations did not load" hint={loadError}
      action={<Button size="sm" variant="outline" onClick={() => setVersion((v) => v + 1)}>Try again</Button>} />;
  }
  if (!configs) return <Empty icon={<Loader2 className="size-5 animate-spin" />} title="Reading the debug configurations…" />;

  const ended = session && !live;
  const statusText = !session ? null
    : session.status === 'starting' ? 'Starting…'
      : session.status === 'running' ? 'Running'
        : session.status === 'paused' ? `Paused${session.stopped?.reason ? ` on ${session.stopped.reason}` : ''}${top?.path ? ` · ${base(top.path)}:${top.line}` : ''}`
          : session.status === 'failed' ? `Failed · ${session.note}`
            : `Ended${session.exitCode === null ? '' : ` · exit ${session.exitCode}`}`;
  const bpList = Object.entries(breakpoints).flatMap(([path, ls]) => ls.map((line) => ({ path, line })));
  const verified = (path: string, line: number) => session?.breakpoints[path]?.find((b) => b.line === line)?.verified;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 flex-wrap items-center gap-1.5 border-b border-line/60 px-3 py-1.5">
        {!live ? (
          <>
            {choices.length > 0 ? (
              <select value={chosen?.id ?? ''} onChange={(e) => setTarget(e.target.value)} aria-label="What to debug"
                className="h-7 max-w-[220px] rounded-md border border-line-strong bg-surface-2 px-2 text-[12.5px] text-ink-2">
                {choices.map((c) => <option key={c.id} value={c.id} className="bg-surface">{c.label}</option>)}
              </select>
            ) : (
              <span className="text-[12.5px] text-dim">No debug configuration yet</span>
            )}
            <Button size="xs" disabled={!chosen || starting} onClick={() => void start()}>
              {starting ? <Loader2 className="animate-spin" /> : <Play />}{ended ? 'Debug again' : 'Start debugging'}
            </Button>
            {chosen?.config && (
              <Button size="icon-xs" variant="ghost" aria-label={`Change ${chosen.config.name}`} title="Change"
                onClick={() => { setEditing(chosen.config); setDialog(true); }}><Pencil /></Button>
            )}
            <Button size="xs" variant="ghost" onClick={() => { setEditing(null); setDialog(true); }}><Plus />New configuration</Button>
            {ended && <Button size="xs" variant="ghost" onClick={close}><X />Close</Button>}
          </>
        ) : (
          <>
            <ToolButton label="Continue (F5)" disabled={!isPaused || !!acting} onClick={() => step('continue')}><Play /></ToolButton>
            <ToolButton label="Pause" disabled={session?.status !== 'running' || !!acting} onClick={() => step('pause')}><Pause /></ToolButton>
            <ToolButton label="Step over (F10)" disabled={!isPaused || !!acting} onClick={() => step('next')}><Redo2 /></ToolButton>
            <ToolButton label="Step into (F11)" disabled={!isPaused || !!acting} onClick={() => step('stepIn')}><ArrowDownToLine /></ToolButton>
            <ToolButton label="Step out (Shift+F11)" disabled={!isPaused || !!acting} onClick={() => step('stepOut')}><ArrowUpFromLine /></ToolButton>
            <span className="mx-1 h-4 w-px bg-line" />
            <ToolButton label="Restart" disabled={!!acting} onClick={restart}><RotateCcw /></ToolButton>
            <ToolButton label="Stop (Shift+F5)" disabled={!!acting} onClick={stop}><Square /></ToolButton>
          </>
        )}
        <span className="flex-1" />
        {session && (
          <span className="flex min-w-0 items-center gap-1.5 text-[12px] text-soft">
            {linkLost && <Tag tone="warn">Reconnecting</Tag>}
            <Tag tone={session.language === 'python' ? 'info' : 'ok'}>{session.language === 'python' ? 'Python' : 'Node'}</Tag>
            <span className={cx('truncate', session.status === 'failed' && 'text-danger', session.status === 'paused' && 'text-warn')} title={statusText ?? ''}>{statusText}</span>
          </span>
        )}
      </div>

      {!session ? (
        <Empty icon={<Bug className="size-6" />}
          title={choices.length ? 'Nothing is being debugged' : 'Nothing to debug yet'}
          hint={choices.length
            ? 'Set breakpoints in the editor’s gutter, then start debugging. Python runs with the project’s own .venv when it has one.'
            : 'Add a debug configuration — a .py or .js file of the checkout and its arguments — or open a Python or JavaScript file in the editor.'}
          action={choices.length ? undefined : <Button size="sm" variant="outline" onClick={() => { setEditing(null); setDialog(true); }}><Plus className="size-3.5" />New debug configuration</Button>} />
      ) : (
        <div className="grid min-h-0 flex-1 grid-cols-1 overflow-y-auto md:grid-cols-[minmax(0,0.9fr)_minmax(0,1.15fr)_minmax(0,1.35fr)] md:divide-x md:divide-line/60 md:overflow-hidden">
          <div className="flex min-h-0 flex-col">
            <Section title="Call stack" className="md:flex-[3]">
              {!isPaused ? <Quiet>{live ? 'Frames appear when the program stops — at a breakpoint, or when you pause it.' : 'The program is not running.'}</Quiet>
                : session.frames.length === 0 ? <Quiet>The debugger reported no frames.</Quiet>
                  : session.frames.map((f, i) => (
                    <button key={`${f.id}:${i}`} type="button"
                      onClick={() => { setPick({ key: pauseKey ?? '', index: i }); if (f.path) openFile(f.path, f.line ?? undefined); }}
                      className={cx('flex w-full items-baseline gap-2 rounded-md px-2 py-1 text-left text-[12.5px]',
                        i === frameIndex ? 'bg-brand/10 text-ink' : 'text-ink-2 hover:bg-surface-2', f.hint === 'subtle' && 'opacity-60')}>
                      <span className="min-w-0 flex-1 truncate font-mono text-[12px]">{f.name}</span>
                      <span className="shrink-0 font-mono text-[11px] text-dim" title={f.path ?? ''}>{f.path ? `${base(f.path)}:${f.line}` : 'no file'}</span>
                    </button>
                  ))}
            </Section>
            <Section title="Breakpoints" className="border-t border-line/60 md:flex-[2]">
              {bpList.length === 0 ? <Quiet>Click beside a line number in the editor to add one.</Quiet>
                : bpList.map((b) => {
                  const ok = verified(b.path, b.line);
                  return (
                    <button key={`${b.path}:${b.line}`} type="button" onClick={() => openFile(b.path, b.line)}
                      className="flex w-full items-center gap-2 rounded-md px-2 py-0.5 text-left text-[12.5px] text-ink-2 hover:bg-surface-2"
                      title={ok === false ? 'The debugger could not bind this line to code' : b.path}>
                      <CircleDot className={cx('size-3 shrink-0', ok === false ? 'text-dim' : 'text-danger')} />
                      <span className="min-w-0 flex-1 truncate font-mono text-[12px]">{base(b.path)}</span>
                      <span className="shrink-0 font-mono text-[11px] text-dim">{b.line}</span>
                    </button>
                  );
                })}
            </Section>
          </div>
          <div className="flex min-h-0 flex-col border-t border-line/60 md:border-t-0">
            <Section title="Variables" className="md:flex-[3]">
              {frame ? <Variables key={`${pauseKey}:${frame.id}`} sessionId={session.id} frameId={frame.id} />
                : <Quiet>{live ? 'Variables appear when the program stops.' : 'The program is not running.'}</Quiet>}
            </Section>
            <Section title="Watch" className="border-t border-line/60 md:flex-[2]">
              <Watches projectId={projectId} sessionId={live ? session.id : null} frameId={frame?.id ?? null} pauseKey={pauseKey} />
            </Section>
          </div>
          <Section title="Debug console" className="min-h-[220px] border-t border-line/60 md:border-t-0">
            <div className="px-2 font-mono text-[12px] leading-relaxed whitespace-pre-wrap [overflow-wrap:anywhere]">
              {lines.length === 0 && <span className="font-sans text-dim">What the program prints appears here.</span>}
              {lines.map((l, i) => (
                <div key={i} className={cx(
                  l.kind === 'in' && 'text-brand', l.kind === 'error' && 'text-danger', l.kind === 'result' && 'text-ink',
                  l.kind === 'out' && (l.category === 'stderr' ? 'text-danger' : l.category === 'important' ? 'text-warn' : l.category === 'console' ? 'text-dim' : 'text-ink-2'),
                )}>{l.kind === 'in' ? `› ${l.text}` : l.text}</div>
              ))}
              <div ref={consoleEnd} />
            </div>
          </Section>
        </div>
      )}
      {session && live && (
        <form className="flex shrink-0 items-center gap-2 border-t border-line/60 px-3 py-1.5" onSubmit={(e) => { e.preventDefault(); void evaluate(); }}>
          <span className="font-mono text-[12px] text-brand">›</span>
          <Input value={repl} onChange={(e) => setRepl(e.target.value)} aria-label="Evaluate an expression"
            placeholder={isPaused ? `Evaluate in ${frame?.name ?? 'the current frame'}` : 'Evaluate an expression'}
            className="h-7 flex-1 border-transparent bg-transparent font-mono text-[12px] shadow-none focus-visible:border-line" />
          <Button size="icon-xs" variant="ghost" type="submit" aria-label="Evaluate" disabled={!repl.trim()}><SendHorizontal /></Button>
        </form>
      )}
      <RunConfigDialog open={dialog} onOpenChange={setDialog} projectId={projectId} kind="debug" editing={editing}
        onSaved={(saved) => { setTarget(`config:${saved.id}`); setVersion((v) => v + 1); }} />
    </div>
  );
}

function ToolButton({ label, disabled, onClick, children }: { label: string; disabled?: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <Button size="icon-sm" variant="ghost" aria-label={label} title={label} disabled={disabled} onClick={onClick}>{children}</Button>
  );
}
