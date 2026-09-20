import { useEffect, useMemo, useState } from 'react';
import { Check, Loader2, Pencil, Play, Plus, RotateCcw, Square, Trash2, WandSparkles, X } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Empty, Tag, cx } from '@/components/os';
import { ApiError } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import {
  runConfigs, terminals, type Detected, type RunConfig, type RunConfigKind, type RunLanguage, type RunSuggestion,
  type TerminalDoc, type TerminalMessage,
} from '@/lib/live/terminal';
import { TerminalView } from '@/components/workbench/TerminalPanel';

/* The Run tab: a project's run configurations — a command in a folder of its checkout, with its own
   environment — started into a terminal whose output streams here. "Detect" reads the checkout and
   suggests configurations; nothing it finds runs until a person saves it and presses Run. */

const reason = (e: unknown) => (e instanceof ApiError ? e.message : 'The local API did not answer.');
const LANGS: Record<RunConfigKind, RunLanguage[]> = { run: ['shell', 'node', 'python'], debug: ['python', 'node'] };
const LANG_WORD: Record<RunLanguage, string> = { shell: 'Shell', node: 'Node', python: 'Python' };

/* ── saving a configuration ──────────────────────────────────────── */

interface EnvRow { key: number; name: string; value: string; kept: boolean; removed: boolean }

/**
 * Add or change a run (or debug) configuration. Environment values are never sent back by the server, so
 * a variable it already holds shows its name with an empty value: leave it empty to keep it, type to
 * replace it, or remove it.
 */
export function RunConfigDialog({ open, onOpenChange, projectId, kind, editing, onSaved }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectId: string;
  kind: RunConfigKind;
  editing: RunConfig | null;
  onSaved: (config: RunConfig) => void;
}) {
  const [name, setName] = useState('');
  const [language, setLanguage] = useState<RunLanguage>(LANGS[kind][0]);
  const [command, setCommand] = useState('');
  const [args, setArgs] = useState('');
  const [cwd, setCwd] = useState('');
  const [env, setEnv] = useState<EnvRow[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [seeded, setSeeded] = useState<string | null>(null);

  // Filled once per opening, from the configuration being changed or blank. Done during render, keyed on
  // what is being edited, so reopening for another configuration starts from that one.
  const seedKey = open ? `${kind}:${editing?.id ?? 'new'}` : null;
  if (seedKey !== seeded) {
    setSeeded(seedKey);
    if (seedKey) {
      setName(editing?.name ?? '');
      setLanguage(editing?.language ?? LANGS[kind][0]);
      setCommand(editing?.command ?? '');
      setArgs((editing?.args ?? []).join('\n'));
      setCwd(editing?.cwd ?? '');
      setEnv((editing?.env ?? []).map((n, i) => ({ key: i, name: n, value: '', kept: true, removed: false })));
      setError(null);
    }
  }

  const save = async () => {
    setSaving(true);
    setError(null);
    const argList = args.split('\n').map((a) => a.trim()).filter(Boolean);
    try {
      let saved: RunConfig;
      if (editing) {
        const patch: Record<string, string | null> = {};
        for (const row of env) {
          const n = row.name.trim();
          if (!n) continue;
          if (row.kept && row.removed) patch[n] = null;
          else if (!row.kept || row.value !== '') patch[n] = row.value;
        }
        saved = await runConfigs.change(projectId, editing.id, { name: name.trim(), language, command: command.trim(), args: argList, cwd: cwd.trim(), env: patch });
      } else {
        const values: Record<string, string> = {};
        for (const row of env) if (row.name.trim()) values[row.name.trim()] = row.value;
        saved = await runConfigs.add(projectId, { name: name.trim(), kind, language, command: command.trim(), args: argList, cwd: cwd.trim(), env: values });
      }
      onSaved(saved);
      onOpenChange(false);
    } catch (e) {
      setError(reason(e));
    } finally {
      setSaving(false);
    }
  };

  const label = kind === 'debug' ? 'Program' : 'Command';
  const hint = kind === 'debug'
    ? language === 'python' ? 'A .py file of the checkout, relative to the folder below — or -m and a module name.' : 'A .js, .mjs or .cjs file of the checkout, relative to the folder below.'
    : 'Run by your shell in the folder below, exactly as typed.';
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>{editing ? `Change ${editing.name}` : kind === 'debug' ? 'New debug configuration' : 'New run configuration'}</DialogTitle>
          <DialogDescription>
            {kind === 'debug' ? 'What the debugger launches, with the project’s own interpreter.' : 'A command a person starts from the Run tab. Nothing runs until you press Run.'}
          </DialogDescription>
        </DialogHeader>
        <div className="grid max-h-[60vh] gap-3.5 overflow-y-auto pr-1">
          <div className="grid gap-3.5 sm:grid-cols-[1fr_140px]">
            <label className="grid gap-1.5 text-[13px] text-ink-2">Name
              <Input value={name} onChange={(e) => setName(e.target.value)} placeholder={kind === 'debug' ? 'Debug the API' : 'Dev server'} />
            </label>
            <label className="grid gap-1.5 text-[13px] text-ink-2">Language
              <select value={language} onChange={(e) => setLanguage(e.target.value as RunLanguage)}
                className="h-8 rounded-lg border border-line-strong bg-surface-2 px-2 text-[13px] text-ink-2">
                {LANGS[kind].map((l) => <option key={l} value={l} className="bg-surface">{LANG_WORD[l]}</option>)}
              </select>
            </label>
          </div>
          <label className="grid gap-1.5 text-[13px] text-ink-2">{label}
            <Input value={command} onChange={(e) => setCommand(e.target.value)} className="font-mono text-[12.5px]"
              placeholder={kind === 'debug' ? (language === 'python' ? 'app/main.py' : 'server.js') : 'npm run dev'} />
            <span className="text-[12px] text-dim">{hint}</span>
          </label>
          <label className="grid gap-1.5 text-[13px] text-ink-2">Arguments
            <Textarea value={args} onChange={(e) => setArgs(e.target.value)} rows={2} className="font-mono text-[12.5px]" placeholder="One per line" />
          </label>
          <label className="grid gap-1.5 text-[13px] text-ink-2">Folder
            <Input value={cwd} onChange={(e) => setCwd(e.target.value)} className="font-mono text-[12.5px]" placeholder="The checkout's root" />
            <span className="text-[12px] text-dim">Relative to the project’s checkout, such as <span className="font-mono">services/api</span>. A further source’s folders start with its label, such as <span className="font-mono">api/src</span>.</span>
          </label>
          <div className="grid gap-1.5">
            <div className="flex items-center justify-between text-[13px] text-ink-2">Environment
              <Button size="xs" variant="ghost" onClick={() => setEnv((rows) => [...rows, { key: Date.now(), name: '', value: '', kept: false, removed: false }])}>
                <Plus />Add variable
              </Button>
            </div>
            {env.length === 0 && <p className="text-[12.5px] text-dim">None beyond what the API itself runs with.</p>}
            {env.map((row) => (
              <div key={row.key} className={cx('grid grid-cols-[minmax(0,0.8fr)_minmax(0,1fr)_auto] items-center gap-2', row.removed && 'opacity-50')}>
                <Input value={row.name} disabled={row.kept} aria-label="Variable name" placeholder="NAME" className="font-mono text-[12.5px]"
                  onChange={(e) => setEnv((rows) => rows.map((r) => (r.key === row.key ? { ...r, name: e.target.value } : r)))} />
                <Input value={row.value} type="password" autoComplete="off" aria-label={`Value of ${row.name || 'the variable'}`} disabled={row.removed}
                  placeholder={row.kept ? 'Unchanged' : 'value'} className="font-mono text-[12.5px]"
                  onChange={(e) => setEnv((rows) => rows.map((r) => (r.key === row.key ? { ...r, value: e.target.value } : r)))} />
                <Button size="icon-xs" variant="ghost" aria-label={row.removed ? 'Keep it' : 'Remove it'} title={row.removed ? 'Keep it' : 'Remove it'}
                  onClick={() => setEnv((rows) => (row.kept ? rows.map((r) => (r.key === row.key ? { ...r, removed: !r.removed } : r)) : rows.filter((r) => r.key !== row.key)))}>
                  {row.removed ? <RotateCcw /> : <X />}
                </Button>
              </div>
            ))}
            <p className="text-[12px] text-dim">Values are kept on the server and never shown again — only their names.</p>
          </div>
          {error && <p className="rounded-lg bg-danger/10 px-3 py-2 text-[13px] text-danger">{error}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button disabled={saving || !name.trim() || !command.trim()} onClick={() => void save()}>
            {saving && <Loader2 className="animate-spin" />}{editing ? 'Save changes' : 'Save'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/* ── what the checkout suggests ──────────────────────────────────── */

function DetectDialog({ open, onOpenChange, projectId, onSaved }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectId: string;
  onSaved: () => void;
}) {
  const [found, setFound] = useState<{ key: string; data: Detected | null; error: string | null } | null>(null);
  const [saving, setSaving] = useState<string | null>(null);
  const key = open ? projectId : null;

  useEffect(() => {
    if (!key) return;
    let current = true;
    runConfigs.detect(key).then(
      (data) => { if (current) setFound({ key, data, error: null }); },
      (e: unknown) => { if (current) setFound({ key, data: null, error: reason(e) }); },
    );
    return () => { current = false; };
  }, [key]);

  const fresh = found && found.key === key ? found : null;
  const save = async (s: RunSuggestion) => {
    const id = `${s.kind}:${s.command}:${s.cwd}`;
    setSaving(id);
    try {
      await runConfigs.add(projectId, { name: s.name, kind: s.kind, language: s.language, command: s.command, args: s.args, cwd: s.cwd, env: {} });
      setFound((now) => now && now.data ? { ...now, data: { ...now.data, suggestions: now.data.suggestions.map((x) => (x === s ? { ...x, saved: true } : x)) } } : now);
      onSaved();
    } catch (e) {
      toast.error(`${s.name} was not saved`, { description: reason(e) });
    } finally {
      setSaving(null);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Suggested configurations</DialogTitle>
          <DialogDescription>Read from the checkout’s files — package.json scripts, Makefile targets, pyproject scripts, manage.py, go.mod, Cargo.toml, notebooks. Nothing here has run; save the ones you want.</DialogDescription>
        </DialogHeader>
        <div className="max-h-[60vh] overflow-y-auto">
          {!fresh ? (
            <Empty icon={<Loader2 className="size-5 animate-spin" />} title="Reading the checkout…" />
          ) : fresh.error ? (
            <Empty title="The checkout could not be read" hint={fresh.error} />
          ) : fresh.data && fresh.data.suggestions.length === 0 ? (
            <Empty icon={<WandSparkles className="size-6" />} title="Nothing to suggest"
              hint="No package.json, Makefile, pyproject.toml, manage.py, go.mod, Cargo.toml or notebook was found at the root or one folder down. Add a configuration by hand instead." />
          ) : fresh.data && (
            <div className="divide-y divide-line/60">
              {fresh.data.suggestions.map((s) => {
                const id = `${s.kind}:${s.command}:${s.cwd}`;
                return (
                  <div key={id} className="flex items-start gap-3 py-2.5">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-1.5 text-[13.5px] text-ink">
                        <span className="font-medium">{s.name}</span>
                        <Tag tone={s.kind === 'debug' ? 'violet' : 'neutral'}>{s.kind === 'debug' ? 'Debug' : LANG_WORD[s.language]}</Tag>
                      </div>
                      <div className="mt-0.5 font-mono text-[12px] break-all text-ink-2">{s.command}{s.args.length ? ` ${s.args.join(' ')}` : ''}</div>
                      <div className="mt-0.5 text-[12px] text-dim">{s.why}{s.cwd ? ` · in ${s.cwd}` : ''}</div>
                    </div>
                    {s.saved ? (
                      <span className="flex shrink-0 items-center gap-1 pt-1 text-[12.5px] text-ok"><Check className="size-3.5" />Saved</span>
                    ) : (
                      <Button size="xs" variant="outline" disabled={saving === id} onClick={() => void save(s)}>
                        {saving === id ? <Loader2 className="animate-spin" /> : <Plus />}Save
                      </Button>
                    )}
                  </div>
                );
              })}
            </div>
          )}
          {fresh?.data?.capped && <p className="pt-2 text-[12px] text-dim">The list stopped at its ceiling; more may be found in deeper folders.</p>}
        </div>
        <DialogFooter><Button variant="outline" onClick={() => onOpenChange(false)}>Done</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/* ── the Run tab ─────────────────────────────────────────────────── */

const exitTone = (t: TerminalDoc) => (t.status === 'running' ? 'ok' : t.exitCode === 0 ? 'neutral' : 'danger');
const exitWord = (t: TerminalDoc) => (t.status === 'running' ? 'Running' : t.exitCode === null ? 'Stopped' : `Exited ${t.exitCode}`);

/** The Run tab of the Workbench's bottom panel, for the project the Workbench shows. */
export function RunPanel({ projectId }: { projectId: string | null }) {
  const { machine } = useAuth();
  const allowed = machine;
  const [configs, setConfigs] = useState<RunConfig[] | null>(null);
  const [runs, setRuns] = useState<TerminalDoc[]>([]);
  const [error, setError] = useState<{ status: number; message: string } | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [version, setVersion] = useState(0);
  const [editing, setEditing] = useState<RunConfig | null>(null);
  const [dialog, setDialog] = useState(false);
  const [detecting, setDetecting] = useState(false);
  const [busy, setBusy] = useState<number | null>(null);
  const [confirming, setConfirming] = useState<number | null>(null);
  const reload = () => setVersion((v) => v + 1);

  useEffect(() => {
    if (!allowed || !projectId) return;
    let current = true;
    Promise.all([runConfigs.list(projectId, 'run'), terminals.list()]).then(
      ([list, held]) => {
        if (!current) return;
        setConfigs(list);
        setRuns(held.filter((t) => t.kind === 'run' && t.projectId === projectId));
        setError(null);
        setSelected((now) => (now !== null && list.some((c) => c.id === now) ? now : list[0]?.id ?? null));
      },
      (e: unknown) => { if (current) setError({ status: e instanceof ApiError ? e.status : 0, message: reason(e) }); },
    );
    return () => { current = false; };
  }, [allowed, projectId, version]);

  /** The newest terminal each configuration ran in, which is the one its output is read from. */
  const latest = useMemo(() => {
    const by = new Map<number, TerminalDoc>();
    for (const t of runs) if (t.runConfigId !== null) by.set(t.runConfigId, t);
    return by;
  }, [runs]);

  const put = (doc: TerminalDoc) => setRuns((now) => [...now.filter((t) => t.id !== doc.id), doc]);

  const start = async (config: RunConfig) => {
    setBusy(config.id);
    setSelected(config.id);
    try {
      put(await runConfigs.start(config.id, { cols: 120, rows: 30 }));
    } catch (e) {
      toast.error(`${config.name} did not start`, { description: reason(e) });
    } finally {
      setBusy(null);
    }
  };
  const act = async (config: RunConfig, what: 'stop' | 'restart') => {
    const running = latest.get(config.id);
    if (!running) return;
    setBusy(config.id);
    try {
      put(await (what === 'stop' ? terminals.stop(running.id) : terminals.restart(running.id)));
    } catch (e) {
      toast.error(what === 'stop' ? `${config.name} did not stop` : `${config.name} did not restart`, { description: reason(e) });
    } finally {
      setBusy(null);
    }
  };
  const remove = async (config: RunConfig) => {
    if (confirming !== config.id) {
      setConfirming(config.id);
      return;
    }
    setConfirming(null);
    try {
      await runConfigs.remove(config.projectId, config.id);
      toast(`${config.name} removed`);
      reload();
    } catch (e) {
      toast.error(`${config.name} was not removed`, { description: reason(e) });
    }
  };
  const onMessage = (message: TerminalMessage) => {
    if (message.type === 'hello' || message.type === 'state') put(message.terminal);
  };

  if (!allowed) {
    return <Empty icon={<Play className="size-6" />} title="Running needs the machine:access permission"
      hint="A run is a command on the machine the API runs on, so only an Owner holds it unless an Owner grants it." />;
  }
  if (!projectId) {
    return <Empty icon={<Play className="size-6" />} title="Run configurations belong to a project"
      hint="Choose a project in the top bar, or onboard this folder as a project, to save and run its commands." />;
  }
  if (error && !configs) {
    return <Empty title={error.status === 404 && error.message.startsWith('Machine access') ? 'Machine access is off on this server' : 'The run configurations did not load'}
      hint={error.message} action={<Button size="sm" variant="outline" onClick={reload}>Try again</Button>} />;
  }
  if (!configs) return <Empty icon={<Loader2 className="size-5 animate-spin" />} title="Reading the run configurations…" />;

  const chosen = configs.find((c) => c.id === selected) ?? null;
  const output = chosen ? latest.get(chosen.id) ?? null : null;
  return (
    <div className="flex h-full min-h-0 flex-col md:flex-row">
      <div className="flex max-h-[45%] min-h-0 shrink-0 flex-col border-b border-line/60 md:max-h-none md:w-80 md:border-r md:border-b-0">
        <div className="flex shrink-0 items-center gap-1 px-3 py-1.5">
          <span className="flex-1 text-[12.5px] font-medium text-ink-2">Configurations</span>
          <Button size="xs" variant="ghost" onClick={() => setDetecting(true)}><WandSparkles />Detect</Button>
          <Button size="xs" variant="ghost" onClick={() => { setEditing(null); setDialog(true); }}><Plus />New</Button>
        </div>
        {configs.length === 0 ? (
          <Empty icon={<Play className="size-5" />} title="No run configuration yet"
            hint="Detect reads the checkout for scripts and targets to suggest, or add a command by hand."
            action={<Button size="sm" variant="outline" onClick={() => setDetecting(true)}><WandSparkles className="size-3.5" />Detect</Button>} />
        ) : (
          <div className="min-h-0 flex-1 divide-y divide-line/60 overflow-y-auto">
            {configs.map((c) => {
              const t = latest.get(c.id);
              const running = t?.status === 'running';
              return (
                <div key={c.id} onClick={() => setSelected(c.id)}
                  className={cx('group cursor-pointer px-3 py-2.5 transition-colors', c.id === selected ? 'bg-surface-2' : 'hover:bg-surface-2/60')}>
                  <div className="flex items-center gap-2">
                    <span className="min-w-0 flex-1 truncate text-[13.5px] font-medium text-ink">{c.name}</span>
                    {t && <Tag tone={exitTone(t)}>{exitWord(t)}</Tag>}
                  </div>
                  <div className="mt-0.5 truncate font-mono text-[11.5px] text-soft" title={c.command}>{c.command}</div>
                  <div className="mt-1.5 flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
                    {running ? (
                      <>
                        <Button size="xs" variant="outline" disabled={busy === c.id} onClick={() => void act(c, 'stop')}><Square />Stop</Button>
                        <Button size="xs" variant="ghost" disabled={busy === c.id} onClick={() => void act(c, 'restart')}><RotateCcw />Restart</Button>
                      </>
                    ) : (
                      <Button size="xs" variant="outline" disabled={busy === c.id} onClick={() => void start(c)}>
                        {busy === c.id ? <Loader2 className="animate-spin" /> : <Play />}Run
                      </Button>
                    )}
                    <span className="flex-1" />
                    <Button size="icon-xs" variant="ghost" aria-label={`Change ${c.name}`} title="Change" onClick={() => { setEditing(c); setDialog(true); }}><Pencil /></Button>
                    <Button size="xs" variant={confirming === c.id ? 'destructive' : 'ghost'} aria-label={`Remove ${c.name}`} title="Remove"
                      onClick={() => void remove(c)} onBlur={() => setConfirming((now) => (now === c.id ? null : now))}>
                      <Trash2 />{confirming === c.id ? 'Remove?' : ''}
                    </Button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        {chosen && output ? (
          <>
            <div className="flex shrink-0 flex-wrap items-center gap-x-2 gap-y-0.5 border-b border-line/60 px-3 py-1.5 text-[12px] text-soft">
              <span className="font-medium text-ink-2">{chosen.name}</span>
              <span className="min-w-0 flex-1 truncate font-mono text-[11.5px]" title={output.command}>{output.command}</span>
              <span className="font-mono text-[11.5px] text-dim" title={output.cwd}>{output.cwd.split('/').slice(-2).join('/')}</span>
            </div>
            <TerminalView key={output.id} terminalId={output.id} onMessage={onMessage} />
          </>
        ) : (
          <Empty icon={<Play className="size-6" />} title={chosen ? `${chosen.name} has not run yet` : 'Nothing running'}
            hint={chosen ? `Run starts ${chosen.command} in ${chosen.cwd || "the checkout's root"} and streams what it prints here.` : 'Choose a configuration and press Run; its output appears here.'} />
        )}
      </div>
      <RunConfigDialog open={dialog} onOpenChange={setDialog} projectId={projectId} kind="run" editing={editing}
        onSaved={(saved) => { setSelected(saved.id); reload(); }} />
      <DetectDialog open={detecting} onOpenChange={setDetecting} projectId={projectId} onSaved={reload} />
    </div>
  );
}
