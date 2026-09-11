import { useMemo, useState } from 'react';
import { Lightbulb, Swords, Sparkles, ChevronDown, Flag } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import {
  Page, PageHeader, PageBody, Panel, Tag, Mono, ListRow, Ring, DataTable, Row, Cell, Empty,
} from '@/components/os';
import { brainstorms } from '@/mock/thinking';
import { projectName } from '@/mock/projects';
import { cn } from '@/lib/utils';

const SEV_TONE = { HIGH: 'danger', MEDIUM: 'warn', LOW: 'neutral' } as const;

export default function Brainstorm() {
  const [idea, setIdea] = useState('');
  const [sel, setSel] = useState(brainstorms[0].id);
  const [challenged, setChallenged] = useState<Record<string, boolean>>({});

  const b = useMemo(() => brainstorms.find((x) => x.id === sel) ?? brainstorms[0], [sel]);
  const shown = challenged[b.id];

  const run = () => {
    toast.success('Brainstorm started', { description: 'Idea → problem → users → … → roadmap, then a devil’s-advocate pass.' });
    setIdea('');
  };

  return (
    <Page>
      <PageHeader
        title="Brainstorm"
        subtitle="The OS does not start coding when you have an idea. It expands it, then sends a second agent to try to kill it."
      >
        <div className="pb-3">
          <div className="focus-brand flex items-start gap-2 rounded-md border border-line bg-base p-2.5">
            <Lightbulb className="mt-1 size-4 shrink-0 text-brand" />
            <textarea
              value={idea}
              onChange={(e) => setIdea(e.target.value)}
              rows={2}
              placeholder="I want to build a taxi marketplace…"
              className="min-w-0 flex-1 resize-none bg-transparent text-[13px] text-ink placeholder:text-dim focus-visible:outline-none"
            />
            <Button size="sm" disabled={!idea.trim()} onClick={run}><Sparkles className="size-3.5" />Brainstorm</Button>
          </div>
        </div>
      </PageHeader>

      <PageBody className="flex h-full flex-col gap-0 p-0 md:flex-row">
        <div className="no-scrollbar w-full shrink-0 max-h-[42vh] md:max-h-none md:w-[290px] overflow-y-auto border-b border-line md:border-b-0 md:border-r">
          {brainstorms.map((x) => (
            <ListRow key={x.id} active={x.id === b.id} onClick={() => setSel(x.id)}>
              <div className="flex items-center gap-2">
                <Mono>{x.ref}</Mono>
                <Tag tone="neutral">{projectName(x.projectHint)}</Tag>
                <span className={cn('tnum ml-auto text-[12px] font-semibold', x.score >= 75 ? 'text-ok' : x.score >= 55 ? 'text-warn' : 'text-danger')}>{x.score}</span>
              </div>
              <p className="mt-1 line-clamp-2 text-[12px] text-ink">{x.idea}</p>
              <p className="mt-1 text-[10.5px] text-dim">{x.createdAt}</p>
            </ListRow>
          ))}
        </div>

        <div className="min-w-0 flex-1 space-y-4 overflow-y-auto p-5">
          <Panel className="accent-top" eyebrow={`${b.ref} · idea`} title={b.idea}>
            <div className="flex items-center gap-4">
              <Ring pct={b.score} size={58} tone={b.score >= 75 ? 'ok' : b.score >= 55 ? 'warn' : 'danger'} />
              <p className="min-w-0 flex-1 text-[12.5px] leading-relaxed text-ink-2">{b.verdict}</p>
            </div>
          </Panel>

          {/* Expansion chain */}
          <div className="grid grid-cols-1 gap-3 stagger md:grid-cols-2 2xl:grid-cols-4">
            {b.nodes.map((n, i) => (
              <Panel key={n.id} eyebrow={`${i + 1} · ${n.stage}`} title={n.title} className="hover-lift">
                {n.items.length === 0 ? <p className="text-[11.5px] text-dim">—</p> : (
                  <ul className="space-y-1">
                    {n.items.map((it) => (
                      <li key={it} className="flex gap-1.5 text-[11.5px] leading-snug text-ink-2">
                        <span className="mt-1.5 size-1 shrink-0 rounded-full bg-brand" />{it}
                      </li>
                    ))}
                  </ul>
                )}
              </Panel>
            ))}
          </div>

          {/* Devil's advocate */}
          <Panel className={cn(shown && 'border-danger/35')} eyebrow="A second agent, instructed to kill the idea"
            title={<span className="flex items-center gap-2"><Swords className="size-4 text-danger" />Devil’s advocate</span>}
            actions={!shown && <Button size="xs" variant="destructive" onClick={() => setChallenged((c) => ({ ...c, [b.id]: true }))}>Challenge this idea<ChevronDown className="size-3" /></Button>}
            flush>
            {shown ? (
              <div className="divide-y divide-line animate-slide-up">
                {b.devilsAdvocate.map((o, i) => (
                  <div key={i} className="flex items-start gap-3 px-3.5 py-2.5">
                    <Tag tone={SEV_TONE[o.severity]}>{o.severity}</Tag>
                    <span className="text-[12.5px] leading-relaxed text-ink-2">{o.text}</span>
                  </div>
                ))}
              </div>
            ) : (
              <Empty icon={<Swords className="size-5" />} title={`${b.devilsAdvocate.length} objections are waiting`}
                hint="Read the expansion first. The challenge is more useful once you have fallen a little in love with the idea." />
            )}
          </Panel>

          <div className="grid grid-cols-1 gap-3 xl:grid-cols-3">
            <Panel eyebrow="The smallest thing worth building" title={<span className="flex items-center gap-1.5"><Flag className="size-3.5 text-brand" />MVP scope</span>}>
              <ul className="space-y-1">
                {b.mvp.map((m) => <li key={m} className="flex gap-1.5 text-[12px] text-ink-2"><span className="mt-1.5 size-1 shrink-0 rounded-full bg-ok" />{m}</li>)}
              </ul>
            </Panel>
            <Panel className="xl:col-span-2" eyebrow="Phased" title="Roadmap" flush>
              <DataTable head={['Phase', 'Weeks', 'Items']}>
                {b.roadmap.map((r) => (
                  <Row key={r.phase}>
                    <Cell className="font-medium text-ink">{r.phase}</Cell>
                    <Cell mono>{r.weeks}</Cell>
                    <Cell className="text-[11.5px] text-soft">{r.items.join(' · ')}</Cell>
                  </Row>
                ))}
              </DataTable>
            </Panel>
          </div>
        </div>
      </PageBody>
    </Page>
  );
}
