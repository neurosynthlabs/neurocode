import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  ArrowDown, FileCode, Database, Boxes, HelpCircle, FlaskConical, CircleCheck,
  CircleDot, Circle, CircleX, MinusCircle, Play, Cpu, RefreshCw, Check,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Page, PageHeader, PageBody, Panel, Tag, RiskPill, Mono, ListRow, Empty,
  BlockBar, SectionTitle, KV, Bar,
} from '@/components/os';
import { projectName } from '@/mock/projects';
import { inFlight, useData } from '@/lib/data';
import { cn } from '@/lib/utils';
import { toast } from 'sonner';
import type { Plan, PlanStepState } from '@/types';

const STATE_ICON: Record<PlanStepState, { icon: typeof Circle; cls: string; mark: string }> = {
  done:    { icon: CircleCheck, cls: 'text-ok',     mark: '✓' },
  active:  { icon: CircleDot,   cls: 'text-brand',  mark: '●' },
  todo:    { icon: Circle,      cls: 'text-dim',    mark: '○' },
  failed:  { icon: CircleX,     cls: 'text-danger', mark: '✗' },
  skipped: { icon: MinusCircle, cls: 'text-dim',    mark: '–' },
};

const STAGES = [
  { k: 'rawRequirement',       label: 'Raw requirement',      hint: 'what you actually typed' },
  { k: 'businessRequirement',  label: 'Business requirement', hint: 'what it means for the business' },
  { k: 'technicalRequirement', label: 'Technical requirement',hint: 'what has to change in the code' },
] as const;

/** Which planner wrote a compiled plan. The rules planner is marked as such: it is not a model. */
function CompiledBy({ p }: { p: Plan }) {
  if (!p.compiler) return null;
  const rules = p.compiler.provider === 'rules';
  return (
    <span title={rules ? 'Keyword rules, not a model. Set DEEPSEEK_API_KEY or run Ollama for a real compile.' : `Compiled by ${p.compiler.model}`}>
      <Tag tone={rules ? 'warn' : 'brand'}>
        <Cpu className="size-3" />{rules ? 'offline planner' : p.compiler.model} · {(p.compiler.ms / 1000).toFixed(1)}s
      </Tag>
    </span>
  );
}

export default function Plans() {
  const nav = useNavigate();
  const { plans, mode, settleQuestion, dispatchPlan, recompile } = useData();
  const wanted = useSearchParams()[0].get('ref');
  const [sel, setSel] = useState(wanted ?? plans[0]?.ref ?? '');
  const [draft, setDraft] = useState<{ index: number; text: string } | null>(null);
  const [working, setWorking] = useState<'dispatch' | 'recompile' | null>(null);
  const p = useMemo(() => plans.find((x) => x.ref === sel) ?? plans[0], [plans, sel]);
  // ⌘K opens a plan with ?ref=, also when this screen is already showing
  useEffect(() => { if (wanted) { setSel(wanted); setDraft(null); } }, [wanted]);

  if (!p) {
    return (
      <Page>
        <PageHeader title="Plans" subtitle="The requirement compiler." />
        <PageBody><Empty title="No plans yet" hint="Compile a requirement from the Command Center." /></PageBody>
      </Page>
    );
  }

  const doneSteps = p.steps.filter((s) => s.state === 'done').length;
  const underway = inFlight(p);
  const open = p.openQuestions.length;

  const settle = async (index: number, answer: string | null) => {
    if (!(await settleQuestion(p.ref, index, answer))) return;
    setDraft(null);
    if (answer === null) toast('Deferred', { description: 'The plan proceeds under its stated assumption.' });
    else toast.success('Answer recorded', { description: 'Saved to memory as a business rule, so the next plan knows it.' });
  };

  const dispatch = async () => {
    setWorking('dispatch');
    const ok = await dispatchPlan(p.ref);
    setWorking(null);
    if (!ok) return;
    toast.success(`${p.ref} dispatched`, { description: `${p.taskRef} is in progress. ${p.steps[0]?.agent ?? 'The first agent'} starts.` });
    nav('/tasks');
  };

  const again = async () => {
    if (mode !== 'live') {
      toast('Re-compiling needs the local API', { description: 'This demo runs on sample data. Start it with npm run dev:start.' });
      return;
    }
    setWorking('recompile');
    const doc = await recompile(p.ref);
    setWorking(null);
    if (doc) toast.success(`${doc.ref} re-compiled`, { description: `${doc.steps.length} steps · ${doc.openQuestions.length} open questions` });
  };

  return (
    <Page>
      <PageHeader
        title="Plans"
        subtitle="The requirement compiler. Broken Hinglish in, an evidenced implementation plan out — with the open questions it refuses to guess at."
        actions={<Button size="sm" onClick={() => nav('/tasks')}><Play className="size-3.5" />Open the board</Button>}
      />

      <PageBody className="flex h-full flex-col gap-0 p-0 md:flex-row">
        {/* Plan list */}
        <div className="no-scrollbar w-full shrink-0 max-h-[42vh] md:max-h-none md:w-[300px] overflow-y-auto border-b border-line md:border-b-0 md:border-r">
          {plans.map((x) => {
            const d = x.steps.filter((s) => s.state === 'done').length;
            return (
              <ListRow key={x.ref} active={x.ref === p.ref} onClick={() => { setSel(x.ref); setDraft(null); }}>
                <div className="flex items-center gap-2">
                  <Mono tone={x.ref === p.ref ? 'brand' : 'neutral'}>{x.ref}</Mono>
                  {!inFlight(x) && <Tag tone="neutral">draft</Tag>}
                  <span className="ml-auto"><RiskPill risk={x.risk} bare /></span>
                </div>
                <p className="mt-1 line-clamp-2 text-[13px] text-ink">{x.technicalRequirement.split('.')[0]}.</p>
                <div className="mt-1.5 flex items-center gap-2">
                  <BlockBar pct={(d / x.steps.length) * 100} width={10} />
                  <span className="tnum text-[11.5px] text-dim">{d}/{x.steps.length}</span>
                  <span className="ml-auto text-[11.5px] text-dim">conf {x.confidence}%</span>
                </div>
                <p className="mt-1 truncate text-[11px] text-dim">{x.taskRef} · {projectName(x.projectId)}</p>
              </ListRow>
            );
          })}
        </div>

        {/* Plan detail */}
        <div className="min-w-0 flex-1 overflow-y-auto p-5">
          <div className="mb-4 flex flex-wrap items-center gap-2">
            <Mono tone="brand">{p.ref}</Mono>
            <Mono>{p.taskRef}</Mono>
            <Tag tone="neutral">{projectName(p.projectId)}</Tag>
            <RiskPill risk={p.risk} />
            <CompiledBy p={p} />
            <span className="ml-auto text-[12.5px] text-dim">compiled {p.createdAt}</span>
          </div>

          {/* Compiler chain */}
          <div className="space-y-0">
            {STAGES.map((s, i) => (
              <div key={s.k}>
                <Panel eyebrow={s.hint} title={s.label} className={i === 0 ? 'border-brand/35' : undefined}>
                  <p className={cn('leading-relaxed', i === 0 ? 'text-[14px] text-ink' : 'text-[13.5px] text-ink-2')}>
                    {p[s.k]}
                  </p>
                </Panel>
                <div className="flex justify-center py-1.5"><ArrowDown className="size-3.5 text-line-strong" /></div>
              </div>
            ))}

            <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
              <Panel eyebrow={`${p.affectedModules.length} modules`} title={<span className="flex items-center gap-1.5"><Boxes className="size-3.5 text-brand" />Affected modules</span>} flush>
                <div className="divide-y divide-line">
                  {p.affectedModules.length === 0
                    ? <div className="px-3.5 py-3 text-[13px] text-dim">None named yet.</div>
                    : p.affectedModules.map((m) => <div key={m} className="px-3.5 py-1.5 text-[13px] text-ink-2">{m}</div>)}
                </div>
              </Panel>
              <Panel eyebrow={`${p.affectedFiles.length} files`} title={<span className="flex items-center gap-1.5"><FileCode className="size-3.5 text-brand" />Affected files</span>} flush>
                <div className="divide-y divide-line">
                  {p.affectedFiles.length === 0
                    ? <div className="px-3.5 py-3 text-[13px] text-dim">None named yet.</div>
                    : p.affectedFiles.map((f) => <div key={f} className="truncate px-3.5 py-1.5 font-mono text-[12px] text-ink-2" title={f}>{f}</div>)}
                </div>
              </Panel>
              <Panel eyebrow={`${p.affectedDb.length} objects`} title={<span className="flex items-center gap-1.5"><Database className="size-3.5 text-brand" />Affected database</span>} flush>
                <div className="divide-y divide-line">
                  {p.affectedDb.length === 0
                    ? <div className="px-3.5 py-3 text-[13px] text-dim">No database change.</div>
                    : p.affectedDb.map((d) => <div key={d} className="px-3.5 py-1.5 font-mono text-[12px] text-ink-2">{d}</div>)}
                </div>
              </Panel>
            </div>

            <div className="flex justify-center py-1.5"><ArrowDown className="size-3.5 text-line-strong" /></div>

            <Panel eyebrow="Architecture impact" title="What this changes structurally" className="border-warn/30">
              <p className="text-[13.5px] leading-relaxed text-ink-2">{p.architectureImpact}</p>
            </Panel>

            <div className="flex justify-center py-1.5"><ArrowDown className="size-3.5 text-line-strong" /></div>

            {/* Steps */}
            <Panel
              eyebrow="Implementation plan"
              title={`${doneSteps} of ${p.steps.length} steps complete`}
              actions={<span className="flex items-center gap-2"><BlockBar pct={(doneSteps / p.steps.length) * 100} width={14} /></span>}
              flush
            >
              <div className="divide-y divide-line">
                {p.steps.map((s) => {
                  const S = STATE_ICON[s.state];
                  const Icon = S.icon;
                  return (
                    <div key={s.id} className={cn('flex items-start gap-3 px-3.5 py-2.5', s.state === 'active' && 'bg-brand/5')}>
                      <span className="tnum mt-px w-4 shrink-0 text-right font-mono text-[12px] text-dim">{s.n}</span>
                      <Icon className={cn('mt-px size-3.5 shrink-0', S.cls)} />
                      <span className="min-w-0 flex-1">
                        <span className={cn('text-[13.5px]', s.state === 'todo' ? 'text-soft' : 'font-medium text-ink')}>{s.label}</span>
                        <span className="block text-[12.5px] text-dim">{s.detail}</span>
                      </span>
                      <span className="shrink-0 text-right">
                        <span className="block text-[12px] text-soft">{s.agent}</span>
                        {s.durationS !== undefined && (
                          <span className="tnum block font-mono text-[11.5px] text-dim">
                            {s.durationS >= 60 ? `${Math.floor(s.durationS / 60)}m ${s.durationS % 60}s` : `${s.durationS}s`}
                          </span>
                        )}
                      </span>
                    </div>
                  );
                })}
              </div>
            </Panel>
          </div>

          {/* Confidence + questions */}
          <div className="mt-4 grid grid-cols-1 gap-3 lg:grid-cols-2">
            <Panel eyebrow="How sure the OS is" title={`Confidence: ${p.confidence}%`}>
              <Bar pct={p.confidence} tone={p.confidence >= 85 ? 'ok' : p.confidence >= 70 ? 'warn' : 'danger'} height="h-1.5" />
              <SectionTitle className="mt-3 mb-1.5">Evidence</SectionTitle>
              <div className="space-y-1">
                {p.compiler ? (
                  <>
                    <KV k="Memory consulted" v={p.cited?.length ? p.cited.join(' · ') : 'nothing matched'} />
                    <KV k="Files named" v={p.affectedFiles.length} />
                    <KV k="Database objects named" v={p.affectedDb.length} />
                    <KV k="Compiled by" v={p.compiler.provider === 'rules' ? 'offline planner, not a model' : p.compiler.model} />
                  </>
                ) : (
                  <>
                    <KV k="Source files read" v={p.affectedFiles.length + 5} />
                    <KV k="Database objects mapped" v={p.affectedDb.length} />
                    <KV k="Previous fixes reviewed" v={4} />
                    <KV k="Decisions cited" v="ADR-47 · ADR-49 · MEM-142" />
                  </>
                )}
              </div>
              <SectionTitle className="mt-3 mb-1.5">Unknown</SectionTitle>
              <p className="text-[12.5px] text-warn">
                {open > 0
                  ? `${open} business question${open > 1 ? 's' : ''} not documented anywhere in the codebase.`
                  : 'Nothing material is unknown for this plan.'}
              </p>
            </Panel>

            <Panel eyebrow="The plan refuses to guess" title={<span className="flex items-center gap-1.5"><HelpCircle className="size-3.5 text-warn" />Open questions</span>} flush>
              {open === 0 ? (
                <Empty title="No open questions" hint="Everything this plan needed is documented or decided." />
              ) : (
                <div className="divide-y divide-line">
                  {p.openQuestions.map((q, i) => (
                    <div key={`${p.ref}-${q}`} className="px-3.5 py-2.5">
                      <p className="text-[13px] text-ink-2">{q}</p>
                      {draft?.index === i ? (
                        <form
                          className="mt-2 space-y-1.5"
                          onSubmit={(e) => { e.preventDefault(); if (draft.text.trim()) settle(i, draft.text.trim()); }}
                        >
                          <textarea
                            autoFocus
                            rows={2}
                            value={draft.text}
                            onChange={(e) => setDraft({ index: i, text: e.target.value })}
                            aria-label={`Answer: ${q}`}
                            placeholder="Your answer becomes a business rule in memory…"
                            className="w-full resize-none rounded-sm border border-line bg-base px-2.5 py-1.5 text-[13px] text-ink placeholder:text-dim focus-visible:border-brand focus-visible:outline-none"
                          />
                          <div className="flex gap-1.5">
                            <Button size="xs" type="submit" disabled={!draft.text.trim()}><Check className="size-3" />Save answer</Button>
                            <Button size="xs" type="button" variant="ghost" onClick={() => setDraft(null)}>Cancel</Button>
                          </div>
                        </form>
                      ) : (
                        <div className="mt-1.5 flex gap-1.5">
                          <Button size="xs" variant="outline" onClick={() => setDraft({ index: i, text: '' })}>Answer</Button>
                          <Button size="xs" variant="ghost" onClick={() => settle(i, null)}>Defer</Button>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
              {(p.answered?.length || p.deferred?.length) ? (
                <div className="space-y-1.5 border-t border-line px-3.5 py-2.5">
                  {p.answered?.map((a) => (
                    <p key={a.q} className="text-[12.5px] text-soft">
                      <Check className="mr-1 inline size-3 text-ok" /><span className="text-ink-2">{a.q}</span> → {a.a}
                    </p>
                  ))}
                  {p.deferred?.map((q) => <p key={q} className="text-[12.5px] text-dim">Deferred: {q}</p>)}
                </div>
              ) : null}
            </Panel>
          </div>

          <Panel className="mt-3" eyebrow="Verification" title={<span className="flex items-center gap-1.5"><FlaskConical className="size-3.5 text-brand" />Test plan</span>} flush>
            <div className="divide-y divide-line">
              {p.testPlan.map((t, i) => (
                <div key={i} className="flex items-start gap-2.5 px-3.5 py-1.5">
                  <span className="tnum mt-px w-4 shrink-0 text-right font-mono text-[11.5px] text-dim">{i + 1}</span>
                  <span className="text-[13px] text-ink-2">{t}</span>
                </div>
              ))}
            </div>
          </Panel>

          <div className="mt-4 flex flex-wrap items-center gap-2">
            <Button size="sm" disabled={underway || open > 0 || working !== null} onClick={dispatch}>
              <Play className="size-3.5" />{underway ? 'Dispatched' : working === 'dispatch' ? 'Dispatching…' : 'Dispatch plan'}
            </Button>
            <Button size="sm" variant="outline" onClick={() => nav('/architecture')}>See impact analysis</Button>
            <Button size="sm" variant="ghost" disabled={underway || working !== null} onClick={again}>
              <RefreshCw className={cn('size-3.5', working === 'recompile' && 'animate-spin')} />
              {working === 'recompile' ? 'Re-compiling…' : 'Re-compile'}
            </Button>
            {!underway && open > 0 && (
              <span className="text-[12px] text-warn">Answer or defer {open} open question{open > 1 ? 's' : ''} to dispatch.</span>
            )}
          </div>
        </div>
      </PageBody>
    </Page>
  );
}
