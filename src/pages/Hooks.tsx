import { useMemo, useState } from 'react';
import { Webhook, ShieldAlert, Zap } from 'lucide-react';
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

export default function Hooks() {
  const [ev, setEv] = useState('all');
  const [sel, setSel] = useState<string | null>('h1');
  const [on, setOn] = useState<Record<string, boolean>>(() => Object.fromEntries(hooks.map((h) => [h.id, h.enabled])));

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
          <span className="ml-auto text-[11.5px] text-dim">{list.length} of {hooks.length} hooks · {fires} firings in 24h</span>
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
          <p className="max-w-4xl text-[12.5px] leading-relaxed text-ink-2">
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
                  <Cell><Switch checked={on[x.id]} onCheckedChange={(v) => setOn((m) => ({ ...m, [x.id]: v }))} /></Cell>
                </Row>
              ))}
            </DataTable>
          )}
        </Panel>

        <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
          {h && (
            <Panel eyebrow={`${h.event} · ${h.scope}`} title={<Mono tone="brand">{h.command}</Mono>}
              actions={h.blocking ? <Tag tone="warn">blocking</Tag> : <Tag tone="neutral">advisory</Tag>}>
              <p className="text-[12.5px] leading-relaxed text-ink-2">{h.description}</p>
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
                  <span className="shrink-0 tnum text-[11px] text-soft">{x.fires24h}×</span>
                  <span className="w-20 shrink-0 text-right text-[10.5px] text-dim">{x.lastFired}</span>
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
                <span className={cn('rounded-sm border px-2 py-1 font-mono text-[11px]',
                  e.startsWith('Pre') ? 'border-warn/30 bg-warn/8 text-warn' : e.startsWith('Post') ? 'border-ok/30 bg-ok/8 text-ok' : 'border-line bg-surface-2 text-ink-2')}>
                  {e}
                </span>
                {i < a.length - 1 && <span className="text-dim">›</span>}
              </span>
            ))}
          </div>
          <p className="mt-2.5 text-[11.5px] text-dim">
            <span className="text-warn">Pre</span> hooks can refuse. <span className="text-ok">Post</span> hooks observe
            and enrich. Both may write back into the agent's context — which is how project rules reach an agent that never
            asked for them.
          </p>
        </Panel>
      </PageBody>
    </Page>
  );
}
