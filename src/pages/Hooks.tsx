import { useMemo, useState } from 'react';
import { Webhook, ShieldAlert, Loader2, FileCog, Play, ShieldCheck, Copy } from 'lucide-react';
import { toast } from 'sonner';
import { useAuth } from '@/lib/auth';
import { useProject } from '@/lib/project-context';
import { useRemote } from '@/lib/remote';
import { extensionsAdmin, HOOKS_PERMISSION, type GovernedHook, type HookFiring } from '@/lib/live/work';
import { Button } from '@/components/ui/button';
import {
  Page, PageHeader, PageBody, Panel, Tag, Mono, Toolbar, SelectField, DataTable, Row, Cell,
  Stat, StatGrid, KV, Empty, SectionTitle, More,
} from '@/components/os';
import { cn } from '@/lib/utils';

/** The hooks really configured on this machine, which of them a rule allows to run, and what one did. */
export default function Hooks() {
  return <LiveHooks />;
}

/* The hooks Claude Code runs, read from its settings files and enabled plugins. A repository's hook is
   somebody else's shell script, so none of them runs here because it is there: a hook runs only where a
   person wrote a tool rule of kind `hook` allowing that one, matched on `event/command`. This screen's
   job is to say which is which in so many words — every row says "runs" or "shown only", and the panel
   above the table says how to change that. Every command is redacted by the server before it gets here.
   With no project yet, only the workspace's settings and plugins are read. */

const SCOPE_LABEL: Record<string, string> = {
  global: 'workspace', project: 'project', local: 'project, local', plugin: 'plugin',
};

/** What a person would write in Governance → Permissions to allow exactly this hook and nothing else. */
const patternFor = (h: GovernedHook) => h.pattern;

function LiveHooks() {
  const { can } = useAuth();
  const { project } = useProject();
  const pid = project?.id ?? null;
  const [ev, setEv] = useState('all');
  const [show, setShow] = useState<'all' | 'allowed' | 'shown'>('all');
  const [sel, setSel] = useState<string | null>(null);
  const [firing, setFiring] = useState<string | null>(null);
  const [fired, setFired] = useState<HookFiring | null>(null);
  const r = useRemote(`hooks:${pid ?? ''}`, () => extensionsAdmin.hooks(pid));

  const all = useMemo(() => r.data?.hooks ?? [], [r.data]);
  const events = useMemo(() => Array.from(new Set(all.map((x) => x.event))), [all]);
  const list = all
    .filter((x) => ev === 'all' || x.event === ev)
    .filter((x) => show === 'all' || (show === 'allowed' ? x.allowed : !x.allowed));
  const h = all.find((x) => x.id === sel) ?? list[0] ?? null;
  const blocking = all.filter((x) => x.blocking).length;
  const allowed = all.filter((x) => x.allowed).length;
  const files = r.data?.files ?? [];
  const canRun = r.data?.canRun ?? false;

  const runNow = async (hook: GovernedHook) => {
    setFiring(hook.id);
    setFired(null);
    try {
      const out = await extensionsAdmin.runHook(hook.id, pid);
      setFired(out);
      toast(out.ran ? `${hook.event} hook exited ${out.exitCode}` : 'It did not run', {
        description: out.ran ? `${out.ms} ms. Its output is below.` : out.why,
      });
    } catch (e) {
      toast('The hook did not run', { description: e instanceof Error ? e.message : String(e) });
    } finally {
      setFiring(null);
    }
  };

  const copyPattern = async (hook: GovernedHook) => {
    try {
      await navigator.clipboard.writeText(patternFor(hook));
      toast('Pattern copied', { description: 'Paste it into a `hook` rule in Governance → Permissions.' });
    } catch {
      toast(patternFor(hook), { description: 'Use this as the pattern of a `hook` rule.' });
    }
  };

  return (
    <Page>
      <PageHeader
        title="Hooks"
        subtitle="What Claude Code runs around tool calls, and which may run here."
        about={<>
          <p>Read from Claude Code's settings files{project ? ` for ${project.name}` : ' in the workspace'} and enabled plugins.</p>
          <p>A hook is someone's shell script, so each is refused by default. A <Mono>hook</Mono> tool rule in Permissions allows one, matched on <Mono>event/command</Mono>.</p>
          <p>An allowed hook fires in the project's checkout, with a timeout. Every firing is in the activity feed.</p>
        </>}
        actions={r.data && (
          <div className="flex items-center gap-1.5">
            <Tag tone={allowed ? 'ok' : 'neutral'}><ShieldCheck className="size-3" />{allowed} allowed to run</Tag>
            <Tag tone="warn"><ShieldAlert className="size-3" />{blocking} can block</Tag>
          </div>
        )}
      >
        <Toolbar>
          <SelectField className="w-52" value={ev} onChange={setEv}
            options={[{ value: 'all', label: `All events (${all.length})` }, ...events.map((e) => ({ value: e, label: `${e} (${all.filter((x) => x.event === e).length})` }))]} />
          <SelectField className="w-48" value={show} onChange={(v) => setShow(v as typeof show)}
            options={[
              { value: 'all', label: 'Allowed and shown' },
              { value: 'allowed', label: `A rule allows (${allowed})` },
              { value: 'shown', label: `Shown only (${all.length - allowed})` },
            ]} />
          <span className="ml-auto text-[12.5px] text-dim">{list.length} of {all.length} hooks</span>
        </Toolbar>
      </PageHeader>

      {r.error ? (
        <PageBody><Empty title="The hooks did not load" hint={r.error} action={<Button size="sm" variant="outline" onClick={r.reload}>Try again</Button>} /></PageBody>
      ) : !r.data ? (
        <PageBody><Empty icon={<Loader2 className="size-5 animate-spin" />} title="Reading settings files…" /></PageBody>
      ) : (
        <PageBody className="space-y-4">
          {all.length > 0 && <StatGrid cols={4}>
            <Stat label="Hooks" value={all.length} sub={`in ${files.filter((f) => f.exists).length} settings files and ${new Set(all.filter((x) => x.scope === 'plugin').map((x) => x.source)).size} plugins`} icon={<Webhook className="size-3" />} />
            <Stat label="A rule allows" value={allowed} tone={allowed ? 'ok' : undefined} sub={allowed ? 'these run at their event' : 'none runs without a rule'} />
            <Stat label="Shown only" value={all.length - allowed} sub="found, never run" />
            <Stat label="Can block" value={blocking} tone="warn" sub="exit 2 refuses the action, where a rule allows it" />
          </StatGrid>}

          {all.length > 0 && !canRun && (
            <p className="text-[12.5px] text-warn">
              Nothing can run here now: {project ? 'no checkout on this machine' : 'no project is open'}, or machine access is off. Allowed hooks are still listed.
            </p>
          )}

          {all.length === 0 ? (
            <Panel>
              <Empty icon={<Webhook className="size-6" />} title="No hooks configured"
                hint="None in the settings files below, or in an enabled plugin." />
            </Panel>
          ) : (
            <>
              <Panel flush>
                {list.length === 0 ? <Empty title="No hook matches those filters" /> : (
                  <DataTable head={['Event', 'Matcher', 'Command', 'Scope', 'Here']}>
                    {list.map((x) => (
                      <Row key={x.id} active={h?.id === x.id} onClick={() => { setSel(x.id); setFired(null); }}>
                        <Cell><Tag tone={x.event.startsWith('Pre') ? 'warn' : x.event.startsWith('Post') ? 'ok' : 'neutral'}>{x.event}</Tag></Cell>
                        <Cell mono className="text-ink-2">{x.matcher || '*'}</Cell>
                        <Cell mono className="max-w-[380px] truncate text-brand">{x.command}</Cell>
                        <Cell><Tag tone="neutral">{SCOPE_LABEL[x.scope] ?? x.scope}</Tag></Cell>
                        <Cell>{x.allowed
                          ? <Tag tone="ok">runs{x.blocking ? ' · can block' : ''}</Tag>
                          : <span className="text-[12.5px] text-dim">shown only</span>}</Cell>
                      </Row>
                    ))}
                  </DataTable>
                )}
              </Panel>

              {h && (
                <Panel eyebrow={`${h.event} · ${SCOPE_LABEL[h.scope] ?? h.scope}`} title={<Mono tone="brand" className="break-all">{h.command}</Mono>}
                  actions={
                    <div className="flex items-center gap-1.5">
                      {h.allowed ? <Tag tone="ok">a rule allows it</Tag> : <Tag tone="neutral">shown only</Tag>}
                      {h.blocking && <Tag tone="warn">can block</Tag>}
                    </div>
                  }>
                  <p className="text-[13.5px] leading-relaxed text-ink-2">{h.description}</p>
                  <div className="mt-3 border-t border-line pt-2.5">
                    <KV k="Here" v={<span className={h.allowed ? 'text-ok' : 'text-dim'}>{h.why}</span>} />
                    <KV k="A rule matches" v={patternFor(h)} mono />
                    <KV k="Matcher" v={h.matcher || '* (everything)'} mono />
                    <KV k="Type" v={h.type} />
                    <KV k="Source" v={h.source} mono />
                    <KV k="Timeout" v={h.timeoutS ? `${h.timeoutS} s, capped at 60 s` : 'up to 60 s'} />
                    <KV k="Exit 0" v="the event carries on" />
                    <KV k="Exit 2" v={h.blocking
                      ? <span className="text-warn">refuses the action; its output is the reason</span>
                      : <span className="text-dim">its output is kept; the action still happens</span>} />
                  </div>
                  <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-line pt-3">
                    <Button size="sm" variant="outline" onClick={() => void copyPattern(h)}>
                      <Copy className="size-3.5" />Copy pattern
                    </Button>
                    <Button size="sm" disabled={!h.allowed || !canRun || !can(HOOKS_PERMISSION) || firing === h.id}
                      onClick={() => void runNow(h)}>
                      {firing === h.id ? <Loader2 className="size-3.5 animate-spin" /> : <Play className="size-3.5" />}
                      Run once
                    </Button>
                    <span className="text-[12px] text-dim">
                      {!h.allowed ? 'Needs a hook rule that allows it.'
                        : !canRun ? 'Nowhere to run it on this machine now.'
                          : !can(HOOKS_PERMISSION) ? `Running one needs ${HOOKS_PERMISSION}.`
                            : 'Same rule, same limits; the firing is logged.'}
                    </span>
                  </div>
                  {fired && fired.hookId === h.id && (
                    <div className="mt-3 border-t border-line pt-3">
                      <SectionTitle>{fired.ran ? `It exited ${fired.exitCode} in ${fired.ms} ms` : 'It did not run'}</SectionTitle>
                      <pre className="mt-1.5 max-h-72 overflow-auto rounded-xs border border-line bg-surface-2 p-3 text-[12px] leading-relaxed whitespace-pre-wrap text-ink-2">
                        {fired.ran ? (fired.output || '(it printed nothing)') : fired.why}
                      </pre>
                      {fired.blocked && <p className="mt-1.5 text-[12.5px] text-warn">On its own event, this would have refused the action.</p>}
                    </div>
                  )}
                </Panel>
              )}
            </>
          )}

          <More label="Files read">
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
              Only hooks are read. Everything else in these files stays on the server.
            </p>
          </Panel>
          </More>
        </PageBody>
      )}
    </Page>
  );
}
