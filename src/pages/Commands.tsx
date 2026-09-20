import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { toast } from 'sonner';
import { usePref } from '@/lib/data';
import {
  SquareSlash, CornerDownLeft, Loader2, TriangleAlert, Wrench, Plus, Trash2, ShieldCheck, ShieldOff,
} from 'lucide-react';
import { ApiError, api } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { useProject } from '@/lib/project-context';
import { useRemote } from '@/lib/remote';
import { extensions, type LiveCommand } from '@/lib/live/extensions';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/switch';
import {
  Page, PageHeader, PageBody, Panel, Tag, Mono, ListRow, Stat, StatGrid, KV, Empty, SectionTitle,
  Segmented, Field, SelectField, Toolbar,
} from '@/components/os';
import {
  work, extensionsAdmin, EXTENSIONS_PERMISSION, type CustomTool, type RuleTrial,
} from '@/lib/live/work';
import { cn } from '@/lib/utils';
import { ago } from './code/format';

/** The command files really on this machine, and the tools the people here defined. */
export default function Commands() {
  // Two things a person writes for their sessions: a slash command, which is a prompt they type, and a
  // custom tool, which is something an agent may call. They live on one screen because they are the same
  // question asked twice — what does this workspace add to what a session can do?
  const [tab, setTab] = useState<'commands' | 'tools'>('commands');
  const nav = (
    <Segmented
      options={[{ id: 'commands', label: 'Slash commands' }, { id: 'tools', label: 'Custom tools' }]}
      value={tab} onChange={setTab}
    />
  );
  return tab === 'commands' ? <LiveCommands nav={nav} /> : <CustomTools nav={nav} />;
}

/* Live: the slash commands a NeuroCode session on this project understands — ~/.claude/commands, the
   project's .claude/commands and those of plugins enabled in Claude Code. Typing one here opens a session
   and sends it; the session turns it into the prompt below. */

const NONE_OFF: Record<string, boolean> = {};
const COMMAND = /^\/([\w:.-]+)(?:\s+([\s\S]*))?$/;
const SHELL = /!`([^`\n]+)`/g;

/** The body as a session receives it — the same order and rules as services/extensions.py::expand:
 *  shell lines replaced by a note, $1…$9 by the matching word (or nothing), then $ARGUMENTS whole. */
function expand(body: string, args: string): string {
  const words = args.trim() ? args.trim().split(/\s+/) : [];
  return body
    .replace(SHELL, (_m, line: string) => `[not run by NeuroCode: ${line}]`)
    .replace(/\$([1-9])/g, (_m, n: string) => (words[Number(n) - 1] ? `«${words[Number(n) - 1]}»` : ''))
    .replace(/\$ARGUMENTS/g, `«${args}»`);
}

/** Which file `/name` means, by the same rule the server uses: plugin:cmd names a plugin's; else project, workspace, plugin. */
function resolve(list: LiveCommand[], name: string): LiveCommand | undefined {
  const rank = { project: 0, global: 1, plugin: 2 } as const;
  if (name.includes(':')) return list.find((c) => c.name === `/${name}`);
  return list.filter((c) => c.name === `/${name}`).sort((a, b) => rank[a.scope] - rank[b.scope])[0]
    ?? list.filter((c) => c.scope === 'plugin' && c.name.endsWith(`:${name}`))[0];
}

function LiveCommands({ nav: tabs }: { nav: ReactNode }) {
  const nav = useNavigate();
  const { can } = useAuth();
  const { project } = useProject();
  const pid = project?.id ?? null;
  const [q, setQ] = useState('/');
  const [picked, setPicked] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [on, setOn] = usePref('commands.enabled', NONE_OFF);
  const r = useRemote(`commands:${pid ?? ''}`, () => extensions.commands(pid));
  const isOn = (id: string) => on[id] !== false;

  const all = useMemo(() => r.data?.commands ?? [], [r.data]);
  const list = useMemo(() => {
    const t = q.replace(/^\//, '').split(/\s/)[0].trim().toLowerCase();
    return t ? all.filter((c) => (c.name + c.description + c.agent).toLowerCase().includes(t)) : all;
  }, [q, all]);
  const c = list.find((x) => x.id === picked) ?? list[0] ?? null;

  const typed = COMMAND.exec(q.trim());
  const target = typed && typed[1] ? resolve(all, typed[1]) : undefined;
  const runs = all.reduce((n, x) => n + x.runs, 0);
  const busiest = [...all].sort((a, b) => b.runs - a.runs)[0];

  const send = async () => {
    if (!target || sending || !project || !can('sessions:chat')) return;
    if (!isOn(target.id)) { toast.error(`${target.name} is switched off`, { description: 'Switch it on to use it in a session.' }); return; }
    const text = q.trim();
    setSending(true);
    try {
      const session = await api.newSession(project.id, text.slice(0, 80));
      await api.askSession(session.ref, text);
      nav(`/sessions?ref=${encodeURIComponent(session.ref)}`);
    } catch (e) {
      toast.error('Not sent', { description: e instanceof ApiError ? e.message : 'The local API did not answer.' });
    } finally {
      setSending(false);
    }
  };

  const args = c ? c.args.replace(/[<>[\]…]/g, '').trim() : '';
  const example = c ? `${c.name} ${args}`.trim() : '';
  const shell = c ? r.data?.shellLines[c.id] ?? [] : [];

  return (
    <Page>
      <PageHeader
        title="Commands"
        subtitle={project
          ? `Slash commands read from this machine: the workspace's, ${project.name}'s and those of plugins enabled in Claude Code. Type one and press Enter to send it to a new session on ${project.name}.`
          : 'Slash commands read from this machine: the workspace’s and those of plugins enabled in Claude Code. Onboard a project to add its own, and to send one to a session.'}
        actions={<div className="flex items-center gap-2">{r.data && <Tag tone="ok">{all.filter((x) => isOn(x.id)).length} of {all.length} enabled</Tag>}{tabs}</div>}
      >
        <div className="pb-3">
          <div className="focus-brand flex h-9 items-center gap-2 rounded-md border border-line bg-base px-3 transition-colors">
            <SquareSlash className="size-4 shrink-0 text-brand" />
            <input
              value={q} aria-label="Command"
              onChange={(e) => setQ(e.target.value.startsWith('/') ? e.target.value : '/' + e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') void send(); }}
              disabled={sending}
              className="min-w-0 flex-1 bg-transparent font-mono text-[14px] text-ink placeholder:text-dim focus-visible:outline-none"
            />
            <span className="shrink-0 text-[12px] text-dim">
              {sending ? 'Sending…' : target
                ? (!project ? 'Onboard a project first: a command is sent to a session on one' : can('sessions:chat') ? `Enter sends ${target.name}` : 'Your role cannot use sessions')
                : `${list.length} match${list.length === 1 ? '' : 'es'}`}
            </span>
            {sending ? <Loader2 className="size-3.5 shrink-0 animate-spin text-dim" /> : <CornerDownLeft className="size-3.5 shrink-0 text-dim" />}
          </div>
        </div>
      </PageHeader>

      {r.error ? (
        <PageBody><Empty title="The commands did not load" hint={r.error} action={<Button size="sm" variant="outline" onClick={r.reload}>Try again</Button>} /></PageBody>
      ) : !r.data ? (
        <PageBody><Empty icon={<Loader2 className="size-5 animate-spin" />} title="Reading commands off disk…" /></PageBody>
      ) : all.length === 0 ? (
        <PageBody>
          <Empty icon={<SquareSlash className="size-6" />} title="No commands on this machine"
            hint={`Nothing was found under ${r.data.roots.map((x) => `${x.path}${x.exists ? '' : ' (missing)'}`).join(', ')}. A command is a markdown file in a commands folder.`} />
        </PageBody>
      ) : (
        <PageBody className="space-y-4">
          <StatGrid cols={4}>
            <Stat label="Commands" value={all.length} sub={`${all.filter((x) => x.scope === 'project').length} project-scoped`} />
            <Stat label="Runs" value={runs.toLocaleString()} sub={project ? `in sessions on ${project.name}` : 'sessions belong to a project, and there is none yet'} />
            <Stat label="Busiest" value={busiest && busiest.runs > 0 ? busiest.name : '—'} tone="brand"
              sub={busiest && busiest.runs > 0 ? `${busiest.runs} runs` : 'no command has run here yet'} />
            <Stat label="Model-pinned" value={all.filter((x) => x.model).length} sub="named in the file; the router still picks" />
          </StatGrid>

          {r.data.conflicts.length > 0 && (
            <Panel className="border-warn/35" eyebrow="The same name in more than one place" title={<span className="flex items-center gap-1.5"><TriangleAlert className="size-3.5 text-warn" />Conflicts</span>} flush>
              <div className="divide-y divide-line">
                {r.data.conflicts.map((x) => (
                  <div key={x.command} className="px-3.5 py-2.5">
                    <div className="flex flex-wrap items-center gap-2">
                      <Mono tone="brand">{x.command}</Mono><Tag tone="neutral">{x.a}</Tag><span className="text-[12px] text-dim">over</span><Tag tone="neutral">{x.b}</Tag>
                    </div>
                    <p className="mt-1 text-[12.5px] text-soft">{x.resolution}</p>
                  </div>
                ))}
              </div>
            </Panel>
          )}

          <div className="flex min-h-[520px] flex-col gap-3 md:flex-row">
            <div className="no-scrollbar w-full shrink-0 max-h-[42vh] md:max-h-none md:w-[320px] overflow-y-auto rounded-md border border-line bg-surface">
              {list.length === 0 ? <Empty title="No command matches" hint="Clear the bar to see them all." /> : list.map((x) => (
                <ListRow key={x.id} active={x.id === c?.id} onClick={() => setPicked(x.id)}>
                  <div className="flex items-center gap-2">
                    <span className={cn('size-1.5 shrink-0 rounded-full', isOn(x.id) ? 'bg-ok' : 'bg-dim')} />
                    <Mono tone={x.id === c?.id ? 'brand' : 'neutral'} className="truncate">{x.name}</Mono>
                    <span className="ml-auto shrink-0 tnum text-[11.5px] text-dim">{x.runs}</span>
                  </div>
                  <p className="mt-1 line-clamp-2 text-[12px] text-dim">{x.description}</p>
                </ListRow>
              ))}
            </div>

            {c ? (
              <div className="min-w-0 flex-1 space-y-3">
                <Panel
                  eyebrow={`${c.scope} scope · ${c.lastRun ? `last run ${ago(c.lastRun)}` : 'never run here'}`}
                  title={<span className="flex items-center gap-2"><Mono tone="brand">{c.name}</Mono><span className="font-mono text-[12.5px] text-dim">{c.args}</span></span>}
                  actions={<Switch checked={isOn(c.id)} disabled={!can('settings:write')} aria-label={`Use ${c.name} in NeuroCode sessions`}
                    onCheckedChange={(v) => setOn({ ...on, [c.id]: v }, `Command ${c.name} ${v ? 'enabled' : 'disabled'} in NeuroCode sessions`)} />}
                >
                  <p className="text-[13.5px] leading-relaxed text-ink-2">{c.description || <span className="text-dim">No description in its front matter.</span>}</p>
                  <div className="mt-3 grid grid-cols-1 sm:grid-cols-2 gap-x-6 border-t border-line pt-2.5 xl:grid-cols-4">
                    <KV k="Dispatches to" v={c.agent === 'Session' ? (project ? `a session on ${project.name}` : 'a session on a project') : c.agent} />
                    <KV k="Model" v={c.model ? <span className="flex items-center gap-1.5"><Mono>{c.model}</Mono><span className="text-[12px] text-dim">not used here</span></span> : <span className="text-dim">router decides</span>} />
                    <KV k="Runs" v={c.runs.toLocaleString()} />
                    <KV k="Source" v={c.source} mono />
                  </div>
                </Panel>

                {shell.length > 0 && (
                  <Panel className="border-warn/35" eyebrow="Claude Code runs these when it expands the command" title={<span className="flex items-center gap-1.5"><TriangleAlert className="size-3.5 text-warn" />Shell lines</span>}>
                    <div className="flex flex-wrap gap-1">{shell.map((line) => <Mono key={line}>{line}</Mono>)}</div>
                    <p className="mt-2 text-[12.5px] text-soft">NeuroCode never runs them. In a session each one is replaced by a note saying so.</p>
                  </Panel>
                )}

                <Panel eyebrow={c.truncated ? 'The prompt body — cut off here at 32 KB' : 'The prompt body — $ARGUMENTS is filled in verbatim'} title="Definition">
                  <pre className="ascii max-h-[360px] overflow-auto rounded-sm border border-line bg-base p-3.5 whitespace-pre-wrap">{c.body}</pre>
                </Panel>

                <Panel eyebrow="What a session receives" title="Resolved example">
                  <div className="rounded-sm border border-line bg-base p-3">
                    <div className="mb-2 flex items-center gap-2 border-b border-line pb-2">
                      <SquareSlash className="size-3.5 text-brand" />
                      <span className="font-mono text-[13px] text-ink">{example}</span>
                    </div>
                    <pre className="ascii whitespace-pre-wrap">
                      {expand(c.body, args)}
                    </pre>
                  </div>
                  <SectionTitle className="mt-3 mb-1.5">Then</SectionTitle>
                  <p className="text-[12.5px] text-soft">
                    The resolved prompt is written into the session as its own turn, so you see exactly what the model was given,
                    and the router picks the lane{c.model ? <> — <Mono>{c.model}</Mono> is named in the file, and NeuroCode does not use it</> : ''}.
                  </p>
                </Panel>
              </div>
            ) : <div className="min-w-0 flex-1"><Empty title="Nothing selected" hint="Clear the bar to pick a command." /></div>}
          </div>
        </PageBody>
      )}
    </Page>
  );
}


/* Custom tools: what the people here defined for their agents to call. A definition is not a permission —
   a tool runs only where a `tool` rule allows its name, and with no rule it does not run at all, which is
   what the verdict line under each one says. The definition itself is shown whole, because the person
   reading this screen is the one who has to be able to check what it would do. */

const EXAMPLE: Record<'command' | 'http', string> = {
  command: JSON.stringify({
    argv: ['./scripts/preview.sh', '{branch}'],
    cwd: '',
    timeoutS: 60,
    arguments: {
      type: 'object',
      properties: { branch: { type: 'string', description: 'the branch to build', pattern: '^[\\w./-]+$' } },
      required: ['branch'],
    },
  }, null, 2),
  http: JSON.stringify({
    method: 'GET',
    url: 'https://status.example.com/api/services/{service}',
    headers: { Accept: 'application/json' },
    timeoutS: 15,
    arguments: {
      type: 'object',
      properties: { service: { type: 'string', enum: ['web', 'api', 'worker'] } },
      required: ['service'],
    },
  }, null, 2),
};

function CustomTools({ nav: tabs }: { nav: ReactNode }) {
  const { can } = useAuth();
  const { project } = useProject();
  const pid = project?.id ?? null;
  const [picked, setPicked] = useState<string | null>(null);
  const [asked, setAsked] = useState<RuleTrial | null>(null);
  const [drafting, setDrafting] = useState(false);
  const [busy, setBusy] = useState(false);
  const [name, setName] = useState('');
  const [what, setWhat] = useState('');
  const [kind, setKind] = useState<'command' | 'http'>('command');
  const [spec, setSpec] = useState(EXAMPLE.command);
  const [version, setVersion] = useState(0);
  const r = useRemote(`custom-tools:${pid ?? ''}:${version}`, () => extensionsAdmin.tools(pid));

  const all = useMemo(() => r.data ?? [], [r.data]);
  const t = all.find((x) => x.id === picked) ?? all[0] ?? null;
  const mayWrite = can(EXTENSIONS_PERMISSION);

  // What the rules say about the selected tool, asked of the server rather than worked out here: the
  // weighing is the runtime's, and a screen that re-implemented it would drift from it. It is asked
  // again whenever the selection or the project changes, and an answer that arrives after the selection
  // moved on is dropped — otherwise the panel would show one tool's verdict under another's name.
  const chosen = t?.name ?? '';
  useEffect(() => {
    if (!chosen) return;
    let current = true;
    work.tryToolRule('tool', chosen, pid)
      .then((answer) => { if (current) setAsked(answer); })
      .catch(() => undefined);
    return () => { current = false; };
  }, [chosen, pid]);
  // Derived rather than stored, so the panel never shows one tool's verdict under another's name while
  // an answer is still in flight.
  const verdict = asked && asked.subject === chosen && asked.projectId === pid ? asked : null;

  const define = async () => {
    setBusy(true);
    try {
      const parsed = JSON.parse(spec) as Record<string, unknown>;
      const made = await extensionsAdmin.defineTool({
        name: name.trim().toLowerCase(), description: what.trim(), kind, spec: parsed, projectId: pid,
      });
      toast(`${made.name} defined`, { description: 'It is refused at every call until a `tool` rule allows it.' });
      setDrafting(false); setName(''); setWhat('');
      setVersion((n) => n + 1);
      setPicked(made.id);
    } catch (e) {
      toast('It was not defined', {
        description: e instanceof SyntaxError ? 'The definition is not valid JSON.'
          : e instanceof Error ? e.message : String(e),
      });
    } finally {
      setBusy(false);
    }
  };

  const setEnabled = async (tool: CustomTool, on: boolean) => {
    try {
      await extensionsAdmin.changeTool(tool.id, { enabled: on });
      setVersion((n) => n + 1);
      toast(`${tool.name} ${on ? 'offered to sessions' : 'no longer offered'}`);
    } catch (e) {
      toast('It did not change', { description: e instanceof Error ? e.message : String(e) });
    }
  };

  const remove = async (tool: CustomTool) => {
    if (!window.confirm(`Delete ${tool.name}? Any rule written for its name stops matching anything.`)) return;
    try {
      await extensionsAdmin.removeTool(tool.id);
      setPicked(null);
      setVersion((n) => n + 1);
      toast(`${tool.name} deleted`);
    } catch (e) {
      toast('It was not deleted', { description: e instanceof Error ? e.message : String(e) });
    }
  };

  const schema = (t?.spec?.arguments ?? {}) as { properties?: Record<string, { type?: string; description?: string }>; required?: string[] };
  const takes = Object.entries(schema.properties ?? {});

  return (
    <Page>
      <PageHeader
        title="Custom tools"
        subtitle={project
          ? `Tools the people here defined for ${project.name}: a command on this machine, or an HTTP call, with a schema for its arguments. A session may call one — and only where a tool rule allows its name.`
          : 'Tools defined for the whole workspace: a command on this machine, or an HTTP call, with a schema for its arguments. Open a project to see and define its own as well.'}
        actions={<div className="flex items-center gap-2">
          {r.data && <Tag tone="neutral">{all.filter((x) => x.enabled).length} of {all.length} offered</Tag>}
          {tabs}
        </div>}
      >
        <Toolbar>
          <Button size="sm" disabled={!mayWrite} onClick={() => { setDrafting(!drafting); setSpec(EXAMPLE[kind]); }}>
            <Plus className="size-3.5" />{drafting ? 'Cancel' : 'Define a tool'}
          </Button>
          <span className="ml-auto text-[12.5px] text-dim">
            {mayWrite ? 'A definition says what could happen; a rule says whether it may.' : `Defining one needs ${EXTENSIONS_PERMISSION}.`}
          </span>
        </Toolbar>
      </PageHeader>

      {r.error ? (
        <PageBody><Empty title="The tools did not load" hint={r.error} action={<Button size="sm" variant="outline" onClick={r.reload}>Try again</Button>} /></PageBody>
      ) : !r.data ? (
        <PageBody><Empty icon={<Loader2 className="size-5 animate-spin" />} title="Reading the tools defined here…" /></PageBody>
      ) : (
        <PageBody className="space-y-4">
          <StatGrid cols={4}>
            <Stat label="Defined" value={all.length} icon={<Wrench className="size-3" />} sub={pid ? "the workspace's and this project's" : "the workspace's own"} />
            <Stat label="Offered to sessions" value={all.filter((x) => x.enabled).length} tone="brand" sub="switched on; a rule still decides each call" />
            <Stat label="Commands" value={all.filter((x) => x.kind === 'command').length} sub="run in the checkout, inside the machine's roots" />
            <Stat label="HTTP calls" value={all.filter((x) => x.kind === 'http').length} sub="public addresses only, no redirects" />
          </StatGrid>

          {drafting && (
            <Panel className="accent-left" eyebrow="It is refused at every call until a rule allows its name" title="Define a tool">
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                <Field label="Name" value={name} onChange={setName} mono placeholder="deploy_preview"
                  hint="What a model types to call it: lowercase letters, digits and underscores." />
                <SelectField label="Kind" value={kind} className="sm:col-span-1"
                  onChange={(v) => { setKind(v as 'command' | 'http'); setSpec(EXAMPLE[v as 'command' | 'http']); }}
                  options={[{ value: 'command', label: 'A command on this machine' }, { value: 'http', label: 'An HTTP call' }]} />
                <Field label="What it does" value={what} onChange={setWhat} className="sm:col-span-1"
                  placeholder="Builds a preview of one branch" hint="The model sees this line and nothing else." />
              </div>
              <SectionTitle className="mt-3 mb-1.5">Definition</SectionTitle>
              <textarea value={spec} onChange={(e) => setSpec(e.target.value)} spellCheck={false} rows={14}
                aria-label="Definition"
                className="focus-brand block w-full rounded-lg border border-line bg-base p-3 font-mono text-[12.5px] leading-relaxed text-ink focus-visible:outline-none" />
              <p className="mt-2 text-[12.5px] text-soft">
                A command is <Mono>argv</Mono> — the program and each argument on its own — never a command
                line, because a command line would need a shell. <Mono>{'{name}'}</Mono> anywhere in it is
                filled from the arguments after they have been checked against <Mono>arguments</Mono>,
                and a value fills exactly one entry: it is never split and never read as more.
              </p>
              <div className="mt-3 flex flex-wrap gap-2 border-t border-line pt-3">
                <Button size="sm" disabled={!name.trim() || !spec.trim() || busy || !mayWrite} onClick={() => void define()}>
                  {busy ? <Loader2 className="size-3.5 animate-spin" /> : <Plus className="size-3.5" />}Define it
                </Button>
                <Button size="sm" variant="outline" onClick={() => setDrafting(false)}>Cancel</Button>
              </div>
            </Panel>
          )}

          {all.length === 0 ? (
            <Panel>
              <Empty icon={<Wrench className="size-6" />} title="No tool is defined here"
                hint="Define one, then allow it with a `tool` rule in Governance → Permissions. Until both are done, a session has nothing to call." />
            </Panel>
          ) : (
            <div className="flex min-h-[420px] flex-col gap-3 md:flex-row">
              <div className="no-scrollbar w-full shrink-0 max-h-[42vh] overflow-y-auto rounded-md border border-line bg-surface md:max-h-none md:w-[300px]">
                {all.map((x) => (
                  <ListRow key={x.id} active={x.id === t?.id} onClick={() => setPicked(x.id)}>
                    <div className="flex items-center gap-2">
                      <span className={cn('size-1.5 shrink-0 rounded-full', x.enabled ? 'bg-ok' : 'bg-dim')} />
                      <Mono tone={x.id === t?.id ? 'brand' : 'neutral'} className="truncate">{x.name}</Mono>
                      <Tag tone="neutral">{x.kind}</Tag>
                    </div>
                    <p className="mt-1 line-clamp-2 text-[12px] text-dim">{x.description || 'No description.'}</p>
                  </ListRow>
                ))}
              </div>

              {t && (
                <div className="min-w-0 flex-1 space-y-3">
                  <Panel
                    eyebrow={`${t.projectId ? `${t.projectName ?? t.projectId} only` : 'the whole workspace'} · ${t.kind === 'command' ? 'a command on this machine' : 'an HTTP call'}`}
                    title={<Mono tone="brand">{t.name}</Mono>}
                    actions={<div className="flex items-center gap-2">
                      <Switch checked={t.enabled} disabled={!mayWrite} aria-label={`Offer ${t.name} to sessions`}
                        onCheckedChange={(v) => void setEnabled(t, v)} />
                      <Button size="xs" variant="outline" disabled={!mayWrite} onClick={() => void remove(t)}>
                        <Trash2 className="size-3" />Delete
                      </Button>
                    </div>}
                  >
                    <p className="text-[13.5px] leading-relaxed text-ink-2">{t.description || <span className="text-dim">No description, so the model is told nothing about what it is for.</span>}</p>
                    <div className="mt-3 border-t border-line pt-2.5">
                      <KV k="A rule decides" v={verdict
                        ? <span className={cn('flex items-center gap-1.5', verdict.ruleId === null ? 'text-dim' : verdict.action === 'allow' ? 'text-ok' : verdict.action === 'deny' ? 'text-danger' : 'text-warn')}>
                            {verdict.ruleId !== null && verdict.action === 'allow' ? <ShieldCheck className="size-3.5" /> : <ShieldOff className="size-3.5" />}
                            {verdict.why}
                          </span>
                        : <span className="text-dim">asking the rules…</span>} wrap />
                      <KV k="Offered to sessions" v={t.enabled ? 'yes, while a rule allows it' : 'no — switched off here'} />
                      <KV k="Defined by" v={t.createdBy ?? 'somebody whose account is gone'} />
                    </div>
                  </Panel>

                  <Panel eyebrow="Checked before anything runs" title="Arguments">
                    {takes.length === 0 ? (
                      <p className="text-[13px] text-dim">It takes no arguments, and a call that sends any is refused.</p>
                    ) : (
                      <div className="divide-y divide-line/60">
                        {takes.map(([arg, rule]) => (
                          <div key={arg} className="flex flex-wrap items-baseline gap-2 py-2">
                            <Mono tone="brand">{arg}</Mono>
                            <Tag tone="neutral">{rule?.type ?? 'any'}</Tag>
                            {(schema.required ?? []).includes(arg) ? <Tag tone="warn">required</Tag> : <span className="text-[12px] text-dim">optional</span>}
                            <span className="min-w-0 flex-1 text-[12.5px] text-soft">{rule?.description ?? ''}</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </Panel>

                  <Panel eyebrow="Exactly what the server stored, and exactly what runs" title="Definition">
                    <pre className="ascii max-h-[360px] overflow-auto rounded-sm border border-line bg-base p-3.5 whitespace-pre-wrap">{JSON.stringify(t.spec, null, 2)}</pre>
                  </Panel>
                </div>
              )}
            </div>
          )}
        </PageBody>
      )}
    </Page>
  );
}
