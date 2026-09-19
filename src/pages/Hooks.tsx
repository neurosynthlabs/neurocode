import { useMemo, useState } from 'react';
import { Webhook, ShieldAlert, Loader2, FileCog } from 'lucide-react';
import { useProject } from '@/lib/project-context';
import { useRemote } from '@/lib/remote';
import { extensions } from '@/lib/live/extensions';
import { Button } from '@/components/ui/button';
import {
  Page, PageHeader, PageBody, Panel, Tag, Mono, Toolbar, SelectField, DataTable, Row, Cell,
  Stat, StatGrid, KV, Empty, SectionTitle,
} from '@/components/os';
import { cn } from '@/lib/utils';

/** The hooks really configured on this machine, read from Claude Code's settings files and enabled plugins. */
export default function Hooks() {
  return <LiveHooks />;
}

/* The hooks Claude Code runs, read from its settings files and enabled plugins. NeuroCode shows them and
   never runs one, so there are no firings to count and none are shown; every command is redacted by the
   server before it gets here. With no project yet, only the workspace's settings and plugins are read. */

const SCOPE_LABEL: Record<string, string> = {
  global: 'workspace', project: 'project', local: 'project, local', plugin: 'plugin',
};

function LiveHooks() {
  const { project } = useProject();
  const pid = project?.id ?? null;
  const [ev, setEv] = useState('all');
  const [sel, setSel] = useState<string | null>(null);
  const r = useRemote(`hooks:${pid ?? ''}`, () => extensions.hooks(pid));

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
        subtitle={`What Claude Code runs around its tool calls, read from its settings files${project ? ` for ${project.name}` : ' in the workspace'} and from enabled plugins. NeuroCode does not run hooks — it shows them, with anything that looks like a secret taken out.`}
        actions={r.data && <Tag tone="warn"><ShieldAlert className="size-3" />{blocking} can block</Tag>}
      >
        <Toolbar>
          <SelectField className="w-52" value={ev} onChange={setEv}
            options={[{ value: 'all', label: `All events (${all.length})` }, ...events.map((e) => ({ value: e, label: `${e} (${all.filter((x) => x.event === e).length})` }))]} />
          <span className="ml-auto text-[12.5px] text-dim">{list.length} of {all.length} hooks</span>
        </Toolbar>
      </PageHeader>

      {r.error ? (
        <PageBody><Empty title="The hooks did not load" hint={r.error} action={<Button size="sm" variant="outline" onClick={r.reload}>Try again</Button>} /></PageBody>
      ) : !r.data ? (
        <PageBody><Empty icon={<Loader2 className="size-5 animate-spin" />} title="Reading settings files…" /></PageBody>
      ) : (
        <PageBody className="space-y-4">
          <StatGrid cols={4}>
            <Stat label="Hooks" value={all.length} sub={`in ${files.filter((f) => f.exists).length} settings files and ${new Set(all.filter((x) => x.scope === 'plugin').map((x) => x.source)).size} plugins`} icon={<Webhook className="size-3" />} />
            <Stat label="Can block" value={blocking} tone="warn" sub="exit 2 refuses the action" />
            <Stat label="Workspace" value={all.filter((x) => x.scope === 'global').length} sub="~/.claude settings" />
            <Stat label="Project" value={all.filter((x) => x.scope === 'project' || x.scope === 'local').length} sub={project ? `${project.name}'s .claude folder` : 'onboard a project to read its .claude folder'} />
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
                  <DataTable head={['Event', 'Matcher', 'Command', 'Scope', 'Can block']}>
                    {list.map((x) => (
                      <Row key={x.id} active={h?.id === x.id} onClick={() => setSel(x.id)}>
                        <Cell><Tag tone={x.event.startsWith('Pre') ? 'warn' : x.event.startsWith('Post') ? 'ok' : 'neutral'}>{x.event}</Tag></Cell>
                        <Cell mono className="text-ink-2">{x.matcher || '*'}</Cell>
                        <Cell mono className="max-w-[420px] truncate text-brand">{x.command}</Cell>
                        <Cell><Tag tone="neutral">{SCOPE_LABEL[x.scope] ?? x.scope}</Tag></Cell>
                        <Cell>{x.blocking ? <Tag tone="warn">can block</Tag> : <span className="text-[12.5px] text-dim">advisory</span>}</Cell>
                      </Row>
                    ))}
                  </DataTable>
                )}
              </Panel>

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
                  </div>
                </Panel>
              )}
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
