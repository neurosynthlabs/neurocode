import { useMemo, useState } from 'react';
import { useData, usePref } from '@/lib/data';
import { Webhook, ShieldAlert, Zap, Loader2, FileCog } from 'lucide-react';
import { useProject } from '@/lib/project-context';
import { useRemote } from '@/lib/remote';
import { extensions } from '@/lib/live/extensions';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/switch';
import {
  Page, PageHeader, PageBody, Panel, Tag, Dot, Mono, Toolbar, SelectField, DataTable, Row, Cell,
  Stat, StatGrid, KV, Empty, SectionTitle,
} from '@/components/os';
import { hooks } from '@/mock/commands';
import { cn } from '@/lib/utils';
import type { HookEvent } from '@/types';

const EVENTS: HookEvent[] = ['SessionStart', 'UserPromptSubmit', 'PreToolUse', 'PostToolUse', 'Stop', 'SubagentStop', 'PreCompact', 'Notification', 'PreCommit', 'PostDeploy'];
const RES_TONE = { ok: 'ok', blocked: 'warn', error: 'danger' } as const;

const HOOKS_ON: Record<string, boolean> = Object.fromEntries(hooks.map((h) => [h.id, h.enabled]));

/** With the local API, the hooks really configured on this machine; in the demo, the worked example. */
export default function Hooks() {
  const { mode } = useData();
  return mode === 'live' ? <LiveHooks /> : <SampleHooks />;
}

function SampleHooks() {
  const [ev, setEv] = useState('all');
  const [sel, setSel] = useState<string | null>('h1');
  const [on, setOn] = usePref('hooks.enabled', HOOKS_ON);

  const list = useMemo(() => (ev === 'all' ? hooks : hooks.filter((h) => h.event === ev)), [ev]);
  const h = useMemo(() => hooks.find((x) => x.id === sel), [sel]);
  const blocking = hooks.filter((x) => x.blocking).length;
  const fires = hooks.reduce((n, x) => n + x.fires24h, 0);

  const timeline = useMemo(
    () => [...hooks].filter((x) => x.fires24h > 0).sort((a, b) => b.fires24h - a.fires24h).slice(0, 20),
    [],
  );

  return (
    <Page>
      <PageHeader
        title="Hooks"
        subtitle="Deterministic automation. A hook is not a suggestion to a model — it is shell that runs, and a non-zero exit stops the tool call."
        actions={<Tag tone="warn"><ShieldAlert className="size-3" />{blocking} blocking</Tag>}
      >
        <Toolbar>
          <SelectField className="w-52" value={ev} onChange={setEv}
            options={[{ value: 'all', label: `All events (${hooks.length})` }, ...EVENTS.map((e) => ({ value: e, label: `${e} (${hooks.filter((h2) => h2.event === e).length})` }))]} />
          <span className="ml-auto text-[12.5px] text-dim">{list.length} of {hooks.length} hooks · {fires} firings in 24h</span>
        </Toolbar>
      </PageHeader>

      <PageBody className="space-y-4">
        <StatGrid cols={5}>
          <Stat label="Hooks" value={hooks.length} sub={`${hooks.filter((x) => on[x.id]).length} enabled`} icon={<Webhook className="size-3" />} />
          <Stat label="Blocking" value={blocking} tone="warn" sub="non-zero exit stops the call" />
          <Stat label="Firings 24h" value={fires} icon={<Zap className="size-3" />} />
          <Stat label="Blocked a call" value={hooks.filter((x) => x.lastResult === 'blocked').length} tone="warn" sub="caught before damage" />
          <Stat label="Errored" value={hooks.filter((x) => x.lastResult === 'error').length} tone="danger" sub="need attention" />
        </StatGrid>

        <Panel className="accent-left" eyebrow="Why hooks and not instructions" title="Rules that cannot be talked out of">
          <p className="max-w-4xl text-[13.5px] leading-relaxed text-ink-2">
            A system prompt asks a model to behave. A hook is shell code the runtime executes whether the model likes it or
            not. Anything that must never happen — a write to a TRANS_* table, a recursive delete, a commit carrying a
            secret — lives here, not in a prompt.
          </p>
        </Panel>

        <Panel flush>
          {list.length === 0 ? <Empty title="No hook on that event" /> : (
            <DataTable head={['Event', 'Matcher', 'Command', 'Scope', 'Blocking', 'Fires 24h', 'Last fired', 'Result', 'On']}>
              {list.map((x) => (
                <Row key={x.id} active={sel === x.id} onClick={() => setSel(x.id)} className={cn(!on[x.id] && 'opacity-50')}>
                  <Cell><Tag tone={x.event.startsWith('Pre') ? 'warn' : x.event.startsWith('Post') ? 'ok' : 'neutral'}>{x.event}</Tag></Cell>
                  <Cell mono className="text-ink-2">{x.matcher}</Cell>
                  <Cell mono className="text-brand">{x.command}</Cell>
                  <Cell><Tag tone="neutral">{x.scope}</Tag></Cell>
                  <Cell>{x.blocking ? <Tag tone="warn">blocking</Tag> : <span className="text-dim">—</span>}</Cell>
                  <Cell className="tnum">{x.fires24h}</Cell>
                  <Cell className="text-dim">{x.lastFired}</Cell>
                  <Cell><Tag tone={RES_TONE[x.lastResult]}>{x.lastResult}</Tag></Cell>
                  <Cell><Switch checked={on[x.id]} onCheckedChange={(v) => setOn({ ...on, [x.id]: v }, `Hook ${x.event} · ${x.matcher} ${v ? 'enabled' : 'disabled'}`)} /></Cell>
                </Row>
              ))}
            </DataTable>
          )}
        </Panel>

        <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
          {h && (
            <Panel eyebrow={`${h.event} · ${h.scope}`} title={<Mono tone="brand">{h.command}</Mono>}
              actions={h.blocking ? <Tag tone="warn">blocking</Tag> : <Tag tone="neutral">advisory</Tag>}>
              <p className="text-[13.5px] leading-relaxed text-ink-2">{h.description}</p>
              <div className="mt-3 border-t border-line pt-2.5">
                <KV k="Matcher" v={h.matcher} mono />
                <KV k="Exit 0" v="tool call proceeds; stdout is appended to the agent's context" />
                <KV k="Exit non-zero" v={h.blocking
                  ? <span className="text-warn">tool call is refused and stderr is shown to the agent</span>
                  : <span className="text-dim">logged only — the call still proceeds</span>} />
                <KV k="Fires 24h" v={h.fires24h} />
                <KV k="Last result" v={<Tag tone={RES_TONE[h.lastResult]}>{h.lastResult}</Tag>} />
              </div>
            </Panel>
          )}

          <Panel eyebrow="Last 24 hours, by volume" title="Firing timeline" flush>
            <div className="divide-y divide-line">
              {timeline.map((x) => (
                <div key={x.id} className="flex items-center gap-2.5 px-3.5 py-1.5">
                  <Dot state={x.lastResult === 'ok' ? 'ok' : x.lastResult === 'blocked' ? 'warn' : 'error'} />
                  <Tag tone="neutral">{x.event}</Tag>
                  <Mono className="min-w-0 flex-1 truncate">{x.command}</Mono>
                  <span className="shrink-0 tnum text-[12px] text-soft">{x.fires24h}×</span>
                  <span className="w-20 shrink-0 text-right text-[11.5px] text-dim">{x.lastFired}</span>
                </div>
              ))}
            </div>
          </Panel>
        </div>

        <SectionTitle>Event order in a single turn</SectionTitle>
        <Panel>
          <div className="flex flex-wrap items-center gap-1.5">
            {['SessionStart', 'UserPromptSubmit', 'PreToolUse', 'PostToolUse', 'SubagentStop', 'PreCompact', 'Stop', 'Notification'].map((e, i, a) => (
              <span key={e} className="flex items-center gap-1.5">
                <span className={cn('rounded-sm border px-2 py-1 font-mono text-[12px]',
                  e.startsWith('Pre') ? 'border-warn/30 bg-warn/8 text-warn' : e.startsWith('Post') ? 'border-ok/30 bg-ok/8 text-ok' : 'border-line bg-surface-2 text-ink-2')}>
                  {e}
                </span>
                {i < a.length - 1 && <span className="text-dim">›</span>}
              </span>
            ))}
          </div>
          <p className="mt-2.5 text-[12.5px] text-dim">
            <span className="text-warn">Pre</span> hooks can refuse. <span className="text-ok">Post</span> hooks observe
            and enrich. Both may write back into the agent's context — which is how project rules reach an agent that never
            asked for them.
          </p>
        </Panel>
      </PageBody>
    </Page>
  );
}

/* Live: the hooks Claude Code runs, read from its settings files and enabled plugins. NeuroCode shows them
   and never runs one, so there are no firings to count and none are invented; every command is redacted
   by the server before it gets here. */

const SCOPE_LABEL: Record<string, string> = {
  global: 'workspace', project: 'project', local: 'project, local', plugin: 'plugin',
};

function LiveHooks() {
  const { project } = useProject();
  const [ev, setEv] = useState('all');
  const [sel, setSel] = useState<string | null>(null);
  const r = useRemote(`hooks:${project.id}`, () => extensions.hooks(project.id));

  const all = useMemo(() => r.data?.hooks ?? [], [r.data]);
  const events = useMemo(() => Array.from(new Set(all.map((x) => x.event))), [all]);
  const list = ev === 'all' ? all : all.filter((x) => x.event === ev);
  const h = all.find((x) => x.id === sel) ?? list[0] ?? null;
  const blocking = all.filter((x) => x.blocking).length;
  const files = r.data?.files ?? [];

  return (
    <Page>
      <PageHeader
        title="Hooks"
        subtitle={`What Claude Code runs around its tool calls, read from its settings files for ${project.name} and from enabled plugins. NeuroCode does not run hooks — it shows them, with anything that looks like a secret taken out.`}
        actions={r.data && <Tag tone="warn"><ShieldAlert className="size-3" />{blocking} can block</Tag>}
      >
        <Toolbar>
          <SelectField className="w-52" value={ev} onChange={setEv}
            options={[{ value: 'all', label: `All events (${all.length})` }, ...events.map((e) => ({ value: e, label: `${e} (${all.filter((x) => x.event === e).length})` }))]} />
          <span className="ml-auto text-[12.5px] text-dim">{list.length} of {all.length} hooks · firings are not recorded here</span>
        </Toolbar>
      </PageHeader>

      {r.error ? (
        <PageBody><Empty title="The hooks did not load" hint={r.error} action={<Button size="sm" variant="outline" onClick={r.reload}>Try again</Button>} /></PageBody>
      ) : !r.data ? (
        <PageBody><Empty icon={<Loader2 className="size-5 animate-spin" />} title="Reading settings files…" /></PageBody>
      ) : (
        <PageBody className="space-y-4">
          <StatGrid cols={5}>
            <Stat label="Hooks" value={all.length} sub={`in ${files.filter((f) => f.exists).length} settings files and ${new Set(all.filter((x) => x.scope === 'plugin').map((x) => x.source)).size} plugins`} icon={<Webhook className="size-3" />} />
            <Stat label="Can block" value={blocking} tone="warn" sub="exit 2 refuses the action" />
            <Stat label="Workspace" value={all.filter((x) => x.scope === 'global').length} sub="~/.claude settings" />
            <Stat label="Project" value={all.filter((x) => x.scope === 'project' || x.scope === 'local').length} sub={`${project.name}'s .claude folder`} />
            <Stat label="Firings 24h" value="—" icon={<Zap className="size-3" />} sub="runs in Claude Code, not logged here" />
          </StatGrid>

          <Panel className="accent-left" eyebrow="Shown, never run" title="NeuroCode does not run hooks">
            <p className="max-w-4xl text-[13.5px] leading-relaxed text-ink-2">
              A hook is shell that Claude Code runs on its own events, whether the model likes it or not. NeuroCode's agents
              run nothing but a project's test command, with your approval, so no hook listed here ever fires inside NeuroCode.
              Claude Code keeps no log this app can read, so how often each one fires is not shown rather than guessed.
            </p>
          </Panel>

          {all.length === 0 ? (
            <Panel>
              <Empty icon={<Webhook className="size-6" />} title="No hooks configured"
                hint={`None in ${files.map((f) => `${f.path}${f.exists ? '' : ' (missing)'}`).join(', ')}, or in an enabled plugin.`} />
            </Panel>
          ) : (
            <>
              <Panel flush>
                {list.length === 0 ? <Empty title="No hook on that event" /> : (
                  <DataTable head={['Event', 'Matcher', 'Command', 'Scope', 'Can block', 'Fires 24h', 'Last fired']}>
                    {list.map((x) => (
                      <Row key={x.id} active={h?.id === x.id} onClick={() => setSel(x.id)}>
                        <Cell><Tag tone={x.event.startsWith('Pre') ? 'warn' : x.event.startsWith('Post') ? 'ok' : 'neutral'}>{x.event}</Tag></Cell>
                        <Cell mono className="text-ink-2">{x.matcher || '*'}</Cell>
                        <Cell mono className="max-w-[420px] truncate text-brand">{x.command}</Cell>
                        <Cell><Tag tone="neutral">{SCOPE_LABEL[x.scope] ?? x.scope}</Tag></Cell>
                        <Cell>{x.blocking ? <Tag tone="warn">can block</Tag> : <span className="text-dim">—</span>}</Cell>
                        <Cell className="text-dim">—</Cell>
                        <Cell className="text-dim">runs in Claude Code</Cell>
                      </Row>
                    ))}
                  </DataTable>
                )}
              </Panel>

              <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
                {h && (
                  <Panel eyebrow={`${h.event} · ${SCOPE_LABEL[h.scope] ?? h.scope}`} title={<Mono tone="brand" className="break-all">{h.command}</Mono>}
                    actions={h.blocking ? <Tag tone="warn">can block</Tag> : <Tag tone="neutral">advisory</Tag>}>
                    <p className="text-[13.5px] leading-relaxed text-ink-2">{h.description}</p>
                    <div className="mt-3 border-t border-line pt-2.5">
                      <KV k="Matcher" v={h.matcher || '* (everything)'} mono />
                      <KV k="Type" v={h.type} />
                      <KV k="Source" v={h.source} mono />
                      <KV k="Timeout" v={h.timeoutS ? `${h.timeoutS} s` : <span className="text-dim">Claude Code's default</span>} />
                      <KV k="Exit 0" v="Claude Code carries on" />
                      <KV k="Exit 2" v={h.blocking
                        ? <span className="text-warn">Claude Code refuses the action and shows stderr to the model</span>
                        : <span className="text-dim">stderr is shown; the action still happens</span>} />
                      <KV k="Fires" v={<span className="text-dim">not recorded — runs in Claude Code</span>} />
                    </div>
                  </Panel>
                )}

                <Panel eyebrow="Last 24 hours" title="Firing timeline">
                  <Empty icon={<Zap className="size-5" />} title="NeuroCode does not run hooks"
                    hint="Claude Code runs them and keeps no log this app can read, so there is no timeline to draw." />
                </Panel>
              </div>
            </>
          )}

          <SectionTitle>Files read</SectionTitle>
          <Panel flush>
            <div className="divide-y divide-line/60">
              {files.map((f) => (
                <div key={`${f.scope}:${f.path}`} className="flex items-center gap-2.5 px-5 py-2.5">
                  <FileCog className="size-3.5 shrink-0 text-dim" />
                  <Mono className="min-w-0 truncate">{f.path}</Mono>
                  <Tag tone="neutral">{SCOPE_LABEL[f.scope] ?? f.scope}</Tag>
                  <span className={cn('ml-auto shrink-0 text-[12px]', f.exists ? 'text-ok' : 'text-dim')}>{f.exists ? 'read' : 'not there'}</span>
                </div>
              ))}
            </div>
            <p className="border-t border-line/60 px-5 py-2.5 text-[12.5px] text-dim">
              Only the hooks in each file are read. Everything else in them — environment, permissions, helpers — never leaves the server.
            </p>
          </Panel>
        </PageBody>
      )}
    </Page>
  );
}
