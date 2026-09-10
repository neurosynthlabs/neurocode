import { useMemo, useState } from 'react';
import { Check, X, ScanEye, Quote } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import {
  Page, PageHeader, PageBody, Panel, Tag, Mono, Ascii, ListRow, DataTable, Row, Cell,
  Stat, StatGrid, Empty, SectionTitle, StatusText,
} from '@/components/os';
import { reviews } from '@/mock/testing';
import { projectName } from '@/mock/projects';
import { cn } from '@/lib/utils';

const SEV_TONE = { blocker: 'danger', major: 'warn', minor: 'info', nit: 'neutral' } as const;
const VERDICT_TONE = { approved: 'ok', changes_requested: 'warn', pending: 'neutral' } as const;

const LOOP = `  Reviewer ──▶ Coder ──▶ Reviewer ──▶ Tests ──▶ Reviewer ──▶ PASS ──▶ YOU
   round 1      fixes     round 2      green      round 2     verdict   merge
     ✗            ●          ✓           ✓           ✓          ✓        ○`;

export default function Review() {
  const [sel, setSel] = useState(reviews[0].id);
  const [decided, setDecided] = useState<Record<string, 'accepted' | 'changes'>>({});
  const [finding, setFinding] = useState<string | null>('f2');

  const r = useMemo(() => reviews.find((x) => x.id === sel) ?? reviews[0], [sel]);
  const f = useMemo(() => r.findings.find((x) => x.id === finding), [r, finding]);
  const blockers = r.findings.filter((x) => x.severity === 'blocker').length;

  return (
    <Page>
      <PageHeader
        title="Review"
        subtitle="The last gate before you. It refuses anything that breaks a legacy contract — even when the change is technically better."
        actions={<Tag tone="brand"><ScanEye className="size-3" />Kimi-K2.5</Tag>}
      />

      <PageBody className="space-y-4">
        <StatGrid cols={5}>
          <Stat label="Reviews today" value={reviews.length} />
          <Stat label="Approved" value={reviews.filter((x) => x.verdict === 'approved').length} tone="ok" />
          <Stat label="Changes requested" value={reviews.filter((x) => x.verdict === 'changes_requested').length} tone="warn" />
          <Stat label="Blockers raised" value={reviews.reduce((n, x) => n + x.findings.filter((y) => y.severity === 'blocker').length, 0)} tone="danger" sub="pipeline stopped" />
          <Stat label="Median rounds" value="2" sub="find → fix → verify" />
        </StatGrid>

        <Panel eyebrow="The automatic development loop" title="How a change actually lands">
          <Ascii>{LOOP}</Ascii>
        </Panel>

        <div className="flex min-h-[560px] gap-3">
          <div className="no-scrollbar w-[300px] shrink-0 overflow-y-auto rounded-md border border-line bg-surface">
            {reviews.map((x) => (
              <ListRow key={x.id} active={x.id === sel} onClick={() => { setSel(x.id); setFinding(x.findings[0]?.id ?? null); }}>
                <div className="flex items-center gap-2">
                  <Mono tone={x.id === sel ? 'brand' : 'neutral'}>{x.ref}</Mono>
                  <Tag tone={VERDICT_TONE[x.verdict]} className="ml-auto">{x.verdict.replace('_', ' ')}</Tag>
                </div>
                <p className="mt-1 text-[11.5px] text-ink-2">{x.taskRef} · round {x.round}</p>
                <div className="mt-1 flex items-center gap-2 text-[10.5px] text-dim">
                  <span>{x.filesChanged} files</span>
                  <span className="text-ok">+{x.additions}</span>
                  <span className="text-danger">−{x.deletions}</span>
                  <span className="ml-auto">{x.createdAt}</span>
                </div>
              </ListRow>
            ))}
          </div>

          <div className="min-w-0 flex-1 space-y-3">
            <Panel eyebrow={`${r.reviewer} · round ${r.round} · ${projectName(r.projectId)} · ${r.createdAt}`}
              title={<span className="flex flex-wrap items-center gap-2"><Mono tone="brand">{r.ref}</Mono>{r.taskRef}<Tag tone={VERDICT_TONE[r.verdict]}>{r.verdict.replace('_', ' ')}</Tag></span>}
              actions={<span className="text-[11px] text-dim">{r.filesChanged} files · <span className="text-ok">+{r.additions}</span> <span className="text-danger">−{r.deletions}</span></span>}>
              <div className="flex items-start gap-2.5 rounded-sm border border-line bg-base p-3">
                <Quote className="mt-0.5 size-3.5 shrink-0 text-brand" />
                <p className="text-[12.5px] leading-relaxed text-ink-2 italic">{r.reviewerNote}</p>
              </div>
              <div className="mt-3 flex items-center gap-2 border-t border-line pt-3">
                {decided[r.id] ? (
                  <Tag tone={decided[r.id] === 'accepted' ? 'ok' : 'warn'}>
                    {decided[r.id] === 'accepted' ? 'accepted by you — queued for merge' : 'sent back to the agent'}
                  </Tag>
                ) : (
                  <>
                    <Button size="sm" disabled={blockers > 0}
                      onClick={() => { setDecided((d) => ({ ...d, [r.id]: 'accepted' })); toast.success(`${r.ref} accepted`); }}>
                      <Check className="size-3.5" />Accept
                    </Button>
                    <Button size="sm" variant="outline"
                      onClick={() => { setDecided((d) => ({ ...d, [r.id]: 'changes' })); toast(`${r.ref} sent back`, { description: 'The agent gets the findings and re-runs.' }); }}>
                      <X className="size-3.5" />Request changes
                    </Button>
                    {blockers > 0 && <span className="text-[11.5px] text-danger">{blockers} blocker{blockers > 1 ? 's' : ''} must be resolved before this can be accepted.</span>}
                  </>
                )}
              </div>
            </Panel>

            <Panel eyebrow={`${r.checks.filter((c) => c.status === 'pass').length} of ${r.checks.length} passing`} title="Checklist" flush>
              <div className="divide-y divide-line">
                {r.checks.map((c) => (
                  <div key={c.id} className="flex items-start gap-3 px-3.5 py-2">
                    <span className="w-40 shrink-0"><StatusText state={c.status} label={c.label} /></span>
                    <span className="min-w-0 flex-1 text-[11.5px] text-soft">{c.note}</span>
                  </div>
                ))}
              </div>
            </Panel>

            <Panel eyebrow={`${r.findings.length} findings · every one cites file:line`} title="Findings" flush>
              {r.findings.length === 0 ? (
                <Empty icon={<Check className="size-5" />} title="Nothing to fix" hint="Every check passed and no finding was raised in this round." />
              ) : (
                <DataTable head={['Severity', 'Location', 'Rule', 'Message', 'Raised by']}>
                  {r.findings.map((x) => (
                    <Row key={x.id} active={finding === x.id} onClick={() => setFinding(x.id)}>
                      <Cell><Tag tone={SEV_TONE[x.severity]}>{x.severity}</Tag></Cell>
                      <Cell mono className="text-dim">{x.file.split('/').pop()}:{x.line}</Cell>
                      <Cell mono>{x.rule}</Cell>
                      <Cell className="max-w-[440px] text-[11.5px] text-ink-2">{x.message}</Cell>
                      <Cell className="text-[11.5px] text-dim">{x.agent}</Cell>
                    </Row>
                  ))}
                </DataTable>
              )}
            </Panel>

            {f && (
              <Panel className={cn('accent-left', f.severity === 'blocker' && 'border-danger/35')}
                eyebrow={`${f.rule} · raised by ${f.agent}`}
                title={<span className="flex items-center gap-2"><Tag tone={SEV_TONE[f.severity]}>{f.severity}</Tag><Mono>{f.file}:{f.line}</Mono></span>}>
                <SectionTitle>Problem</SectionTitle>
                <p className="text-[12.5px] leading-relaxed text-ink-2">{f.message}</p>
                <SectionTitle className="mt-3">Suggested fix</SectionTitle>
                <p className="rounded-sm border border-line bg-base p-2.5 text-[12.5px] leading-relaxed text-ink-2">{f.suggestion}</p>
              </Panel>
            )}
          </div>
        </div>
      </PageBody>
    </Page>
  );
}
