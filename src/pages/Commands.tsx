import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { toast } from 'sonner';
import { usePref } from '@/lib/data';
import { SquareSlash, CornerDownLeft, Loader2, TriangleAlert } from 'lucide-react';
import { ApiError, api } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { useProject } from '@/lib/project-context';
import { useRemote } from '@/lib/remote';
import { extensions, type LiveCommand } from '@/lib/live/extensions';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/switch';
import {
  Page, PageHeader, PageBody, Panel, Tag, Mono, ListRow, Stat, StatGrid, KV, Empty, SectionTitle,
} from '@/components/os';
import { cn } from '@/lib/utils';
import { ago } from './code/format';

/** The command files really on this machine, and what this project's sessions ran. */
export default function Commands() {
  return <LiveCommands />;
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

function LiveCommands() {
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
        actions={r.data && <Tag tone="ok">{all.filter((x) => isOn(x.id)).length} of {all.length} enabled</Tag>}
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
