import { useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { GitBranchPlus, Lightbulb, Loader2, Sparkles, Swords } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import {
  Page, PageHeader, PageBody, Panel, Tag, Mono, ListRow, DataTable, Row, Cell, Empty, type Tone,
} from '@/components/os';
import type { BrainstormDoc } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { useData } from '@/lib/data';
import { useProject } from '@/lib/project-context';
import { projectLabel } from '@/lib/live/work';
import { cn } from '@/lib/utils';
import { ago } from '@/lib/time';

const DOT: Record<Tone, string> = {
  ok: 'bg-ok', brand: 'bg-brand', warn: 'bg-warn', danger: 'bg-danger', info: 'bg-info', violet: 'bg-violet', neutral: 'bg-dim',
};

export default function Brainstorm() {
  const nav = useNavigate();
  const { project } = useProject();
  const { can } = useAuth();
  const { brainstorms: mine, brainstorm, projects } = useData();
  const [idea, setIdea] = useState('');
  const [busy, setBusy] = useState(false);

  // ?ref= opens one brief; a click picks another until the link changes again.
  const wanted = useSearchParams()[0].get('ref');
  const [picked, setPicked] = useState<{ link: string | null; id: string } | null>(null);
  const selected = (picked && picked.link === wanted ? picked.id : null)
    ?? mine.find((b) => b.ref === wanted)?.id ?? mine[0]?.id ?? null;
  const pick = (id: string) => setPicked({ link: wanted, id });
  const doc = mine.find((b) => b.id === selected);

  const run = async () => {
    const text = idea.trim();
    if (text.length < 3 || busy) return;
    setBusy(true);
    const made = await brainstorm(text, project?.id);
    setBusy(false);
    if (!made) return;
    setIdea('');
    pick(made.id);
    toast.success(`${made.ref} is ready`, { description: `Written by ${made.compiler.model}.` });
  };
  const planIt = (d: BrainstormDoc) =>
    nav('/', { state: { draft: `${d.brief.title}. Build the MVP: ${d.brief.mvp.join('; ')}.` } });

  return (
    <Page>
      <PageHeader
        title="Brainstorm"
        subtitle="Turn an idea into a brief, then argue against it."
        about={<p>Nothing is coded. A model expands the idea into a problem, audience, MVP, risks and a roadmap.</p>}
      >
        <div className="pb-3">
          <div className="focus-brand flex items-start gap-2 rounded-md border border-line bg-base p-2.5">
            <Lightbulb className="mt-1 size-4 shrink-0 text-brand" />
            <textarea
              value={idea}
              onChange={(e) => setIdea(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); void run(); } }}
              rows={2}
              aria-label="Idea"
              placeholder="I want to build a taxi marketplace…"
              className="min-w-0 flex-1 resize-none bg-transparent text-[14px] text-ink placeholder:text-dim focus-visible:outline-none"
            />
            <Button size="sm" disabled={idea.trim().length < 3 || busy || !can('ai:use')} onClick={() => void run()}>
              {busy ? <Loader2 className="size-3.5 animate-spin" /> : <Sparkles className="size-3.5" />}Brainstorm
            </Button>
          </div>
        </div>
      </PageHeader>

      <PageBody className="flex h-full flex-col gap-0 p-0 md:flex-row">
        <div className="no-scrollbar w-full shrink-0 max-h-[42vh] md:max-h-none md:w-[290px] overflow-y-auto border-b border-line md:border-b-0 md:border-r">
          {mine.length > 0 && <div className="px-4 pt-3.5 pb-1.5 text-[12px] font-semibold text-dim">Briefs</div>}
          {mine.map((x) => (
            <ListRow key={x.id} active={x.id === selected} onClick={() => pick(x.id)}>
              <div className="flex items-center gap-2">
                <Mono>{x.ref}</Mono>
                {x.projectId && <Tag tone="neutral">{projectLabel(projects, x.projectId)}</Tag>}
                <span className="ml-auto shrink-0 text-[11.5px] text-dim">{ago(x.createdAt)}</span>
              </div>
              <p className="mt-1 line-clamp-2 text-[13px] text-ink">{x.brief.title}</p>
              <p className="mt-1 text-[11.5px] text-dim">{x.compiler.model}</p>
            </ListRow>
          ))}
          {mine.length === 0 && <p className="px-4 py-3.5 text-[12.5px] text-dim">Every brief you make is kept here.</p>}
        </div>

        {doc ? <BriefView doc={doc} onPlan={() => planIt(doc)} /> : (
          <div className="min-w-0 flex-1 p-5">
            <Empty icon={<Lightbulb className="size-6" />} title="No briefs yet"
              hint="Pitch an idea above. It needs a model: see Models → Keys." />
          </div>
        )}
      </PageBody>
    </Page>
  );
}

/** A brief a model wrote here. */
function BriefView({ doc, onPlan }: { doc: BrainstormDoc; onPlan: () => void }) {
  const b = doc.brief;
  return (
    <div className="min-w-0 flex-1 space-y-4 overflow-y-auto p-5">
      <Panel
        className="accent-top" eyebrow={`${doc.ref} · ${ago(doc.createdAt)} · ${doc.by}`} title={b.title}
        actions={<Button size="sm" onClick={onPlan}><GitBranchPlus className="size-3.5" />Plan the MVP</Button>}
      >
        <p className="text-[14px] leading-relaxed text-ink-2">{b.problem}</p>
        <p className="mt-3 flex items-center gap-2 text-[12.5px] text-dim">
          <Sparkles className="size-3.5 shrink-0" />
          {`Written by ${doc.compiler.model} in ${(doc.compiler.ms / 1000).toFixed(1)} s`}
        </p>
      </Panel>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <Panel title="Audience"><p className="text-[13.5px] leading-relaxed text-ink-2">{b.audience}</p></Panel>
        <Panel title="Value"><p className="text-[13.5px] leading-relaxed text-ink-2">{b.value}</p></Panel>
      </div>

      <div className="grid grid-cols-1 gap-3 xl:grid-cols-3">
        <Bullets title="MVP scope" items={b.mvp} tone="ok" />
        <Bullets title="Metrics" items={b.metrics} tone="brand" />
        <Bullets title="Open questions" items={b.questions} tone="warn" />
      </div>

      <Panel
        className="border-danger/35" eyebrow="Argued against, on purpose" flush
        title={<span className="flex items-center gap-2"><Swords className="size-4 text-danger" />Risks</span>}
      >
        <div className="divide-y divide-line">
          {b.risks.map((r, i) => <div key={i} className="px-5 py-2.5 text-[13.5px] leading-relaxed text-ink-2">{r}</div>)}
        </div>
      </Panel>

      <Panel title="Roadmap" flush>
        <DataTable head={['Phase', 'Items']}>
          {b.roadmap.map((r) => (
            <Row key={r.phase}>
              <Cell className="font-medium whitespace-nowrap text-ink">{r.phase}</Cell>
              <Cell className="text-[12.5px] text-soft">{r.items.join(' · ')}</Cell>
            </Row>
          ))}
        </DataTable>
      </Panel>
    </div>
  );
}

function Bullets({ title, items, tone }: { title: string; items: string[]; tone: Tone }) {
  return (
    <Panel title={title}>
      {items.length === 0 ? <p className="text-[12.5px] text-dim">None in this brief.</p> : (
        <ul className="space-y-1.5">
          {items.map((it, i) => (
            <li key={i} className="flex gap-2 text-[13px] leading-snug text-ink-2">
              <span className={cn('mt-1.5 size-1.5 shrink-0 rounded-full', DOT[tone])} />{it}
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
