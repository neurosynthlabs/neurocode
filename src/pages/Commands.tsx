import { useMemo, useState } from 'react';
import { SquareSlash, CornerDownLeft } from 'lucide-react';
import { Switch } from '@/components/ui/switch';
import {
  Page, PageHeader, PageBody, Panel, Tag, Mono, ListRow, Stat, StatGrid, KV, Empty, SectionTitle,
} from '@/components/os';
import { commands } from '@/mock/commands';
import { cn } from '@/lib/utils';

export default function Commands() {
  const [q, setQ] = useState('/');
  const [sel, setSel] = useState(commands[0].id);
  const [on, setOn] = useState<Record<string, boolean>>(() => Object.fromEntries(commands.map((c) => [c.id, c.enabled])));

  const list = useMemo(() => {
    const t = q.replace(/^\//, '').trim().toLowerCase();
    return t ? commands.filter((c) => (c.name + c.description + c.agent).toLowerCase().includes(t)) : commands;
  }, [q]);

  const c = useMemo(() => list.find((x) => x.id === sel) ?? list[0] ?? commands[0], [list, sel]);
  const example = `${c.name} ${c.args.replace(/[<>[\]]/g, '').replace('…', '').trim() || ''}`.trim();

  return (
    <Page>
      <PageHeader
        title="Commands"
        subtitle="Slash commands are the operator's keyboard. Each one is a prompt with a fixed shape, a chosen agent and — where it matters — a pinned model."
        actions={<Tag tone="ok">{commands.filter((x) => on[x.id]).length} of {commands.length} enabled</Tag>}
      >
        {/* Live command bar */}
        <div className="pb-3">
          <div className="focus-brand flex h-9 items-center gap-2 rounded-md border border-line bg-base px-3 transition-colors">
            <SquareSlash className="size-4 shrink-0 text-brand" />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value.startsWith('/') ? e.target.value : '/' + e.target.value)}
              placeholder="/plan invoice mein tax galat aa raha hai…"
              className="min-w-0 flex-1 bg-transparent font-mono text-[13px] text-ink placeholder:text-dim focus-visible:outline-none"
            />
            <span className="shrink-0 text-[11px] text-dim">{list.length} match{list.length === 1 ? '' : 'es'}</span>
            <CornerDownLeft className="size-3.5 shrink-0 text-dim" />
          </div>
        </div>
      </PageHeader>

      <PageBody className="space-y-4">
        <StatGrid cols={4}>
          <Stat label="Commands" value={commands.length} sub={`${commands.filter((x) => x.scope === 'project').length} project-scoped`} />
          <Stat label="Runs" value={commands.reduce((n, x) => n + x.runs, 0).toLocaleString()} sub="all time" />
          <Stat label="Busiest" value={[...commands].sort((a, b) => b.runs - a.runs)[0].name} tone="brand" sub={`${[...commands].sort((a, b) => b.runs - a.runs)[0].runs} runs`} />
          <Stat label="Model-pinned" value={commands.filter((x) => x.model).length} sub="the rest go through the router" />
        </StatGrid>

        <div className="flex min-h-[520px] flex-col gap-3 md:flex-row">
          <div className="no-scrollbar w-full shrink-0 max-h-[42vh] md:max-h-none md:w-[320px] overflow-y-auto rounded-md border border-line bg-surface">
            {list.length === 0 ? <Empty title="No command matches" hint="Try /plan, /fix, /impact or /ship." /> : list.map((x) => (
              <ListRow key={x.id} active={x.id === c.id} onClick={() => setSel(x.id)}>
                <div className="flex items-center gap-2">
                  <span className={cn('size-1.5 shrink-0 rounded-full', on[x.id] ? 'bg-ok' : 'bg-dim')} />
                  <Mono tone={x.id === c.id ? 'brand' : 'neutral'}>{x.name}</Mono>
                  <span className="ml-auto shrink-0 tnum text-[10.5px] text-dim">{x.runs}</span>
                </div>
                <p className="mt-1 line-clamp-2 text-[11px] text-dim">{x.description}</p>
              </ListRow>
            ))}
          </div>

          <div className="min-w-0 flex-1 space-y-3">
            <Panel
              eyebrow={`${c.scope} scope · last run ${c.lastRun}`}
              title={<span className="flex items-center gap-2"><Mono tone="brand">{c.name}</Mono><span className="font-mono text-[11.5px] text-dim">{c.args}</span></span>}
              actions={<Switch checked={on[c.id]} onCheckedChange={(v) => setOn((m) => ({ ...m, [c.id]: v }))} />}
            >
              <p className="text-[12.5px] leading-relaxed text-ink-2">{c.description}</p>
              <div className="mt-3 grid grid-cols-1 sm:grid-cols-2 gap-x-6 border-t border-line pt-2.5 xl:grid-cols-4">
                <KV k="Dispatches to" v={c.agent} />
                <KV k="Model" v={c.model ? <Mono tone="brand">{c.model}</Mono> : <span className="text-dim">router decides</span>} />
                <KV k="Runs" v={c.runs.toLocaleString()} />
                <KV k="Scope" v={c.scope} />
              </div>
            </Panel>

            <Panel eyebrow="The prompt body — $ARGUMENTS is substituted verbatim" title="Definition">
              <pre className="ascii rounded-sm border border-line bg-base p-3.5 whitespace-pre-wrap">{c.body}</pre>
            </Panel>

            <Panel eyebrow="What actually gets sent" title="Resolved example">
              <div className="rounded-sm border border-line bg-base p-3">
                <div className="mb-2 flex items-center gap-2 border-b border-line pb-2">
                  <SquareSlash className="size-3.5 text-brand" />
                  <span className="font-mono text-[12px] text-ink">{example}</span>
                </div>
                <pre className="ascii whitespace-pre-wrap">
                  {c.body.replace(/\$ARGUMENTS/g, `«${c.args.replace(/[<>[\]…]/g, '').trim()}»`)}
                </pre>
              </div>
              <SectionTitle className="mt-3 mb-1.5">Then</SectionTitle>
              <p className="text-[11.5px] text-soft">
                The resolved prompt goes to <span className="text-ink-2">{c.agent}</span>
                {c.model ? <> on <Mono tone="brand">{c.model}</Mono>, bypassing the router</> : <>, and the router picks the model for the task class</>}.
                Project rules are attached by the <Mono>UserPromptSubmit</Mono> hook before it is sent.
              </p>
            </Panel>
          </div>
        </div>
      </PageBody>
    </Page>
  );
}
