import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  ArrowDown, FileCode, Database, Boxes, HelpCircle, FlaskConical, Play, Cpu, RefreshCw, Check, Workflow,
  BookOpen, ListChecks, Pencil,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Page, PageHeader, PageBody, Panel, Tag, RiskPill, Mono, ListRow, Empty,
  BlockBar, SectionTitle, KV, Bar,
} from '@/components/os';
import { inFlight, useData } from '@/lib/data';
import { ApiError, type RunDoc } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { instructionsApi, type GroundedPlan } from '@/lib/live/instructions';
import { runtimeApi } from '@/lib/live/runtime';
import { workflowsApi } from '@/lib/live/workflows';
import { projectLabel } from '@/lib/live/work';
import { cn } from '@/lib/utils';
import { toast } from 'sonner';
import type { Plan } from '@/types';
import { ago } from '@/lib/time';

/* A plan's steps are what was agreed; how far the work got is its run's to say. The server moves a
   plan step only once, at dispatch, so progress here is read from the newest run that carries the plan. */
const FINISHED = ['done', 'skipped', 'failed'];
const finished = (r: RunDoc) => r.steps.filter((s) => FINISHED.includes(s.status)).length;

const STAGES = [
  { k: 'rawRequirement',       label: 'Raw requirement',      hint: 'what you actually typed' },
  { k: 'businessRequirement',  label: 'Business requirement', hint: 'what it means for the business' },
  { k: 'technicalRequirement', label: 'Technical requirement',hint: 'what has to change in the code' },
] as const;

/** One affected file, and what checking it against the code index said. */
function FileLine({ path, p }: { path: string; p: GroundedPlan }) {
  const check = p.fileCheck;
  const candidates = check?.ambiguous[path];
  return (
    <div className="flex items-center gap-2 px-3.5 py-1.5">
      <span className="min-w-0 flex-1 truncate font-mono text-[12px] text-ink-2" title={path}>{path}</span>
      {check?.newFiles.includes(path) && <span title="Not in the code index: a file this change creates"><Tag tone="brand">new</Tag></span>}
      {candidates && <span title={`Matches ${candidates.join(', ')} in the code index`}><Tag tone="warn">{candidates.length}+ matches</Tag></span>}
    </div>
  );
}

/** What the compiler was handed: instruction files, code and document pieces, remembered facts. */
function GroundedIn({ p }: { p: GroundedPlan }) {
  const grounding = p.grounding ?? [];
  const told = grounding.filter((g) => g.kind === 'instructions');
  const pieces = grounding.filter((g) => g.kind !== 'instructions');
  const facts = p.cited ?? [];
  if (!p.compiler) {
    return <p className="px-3.5 py-3 text-[13px] text-dim">Written by a workflow, not compiled — nothing was handed to a model.</p>;
  }
  if (told.length + pieces.length + facts.length === 0) {
    return <p className="px-3.5 py-3 text-[13px] text-dim">Nothing: no instruction files, no indexed code and no memory matched. The plan rests on the requirement alone.</p>;
  }
  const group = (label: string, items: { key: string; main: string; sub?: string }[]) => items.length > 0 && (
    <div className="px-3.5 py-2.5">
      <p className="mb-1 text-[12px] font-medium text-dim">{label}</p>
      {items.map((x) => (
        <p key={x.key} className="truncate font-mono text-[12px] text-ink-2" title={x.sub ? `${x.main} · ${x.sub}` : x.main}>
          {x.main}{x.sub && <span className="text-dim"> · {x.sub}</span>}
        </p>
      ))}
    </div>
  );
  return (
    <div className="divide-y divide-line">
      {group('Instruction files', told.map((g) => ({ key: g.path, main: g.path, sub: `sha ${g.ref.slice(0, 7)}` })))}
      {group('Code and documents from retrieval', pieces.map((g) => ({ key: g.ref, main: g.ref, sub: g.kind === 'doc' ? 'document' : undefined })))}
      {group('Memory facts', facts.map((ref) => ({ key: ref, main: ref })))}
    </div>
  );
}

/** Which model compiled a plan, or the workflow that wrote it. */
function CompiledBy({ p, workflow }: { p: Plan; workflow: string | null }) {
  if (p.compiler) {
    return (
      <span title={`Compiled by ${p.compiler.model}`}>
        <Tag tone="brand"><Cpu className="size-3" />{p.compiler.model} · {(p.compiler.ms / 1000).toFixed(1)}s</Tag>
      </span>
    );
  }
  if (!p.workflowId) return null;
  return <Tag tone="violet"><Workflow className="size-3" />From workflow {workflow ?? p.workflowId}</Tag>;
}

export default function Plans() {
  const nav = useNavigate();
  const { plans, runs, projects, settleQuestion, dispatchPlan, recompile } = useData();
  const { can } = useAuth();
  const [workflows, setWorkflows] = useState<Record<string, string>>({});
  const wanted = useSearchParams()[0].get('ref');
  // ?ref= (⌘K) opens a plan, also when this screen is already showing; a click picks another until the link changes.
  const [picked, setPicked] = useState<{ link: string | null; ref: string } | null>(null);
  const sel = (picked && picked.link === wanted ? picked.ref : null) ?? wanted ?? plans[0]?.ref ?? '';
  // An answer being typed belongs to one plan's question, so opening another plan never shows it.
  const [typing, setTyping] = useState<{ plan: string; index: number; text: string } | null>(null);
  const [working, setWorking] = useState<'dispatch' | 'recompile' | 'criteria' | null>(null);
  // Criteria being edited belong to one plan, like an answer being typed. `saved` is the server's answer to
  // a save and the store's criteria at that moment: it is shown only while the store still holds those, so
  // the list never flashes back to the old sentences, and whatever the stream delivers next wins.
  const [criteriaDraft, setCriteriaDraft] = useState<{ plan: string; text: string } | null>(null);
  const [saved, setSaved] = useState<{ doc: GroundedPlan; before: string } | null>(null);
  // "Run until done": attempts in all, the first included — the API takes 1 to 5.
  const [goal, setGoal] = useState<{ on: boolean; budget: number }>({ on: false, budget: 3 });
  const p = useMemo(() => plans.find((x) => x.ref === sel) ?? plans[0], [plans, sel]);
  // Workflow names, only when a plan came from one: the plan keeps the id, the workflow keeps the name.
  const fromWorkflow = plans.some((x) => x.workflowId);
  useEffect(() => {
    if (!fromWorkflow) return;
    let live = true;
    workflowsApi.list(null).then(
      (found) => { if (live) setWorkflows(Object.fromEntries(found.map((w) => [w.id, w.name]))); },
      (e: unknown) => { console.error('[NeuroCode] GET /workflows failed:', e); },
    );
    return () => { live = false; };
  }, [fromWorkflow]);
  // Runs arrive newest first, so the first lead run for a plan is its latest attempt.
  const runOf = useMemo(() => {
    const m = new Map<string, RunDoc>();
    runs.forEach((r) => { if (r.planRef && !r.parent && !m.has(r.planRef)) m.set(r.planRef, r); });
    return m;
  }, [runs]);

  if (!p) {
    return (
      <Page>
        <PageHeader title="Plans" subtitle="The requirement compiler." />
        <PageBody><Empty title="No plans yet" hint="Compile a requirement from the Command Center. If no project is onboarded, onboard one in Projects first." /></PageBody>
      </Page>
    );
  }

  const held: GroundedPlan = p;
  const g: GroundedPlan = saved?.doc.ref === p.ref && (held.acceptanceCriteria ?? []).join('\n') === saved.before
    ? { ...p, acceptanceCriteria: saved.doc.acceptanceCriteria, criteriaEdited: saved.doc.criteriaEdited }
    : p;
  const criteria = g.acceptanceCriteria ?? [];
  const editingCriteria = criteriaDraft?.plan === p.ref ? criteriaDraft : null;
  const run = runOf.get(p.ref);
  const draft = typing?.plan === p.ref ? typing : null;
  const setDraft = (next: { index: number; text: string } | null) => setTyping(next && { plan: p.ref, ...next });
  const workflow = p.workflowId ? workflows[p.workflowId] ?? null : null;
  const underway = inFlight(p);
  const open = p.openQuestions.length;

  const settle = async (index: number, answer: string | null) => {
    if (!(await settleQuestion(p.ref, index, answer))) return;
    setDraft(null);
    if (answer === null) toast('Deferred', { description: 'The plan proceeds under its stated assumption.' });
    else toast.success('Answer recorded', { description: 'Saved to memory as a business rule, so the next plan knows it.' });
  };

  const untilDone = goal.on && criteria.length > 0;
  const dispatch = async () => {
    setWorking('dispatch');
    let ok: boolean;
    if (untilDone) {
      // The store's own dispatch sends no budget; the plan and its run arrive on the stream either way.
      try {
        await runtimeApi.dispatch(p.ref, { goalBudget: goal.budget });
        ok = true;
      } catch (e) {
        toast.error('Not dispatched', { description: e instanceof ApiError ? e.message : 'The local API did not answer.' });
        ok = false;
      }
    } else {
      ok = await dispatchPlan(p.ref);
    }
    setWorking(null);
    if (!ok) return;
    toast.success(`${p.ref} dispatched`, {
      description: untilDone
        ? `${p.taskRef} runs until its acceptance criteria are met, ${goal.budget} attempt${goal.budget > 1 ? 's' : ''} at most. It still stops at your signature.`
        : `${p.taskRef} is in progress. Its run, once one starts, is in Live runs.`,
    });
    nav('/tasks');
  };

  const saveCriteria = async (text: string) => {
    setWorking('criteria');
    try {
      const before = (held.acceptanceCriteria ?? []).join('\n');
      const doc = await instructionsApi.setCriteria(p.ref, text.split('\n').map((x) => x.trim()).filter(Boolean));
      setSaved({ doc, before });
      setCriteriaDraft(null);
      toast.success('Acceptance criteria saved', { description: `${doc.acceptanceCriteria?.length ?? 0} for ${doc.ref}. A re-compile keeps them.` });
    } catch (e) {
      toast.error('Not saved', { description: e instanceof ApiError ? e.message : 'The local API did not answer.' });
    } finally {
      setWorking(null);
    }
  };

  const again = async () => {
    setWorking('recompile');
    const doc = await recompile(p.ref);
    setWorking(null);
    if (doc) toast.success(`${doc.ref} re-compiled`, { description: `${doc.steps.length} steps · ${doc.openQuestions.length} open questions` });
  };

  return (
    <Page>
      <PageHeader
        title="Plans"
        subtitle="The requirement compiler. A requirement in your own words goes in; an evidenced implementation plan comes out, with the open questions it refuses to guess at."
        actions={<Button size="sm" onClick={() => nav('/tasks')}><Play className="size-3.5" />Open the board</Button>}
      />

      <PageBody className="flex h-full flex-col gap-0 p-0 md:flex-row">
        {/* Plan list */}
        <div className="no-scrollbar w-full shrink-0 max-h-[42vh] md:max-h-none md:w-[300px] overflow-y-auto border-b border-line md:border-b-0 md:border-r">
          {plans.map((x) => {
            const r = runOf.get(x.ref);
            return (
              <ListRow key={x.ref} active={x.ref === p.ref} onClick={() => setPicked({ link: wanted, ref: x.ref })}>
                <div className="flex items-center gap-2">
                  <Mono tone={x.ref === p.ref ? 'brand' : 'neutral'}>{x.ref}</Mono>
                  {!inFlight(x) && <Tag tone="neutral">draft</Tag>}
                  <span className="ml-auto"><RiskPill risk={x.risk} bare /></span>
                </div>
                <p className="mt-1 line-clamp-2 text-[13px] text-ink">{(x.technicalRequirement || x.rawRequirement).split('.')[0]}.</p>
                <div className="mt-1.5 flex items-center gap-2">
                  {r ? (
                    <>
                      <BlockBar pct={(finished(r) / Math.max(1, r.steps.length)) * 100} width={10} />
                      <span className="tnum text-[11.5px] text-dim">{finished(r)}/{r.steps.length}</span>
                    </>
                  ) : <span className="text-[11.5px] text-dim">{x.steps.length} steps</span>}
                  <span className="ml-auto text-[11.5px] text-dim">{x.confidence === null ? 'not compiled' : `conf ${x.confidence}%`}</span>
                </div>
                <p className="mt-1 truncate text-[11px] text-dim">{x.taskRef} · {projectLabel(projects, x.projectId)}</p>
              </ListRow>
            );
          })}
        </div>

        {/* Plan detail */}
        <div className="min-w-0 flex-1 overflow-y-auto p-5">
          <div className="mb-4 flex flex-wrap items-center gap-2">
            <Mono tone="brand">{p.ref}</Mono>
            <Mono>{p.taskRef}</Mono>
            <Tag tone="neutral">{projectLabel(projects, p.projectId)}</Tag>
            <RiskPill risk={p.risk} />
            <CompiledBy p={p} workflow={workflow} />
            <span className="ml-auto text-[12.5px] text-dim">{p.compiler ? 'compiled' : 'written'} {ago(p.createdAt)}</span>
          </div>

          {/* Compiler chain */}
          <div className="space-y-0">
            {STAGES.filter((s) => p[s.k]).map((s, i) => (
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
              <Panel
                eyebrow={!g.fileCheck ? `${p.affectedFiles.length} files` : !g.fileCheck.checked
                  ? `${p.affectedFiles.length} files · not checked, no code index`
                  : `${p.affectedFiles.length - g.fileCheck.newFiles.length} in the index · ${g.fileCheck.newFiles.length} new`}
                title={<span className="flex items-center gap-1.5"><FileCode className="size-3.5 text-brand" />Affected files</span>} flush>
                <div className="divide-y divide-line">
                  {p.affectedFiles.length === 0
                    ? <div className="px-3.5 py-3 text-[13px] text-dim">None named yet.</div>
                    : p.affectedFiles.map((f) => <FileLine key={f} path={f} p={g} />)}
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

            {p.architectureImpact && (
              <>
                <Panel eyebrow="Architecture impact" title="What this changes structurally" className="border-warn/30">
                  <p className="text-[13.5px] leading-relaxed text-ink-2">{p.architectureImpact}</p>
                </Panel>
                <div className="flex justify-center py-1.5"><ArrowDown className="size-3.5 text-line-strong" /></div>
              </>
            )}

            {/* Steps */}
            <Panel
              eyebrow={run ? `${run.ref} · ${finished(run)} of ${run.steps.length} run steps finished` : 'Implementation plan'}
              title={`${p.steps.length} step${p.steps.length === 1 ? '' : 's'}, as agreed`}
              actions={run && (
                <span className="flex items-center gap-2">
                  <BlockBar pct={(finished(run) / Math.max(1, run.steps.length)) * 100} width={14} />
                  <Button size="xs" variant="ghost" onClick={() => nav(`/runs?ref=${run.ref}`)}>Open run</Button>
                </span>
              )}
              flush
            >
              <div className="divide-y divide-line">
                {p.steps.map((s) => (
                  <div key={s.id} className="flex items-start gap-3 px-3.5 py-2.5">
                    <span className="tnum mt-px w-4 shrink-0 text-right font-mono text-[12px] text-dim">{s.n}</span>
                    <span className="min-w-0 flex-1">
                      <span className="text-[13.5px] font-medium text-ink">{s.label}</span>
                      {s.detail && <span className="block text-[12.5px] text-dim">{s.detail}</span>}
                    </span>
                    <span className="shrink-0 text-right text-[12px] text-soft">{s.agent}</span>
                  </div>
                ))}
              </div>
            </Panel>
          </div>

          {/* Confidence + questions */}
          <div className="mt-4 grid grid-cols-1 gap-3 lg:grid-cols-2">
            <Panel eyebrow="How sure the compiler said it is" title={p.confidence === null ? 'Confidence: not compiled' : `Confidence: ${p.confidence}%`}>
              {p.confidence !== null && (
                <Bar pct={p.confidence} tone={p.confidence >= 85 ? 'ok' : p.confidence >= 70 ? 'warn' : 'danger'} height="h-1.5" />
              )}
              <SectionTitle className="mt-3 mb-1.5">Evidence</SectionTitle>
              <div className="space-y-1">
                {p.compiler ? (
                  <>
                    <KV k="Memory consulted" v={p.cited?.length ? p.cited.join(' · ') : 'nothing matched'} />
                    <KV k="Files named" v={p.affectedFiles.length} />
                    <KV k="Database objects named" v={p.affectedDb.length} />
                    <KV k="Compiled by" v={p.compiler.model} />
                  </>
                ) : (
                  <p className="text-[12.5px] text-soft">
                    Steps written by {workflow ? `the ${workflow} workflow` : 'a workflow'}, not compiled — nothing was read or weighed to write them.
                  </p>
                )}
              </div>
              <SectionTitle className="mt-3 mb-1.5">Unknown</SectionTitle>
              <p className={cn('text-[12.5px]', open > 0 ? 'text-warn' : 'text-dim')}>
                {open > 0
                  ? `${open} open question${open > 1 ? 's' : ''} the compiler would not guess at.`
                  : 'No question was left open.'}
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

          <div className="mt-3 grid grid-cols-1 gap-3 lg:grid-cols-2">
            <Panel eyebrow="What the compiler was handed" title={<span className="flex items-center gap-1.5"><BookOpen className="size-3.5 text-brand" />Grounded in</span>} flush>
              <GroundedIn p={g} />
            </Panel>

            <Panel eyebrow={g.criteriaEdited ? 'Edited by a person · a re-compile keeps them' : 'What done means · proposed by the compiler'}
              title={<span className="flex items-center gap-1.5"><ListChecks className="size-3.5 text-brand" />Acceptance criteria</span>}
              actions={!underway && !editingCriteria && can('plans:decide') ? (
                <Button size="xs" variant="ghost" onClick={() => setCriteriaDraft({ plan: p.ref, text: criteria.join('\n') })}>
                  <Pencil className="size-3" />Edit
                </Button>
              ) : undefined}
              flush>
              {editingCriteria ? (
                <form className="space-y-1.5 px-3.5 py-2.5"
                  onSubmit={(e) => { e.preventDefault(); void saveCriteria(editingCriteria.text); }}>
                  <textarea autoFocus rows={5} value={editingCriteria.text}
                    onChange={(e) => setCriteriaDraft({ plan: p.ref, text: e.target.value })}
                    aria-label="Acceptance criteria, one per line"
                    placeholder="One checkable sentence per line…"
                    className="w-full resize-y rounded-sm border border-line bg-base px-2.5 py-1.5 text-[13px] text-ink placeholder:text-dim focus-visible:border-brand focus-visible:outline-none" />
                  <p className="text-[11.5px] text-dim">One per line, up to 12. Blank lines are dropped.</p>
                  <div className="flex gap-1.5">
                    <Button size="xs" type="submit" disabled={working !== null}><Check className="size-3" />{working === 'criteria' ? 'Saving…' : 'Save criteria'}</Button>
                    <Button size="xs" type="button" variant="ghost" onClick={() => setCriteriaDraft(null)}>Cancel</Button>
                  </div>
                </form>
              ) : criteria.length === 0 ? (
                <p className="px-3.5 py-3 text-[13px] text-dim">
                  {underway ? 'None were set before it was dispatched.' : 'None yet. A compile proposes them; you can write your own before dispatch.'}
                </p>
              ) : (
                <div className="divide-y divide-line">
                  {criteria.map((c, i) => (
                    <div key={c} className="flex items-start gap-2.5 px-3.5 py-1.5">
                      <span className="tnum mt-px w-4 shrink-0 text-right font-mono text-[11.5px] text-dim">{i + 1}</span>
                      <span className="text-[13px] text-ink-2">{c}</span>
                    </div>
                  ))}
                </div>
              )}
            </Panel>
          </div>

          <Panel className="mt-3" eyebrow="Verification" title={<span className="flex items-center gap-1.5"><FlaskConical className="size-3.5 text-brand" />Test plan</span>} flush>
            {p.testPlan.length === 0 && <p className="px-3.5 py-3 text-[13px] text-dim">No test plan was written for this plan.</p>}
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
            {!underway && can('plans:decide') && (
              <span className="flex flex-wrap items-center gap-1.5 text-[12.5px] text-ink-2">
                <label className="flex items-center gap-1.5" title="After tests, checks and review, a second model judges each acceptance criterion against the diff; a miss sends the run back on its own.">
                  <input type="checkbox" checked={goal.on} disabled={criteria.length === 0}
                    onChange={(e) => setGoal({ ...goal, on: e.target.checked })} className="accent-brand" />
                  Run until done
                </label>
                {goal.on && criteria.length > 0 && (
                  <select value={goal.budget} onChange={(e) => setGoal({ ...goal, budget: Number(e.target.value) })}
                    aria-label="Attempts at most"
                    className="h-7 rounded-sm border border-line bg-base px-1.5 text-[12.5px] text-ink focus-visible:border-brand focus-visible:outline-none">
                    {[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{n} attempt{n > 1 ? 's' : ''}</option>)}
                  </select>
                )}
                {criteria.length === 0 && <span className="text-[12px] text-dim">needs acceptance criteria</span>}
              </span>
            )}
            {!underway && open > 0 && (
              <span className="text-[12px] text-warn">Answer or defer {open} open question{open > 1 ? 's' : ''} to dispatch.</span>
            )}
          </div>
        </div>
      </PageBody>
    </Page>
  );
}
