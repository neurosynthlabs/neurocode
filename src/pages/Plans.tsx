import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  ArrowDown, FileCode, Database, Boxes, HelpCircle, FlaskConical, Play, Cpu, RefreshCw, Check, Workflow,
  BookOpen, ListChecks, Pencil, Plus, Trash2, ChevronUp, ChevronDown, MessageSquare, Wand2, History, Lock,
  PauseCircle, Loader2, RotateCcw,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Page, PageHeader, PageBody, Panel, Tag, RiskPill, Mono, ListRow, Empty,
  BlockBar, SectionTitle, KV, Bar, Segmented,
} from '@/components/os';
import { inFlight, useData } from '@/lib/data';
import { ApiError, type RunDoc } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { useAccess } from '@/lib/access';
import { agentsApi } from '@/lib/live/agents';
import { instructionsApi } from '@/lib/live/instructions';
import {
  COMMENT_KINDS, newer, plansApi,
  type CommentKind, type PlanComment, type PlanRevision, type ShapedPlan, type StepChange,
} from '@/lib/live/plans';
import { useRemote } from '@/lib/remote';
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
  { k: 'rawRequirement',       label: 'Raw requirement',      hint: 'as typed' },
  { k: 'businessRequirement',  label: 'Business requirement', hint: 'what it means' },
  { k: 'technicalRequirement', label: 'Technical requirement',hint: 'what changes in code' },
] as const;

/** One affected file, and what checking it against the code index said. */
function FileLine({ path, p }: { path: string; p: ShapedPlan }) {
  const check = p.fileCheck;
  const candidates = check?.ambiguous[path];
  const closed = check?.readOnly?.[path];
  return (
    <div className="flex items-center gap-2 px-3.5 py-1.5">
      <span className="min-w-0 flex-1 truncate font-mono text-[12px] text-ink-2" title={path}>{path}</span>
      {closed && <span title={`Agents never write here: ${closed}`}><Tag tone="violet"><Lock className="size-3" />{closed.startsWith('reference') ? 'reference, read only' : 'read only'}</Tag></span>}
      {check?.newFiles.includes(path) && <span title="New file, not in the code index"><Tag tone="brand">new</Tag></span>}
      {candidates && <span title={`Matches ${candidates.join(', ')} in the code index`}><Tag tone="warn">{candidates.length}+ matches</Tag></span>}
    </div>
  );
}

/** What the compiler was handed: instruction files, the team's taste, code and document pieces, remembered facts. */
function GroundedIn({ p }: { p: ShapedPlan }) {
  const grounding = p.grounding ?? [];
  const told = grounding.filter((g) => g.kind === 'instructions');
  const taste = grounding.filter((g) => g.kind === 'taste');
  const pieces = grounding.filter((g) => g.kind === 'code' || g.kind === 'doc');
  const facts = p.cited ?? [];
  if (!p.compiler) {
    return <p className="px-3.5 py-3 text-[13px] text-dim">Written by a workflow, not compiled.</p>;
  }
  if (told.length + taste.length + pieces.length + facts.length === 0) {
    return <p className="px-3.5 py-3 text-[13px] text-dim">Nothing matched; it rests on the requirement alone.</p>;
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
      {group('Taste applied', taste.map((g) => ({ key: g.ref, main: g.ref, sub: 'adopted in Memory → Taste' })))}
      {group('From retrieval', pieces.map((g) => ({ key: g.ref, main: g.ref, sub: g.kind === 'doc' ? 'document' : undefined })))}
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
  const { agents } = useAccess();
  const [workflows, setWorkflows] = useState<Record<string, string>>({});
  const wanted = useSearchParams()[0].get('ref');
  // ?ref= (⌘K) opens a plan, also when this screen is already showing; a click picks another until the link changes.
  const [picked, setPicked] = useState<{ link: string | null; ref: string } | null>(null);
  // Which half of the detail column is shown, under the steps. Not a setting anyone reloads into: it
  // starts on the plan every time, because the plan is what the screen is for.
  const [detailTab, setDetailTab] = useState<'plan' | 'evidence'>('plan');
  const sel = (picked && picked.link === wanted ? picked.ref : null) ?? wanted ?? plans[0]?.ref ?? '';
  // An answer being typed belongs to one plan's question, so opening another plan never shows it.
  const [typing, setTyping] = useState<{ plan: string; index: number; text: string } | null>(null);
  const [working, setWorking] = useState<'dispatch' | 'recompile' | 'criteria' | 'step' | 'comment' | 'revise' | null>(null);
  // Criteria being edited belong to one plan, like an answer being typed.
  const [criteriaDraft, setCriteriaDraft] = useState<{ plan: string; text: string } | null>(null);
  // The plan as the last call answered it. The same document reaches the store on the change stream a moment
  // later; until then — and whichever arrives first — the newer of the two is shown (`newer`, by updatedAt),
  // so an edit never flashes back to what it replaced.
  const [fresh, setFresh] = useState<ShapedPlan | null>(null);
  // A step being edited or added, a step whose removal waits for a second click, and a comment being written:
  // each belongs to one plan, so opening another never shows it.
  const [stepDraft, setStepDraft] = useState<StepDraft | null>(null);
  const [removing, setRemoving] = useState<string | null>(null);
  const [commentDraft, setCommentDraft] = useState<CommentDraft | null>(null);
  // "Run until done": attempts in all, the first included — the API takes 1 to 5.
  const [goal, setGoal] = useState<{ on: boolean; budget: number }>({ on: false, budget: 3 });
  // "Pause before each step": the run waits for an approval between one step and the next.
  const [stepGate, setStepGate] = useState(false);
  const held = useMemo(() => plans.find((x) => x.ref === sel) ?? plans[0], [plans, sel]);
  const p: ShapedPlan | undefined = held && newer(held as ShapedPlan, fresh);
  // Comments are not in the store: read for the open plan, again when it is revised or edited.
  const comments = useRemote(p ? `comments:${p.ref}:${p.revision ?? 1}` : null, () => plansApi.comments(p?.ref ?? ''));
  // A step can be handed to a custom agent too: the subagents that may write files and are not shadowed, the same
  // ones the compiler is offered.
  const custom = useRemote(p ? `custom-agents:${p.projectId}` : null, () => agentsApi.list(p?.projectId ?? null));
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
        <PageHeader title="Plans" subtitle="Requirements, compiled into plans you shape and dispatch." />
        <PageBody><Empty title="No plans yet" hint="Compile a requirement from the Command Center."
          action={<Button size="sm" onClick={() => nav('/')}>Write a requirement</Button>} /></PageBody>
      </Page>
    );
  }

  const g = p;
  const criteria = g.acceptanceCriteria ?? [];
  const shapeable = !inFlight(p) && can('plans:compile');
  const writers = (custom.data?.agents ?? [])
    .filter((a) => a.source !== 'built-in' && !a.shadowedBy && a.mode === 'subagent' && (!a.tools.length || a.tools.includes('edit')))
    .map((a) => a.name);
  const names = Array.from(new Set([...(agents.length ? agents.map((a) => a.name) : p.steps.map((s) => s.agent)), ...writers]));
  const editingStep = stepDraft?.plan === p.ref ? stepDraft : null;
  const writing = commentDraft?.plan === p.ref ? commentDraft : null;
  const thread = comments.data?.items ?? [];
  const openComments = thread.filter((c) => !c.resolved);
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
    else toast.success('Answer recorded', { description: 'Saved to memory as a business rule.' });
  };

  const untilDone = goal.on && criteria.length > 0;
  const dispatch = async (skipQuestions = false) => {
    setWorking('dispatch');
    let ok: boolean;
    if (untilDone || stepGate || skipQuestions) {
      // The store's own dispatch sends no options; the plan and its run arrive on the stream either way.
      try {
        setFresh(await plansApi.dispatch(p.ref, { goalBudget: untilDone ? goal.budget : undefined, stepGate, skipQuestions }));
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
    const paced = stepGate ? ' It waits for your approval before each step.' : '';
    toast.success(`${p.ref} dispatched`, {
      description: untilDone
        ? `${p.taskRef} runs until its acceptance criteria are met, ${goal.budget} attempt${goal.budget > 1 ? 's' : ''} at most. It still stops at your signature.${paced}`
        : `${p.taskRef} is in progress; its run shows in Live runs.${paced}`,
    });
    nav('/tasks');
  };

  /** One shaping call: the answer is shown at once, a refusal is a toast in the server's words. */
  const shape = async (kind: 'step' | 'comment' | 'revise', call: () => Promise<ShapedPlan | null>, failed: string) => {
    setWorking(kind);
    try {
      const doc = await call();
      if (doc) setFresh(doc);
      return true;
    } catch (e) {
      toast.error(failed, { description: e instanceof ApiError ? e.message : 'The local API did not answer.' });
      return false;
    } finally {
      setWorking(null);
    }
  };

  const saveStep = async (draft: StepDraft) => {
    const body = { label: draft.label.trim(), agent: draft.agent, detail: draft.detail.trim() };
    const ok = await shape('step', () => (draft.stepId
      ? plansApi.editStep(p.ref, draft.stepId, body)
      : plansApi.addStep(p.ref, { ...body, at: draft.at ?? undefined })), draft.stepId ? 'Step not saved' : 'Step not added');
    if (ok) setStepDraft(null);
  };

  const moveStep = (index: number, by: -1 | 1) => {
    const order = p.steps.map((s) => s.id);
    const [taken] = order.splice(index, 1);
    order.splice(index + by, 0, taken);
    void shape('step', () => plansApi.reorder(p.ref, order), 'Steps not reordered');
  };

  const removeStep = async (stepId: string) => {
    if (removing !== stepId) { setRemoving(stepId); return; }
    setRemoving(null);
    // A comment on the step it removes is now on no step: read them again so they say so.
    if (await shape('step', () => plansApi.removeStep(p.ref, stepId), 'Step not removed')) comments.reload();
  };

  const saveComment = async (draft: CommentDraft) => {
    const ok = await shape('comment', async () => {
      await plansApi.comment(p.ref, { kind: draft.kind, body: draft.body.trim(), stepId: draft.stepId });
      return null;
    }, 'Comment not saved');
    if (!ok) return;
    setCommentDraft(null);
    comments.reload();
  };

  const resolveComment = async (c: PlanComment, resolved: boolean) => {
    const ok = await shape('comment', async () => { await plansApi.resolve(p.ref, c.id, resolved); return null; },
      resolved ? 'Comment not resolved' : 'Comment not reopened');
    if (ok) comments.reload();
  };

  const revise = async () => {
    let changes: StepChange[] = [];
    const ok = await shape('revise', async () => {
      const doc = await plansApi.revise(p.ref);
      changes = doc.changes;
      return doc;
    }, 'Not revised');
    if (!ok) return;
    const moved = changes.filter((c) => c.op !== 'same').length;
    toast.success(`${p.ref} revised`, {
      description: `Revision ${(p.revision ?? 1) + 1}: ${moved ? `${moved} step${moved === 1 ? '' : 's'} changed` : 'no step changed'}. What changed is under “Revisions”.`,
    });
  };

  const saveCriteria = async (text: string) => {
    setWorking('criteria');
    try {
      const doc = await instructionsApi.setCriteria(p.ref, text.split('\n').map((x) => x.trim()).filter(Boolean));
      setFresh(doc);
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
        subtitle="Requirements, compiled into plans you shape and dispatch."
        actions={<Button size="sm" onClick={() => nav('/tasks')}><Play className="size-3.5" />Open board</Button>}
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
            {(p.revision ?? 1) > 1 && <Tag tone="info"><History className="size-3" />revision {p.revision}</Tag>}
            {p.stepGate && <Tag tone="warn"><PauseCircle className="size-3" />pauses before each step</Tag>}
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
                <Panel title="Architecture impact" className="border-warn/30">
                  <p className="text-[13.5px] leading-relaxed text-ink-2">{p.architectureImpact}</p>
                </Panel>
                <div className="flex justify-center py-1.5"><ArrowDown className="size-3.5 text-line-strong" /></div>
              </>
            )}

            {/* Steps */}
            <Panel
              eyebrow={run ? `${run.ref} · ${finished(run)} of ${run.steps.length} run steps finished`
                : shapeable ? 'Editable until dispatch' : 'Implementation plan'}
              title={`${p.steps.length} step${p.steps.length === 1 ? '' : 's'}, as agreed`}
              actions={run ? (
                <span className="flex items-center gap-2">
                  <BlockBar pct={(finished(run) / Math.max(1, run.steps.length)) * 100} width={14} />
                  <Button size="xs" variant="ghost" onClick={() => nav(`/runs?ref=${run.ref}`)}>Open run</Button>
                </span>
              ) : shapeable && !editingStep ? (
                <Button size="xs" variant="ghost" disabled={working !== null}
                  onClick={() => setStepDraft({ plan: p.ref, stepId: null, label: '', agent: names[0] ?? '', detail: '', at: null })}>
                  <Plus className="size-3" />Add step
                </Button>
              ) : undefined}
              flush
            >
              <div className="divide-y divide-line">
                {p.steps.map((s, i) => editingStep?.stepId === s.id ? (
                  <StepEditor key={s.id} draft={editingStep} names={names} busy={working === 'step'}
                    onChange={setStepDraft} onSave={saveStep} onCancel={() => setStepDraft(null)} />
                ) : (
                  <div key={s.id} className="group">
                    <div className="flex items-start gap-3 px-3.5 py-2.5">
                      <span className="tnum mt-px w-4 shrink-0 text-right font-mono text-[12px] text-dim">{s.n}</span>
                      <span className="min-w-0 flex-1">
                        <span className="text-[13.5px] font-medium text-ink">{s.label}</span>
                        {s.detail && <span className="block text-[12.5px] text-dim">{s.detail}</span>}
                        {openComments.some((c) => c.stepId === s.id) && (
                          <span className="mt-1 flex flex-wrap gap-1">
                            {openComments.filter((c) => c.stepId === s.id).map((c) => (
                              <span key={c.id} title={c.body}><Tag tone={KIND_TONE[c.kind]}><MessageSquare className="size-3" />{kindLabel(c.kind)}</Tag></span>
                            ))}
                          </span>
                        )}
                      </span>
                      <span className="shrink-0 text-right text-[12px] text-soft">{s.agent}</span>
                    </div>
                    {shapeable && (
                      <div className="-mt-1.5 flex flex-wrap items-center gap-0.5 px-3.5 pb-2 pl-10 opacity-100 transition-opacity md:opacity-0 md:group-hover:opacity-100 md:focus-within:opacity-100">
                        <Button size="icon-xs" variant="ghost" aria-label={`Move step ${s.n} up`} disabled={i === 0 || working !== null} onClick={() => moveStep(i, -1)}><ChevronUp /></Button>
                        <Button size="icon-xs" variant="ghost" aria-label={`Move step ${s.n} down`} disabled={i === p.steps.length - 1 || working !== null} onClick={() => moveStep(i, 1)}><ChevronDown /></Button>
                        <Button size="xs" variant="ghost" disabled={working !== null}
                          onClick={() => setStepDraft({ plan: p.ref, stepId: s.id, label: s.label, agent: s.agent, detail: s.detail, at: null })}>
                          <Pencil />Edit
                        </Button>
                        <Button size="xs" variant="ghost" disabled={working !== null}
                          onClick={() => setCommentDraft({ plan: p.ref, stepId: s.id, kind: 'comment', body: '' })}>
                          <MessageSquare />Comment
                        </Button>
                        <Button size="xs" variant="ghost" disabled={working !== null}
                          onClick={() => setStepDraft({ plan: p.ref, stepId: null, label: '', agent: s.agent, detail: '', at: s.n + 1 })}>
                          <Plus />Add after
                        </Button>
                        <Button size="xs" variant={removing === s.id ? 'destructive' : 'ghost'} disabled={working !== null || p.steps.length <= 1}
                          onClick={() => void removeStep(s.id)} onBlur={() => setRemoving(null)}>
                          <Trash2 />{removing === s.id ? 'Remove?' : 'Remove'}
                        </Button>
                      </div>
                    )}
                    {writing?.stepId === s.id && (
                      <CommentComposer draft={writing} steps={p.steps} busy={working === 'comment'}
                        onChange={setCommentDraft} onSave={saveComment} onCancel={() => setCommentDraft(null)} />
                    )}
                    {editingStep && !editingStep.stepId && editingStep.at === s.n + 1 && (
                      <StepEditor draft={editingStep} names={names} busy={working === 'step'}
                        onChange={setStepDraft} onSave={saveStep} onCancel={() => setStepDraft(null)} />
                    )}
                  </div>
                ))}
                {editingStep && !editingStep.stepId && (editingStep.at === null || editingStep.at > p.steps.length + 1) && (
                  <StepEditor draft={editingStep} names={names} busy={working === 'step'}
                    onChange={setStepDraft} onSave={saveStep} onCancel={() => setStepDraft(null)} />
                )}
              </div>
            </Panel>
          </div>

          {/* Twelve panels one under the other put the dispatch bar four screens below the steps. Nothing
              here is cut: what the decision needs — how sure it is, what it still asks, what done means, and
              what was said about it — stays under Plan, and what a person reads once, about where the plan
              came from, moves one click away. Both groups hold the same panels, word for word. */}
          <div className="mt-4">
            <Segmented
              options={[{ id: 'plan', label: 'Plan' }, { id: 'evidence', label: 'Evidence & history' }]}
              value={detailTab}
              onChange={setDetailTab}
            />
          </div>

          {detailTab === 'plan' && (
            <>
            <div className="mt-4 grid grid-cols-1 gap-3 lg:grid-cols-2">
              <Panel title={p.confidence === null ? 'Confidence: not compiled' : `Confidence: ${p.confidence}%`}>
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
                      Written by {workflow ? `the ${workflow} workflow` : 'a workflow'}, not compiled.
                    </p>
                  )}
                </div>
                <SectionTitle className="mt-3 mb-1.5">Unknown</SectionTitle>
                <p className={cn('text-[12.5px]', open > 0 ? 'text-warn' : 'text-dim')}>
                  {open > 0
                    ? `${open} open question${open > 1 ? 's' : ''} left to you.`
                    : 'No question was left open.'}
                </p>
              </Panel>

              <Panel title={<span className="flex items-center gap-1.5"><HelpCircle className="size-3.5 text-warn" />Open questions</span>} flush>
                {open === 0 ? (
                  <Empty title="No open questions" />
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

              <div className="mt-3">
              <Panel eyebrow={g.criteriaEdited ? 'Edited · kept on re-compile' : 'Proposed by the compiler'}
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
                    <p className="text-[11.5px] text-dim">One per line, up to 12.</p>
                    <div className="flex gap-1.5">
                      <Button size="xs" type="submit" disabled={working !== null}><Check className="size-3" />{working === 'criteria' ? 'Saving…' : 'Save criteria'}</Button>
                      <Button size="xs" type="button" variant="ghost" onClick={() => setCriteriaDraft(null)}>Cancel</Button>
                    </div>
                  </form>
                ) : criteria.length === 0 ? (
                  <p className="px-3.5 py-3 text-[13px] text-dim">
                    {underway ? 'None were set before dispatch.' : 'None yet. Write your own before dispatch.'}
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

              <div className="mt-3">
              <CommentsPanel
                p={p} shapeable={shapeable} loading={comments.loading} error={comments.error} onRetry={comments.reload}
                thread={thread} writing={writing?.stepId === null ? writing : null} working={working}
                onWrite={() => setCommentDraft({ plan: p.ref, stepId: null, kind: 'comment', body: '' })}
                onChange={setCommentDraft} onSave={saveComment} onCancel={() => setCommentDraft(null)}
                onResolve={resolveComment} onRevise={revise}
              />
              </div>
            </>
          )}

          {detailTab === 'evidence' && (
            <>
              <div className="mt-4 grid grid-cols-1 gap-3 lg:grid-cols-2">
              <Panel title={<span className="flex items-center gap-1.5"><BookOpen className="size-3.5 text-brand" />Grounded in</span>} flush
                about="What the compiler was handed: instruction files, taste, code, documents and memory facts.">
                <GroundedIn p={g} />
              </Panel>

              <RevisionsPanel p={p} />
              </div>

            <Panel className="mt-3" title={<span className="flex items-center gap-1.5"><FlaskConical className="size-3.5 text-brand" />Test plan</span>} flush>
              {p.testPlan.length === 0 && <p className="px-3.5 py-3 text-[13px] text-dim">No test plan was written.</p>}
              <div className="divide-y divide-line">
                {p.testPlan.map((t, i) => (
                  <div key={i} className="flex items-start gap-2.5 px-3.5 py-1.5">
                    <span className="tnum mt-px w-4 shrink-0 text-right font-mono text-[11.5px] text-dim">{i + 1}</span>
                    <span className="text-[13px] text-ink-2">{t}</span>
                  </div>
                ))}
              </div>
            </Panel>
            </>
          )}

          <div className="mt-4 flex flex-wrap items-center gap-2">
            <Button size="sm" disabled={underway || open > 0 || working !== null} onClick={() => void dispatch()}>
              <Play className="size-3.5" />{underway ? 'Dispatched' : working === 'dispatch' ? 'Dispatching…' : 'Dispatch plan'}
            </Button>
            {!underway && open > 0 && can('plans:decide') && (
              <Button size="sm" variant="outline" disabled={working !== null} onClick={() => void dispatch(true)}
                title={`Defers ${open === 1 ? 'the open question' : `all ${open} open questions`} on the record, then starts`}>
                <Play className="size-3.5" />Skip and start
              </Button>
            )}
            {!underway && open > 0 && (
              <span className="text-[12.5px] text-dim">{open === 1 ? '1 question is open' : `${open} questions are open`}: answer above, or skip.</span>
            )}
            <Button size="sm" variant="outline" onClick={() => nav('/architecture')}>See impact</Button>
            <Button size="sm" variant="ghost" disabled={underway || working !== null} onClick={again}>
              <RefreshCw className={cn('size-3.5', working === 'recompile' && 'animate-spin')} />
              {working === 'recompile' ? 'Re-compiling…' : 'Re-compile'}
            </Button>
            {!underway && can('plans:decide') && (
              <span className="flex flex-wrap items-center gap-1.5 text-[12.5px] text-ink-2">
                <label className="flex items-center gap-1.5" title="A second model judges each criterion; a miss sends the run back.">
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
                <label className="ml-2 flex items-center gap-1.5" title="Waits for your approval between steps">
                  <input type="checkbox" checked={stepGate} onChange={(e) => setStepGate(e.target.checked)} className="accent-brand" />
                  Pause before each step
                </label>
              </span>
            )}
            {!underway && open > 0 && (
              <span className="text-[12px] text-warn">Answer or defer {open} open question{open > 1 ? 's' : ''} to dispatch.</span>
            )}
            {!underway && openComments.length > 0 && (
              <span className="text-[12px] text-dim">{openComments.length} open comment{openComments.length > 1 ? 's' : ''} — revise to fold {openComments.length > 1 ? 'them' : 'it'} in, or resolve.</span>
            )}
          </div>
        </div>
      </PageBody>
    </Page>
  );
}

/* ── Shaping a plan before dispatch ─────────────────────────────── */

interface StepDraft {
  plan: string;
  /** Null for a new step. */
  stepId: string | null;
  label: string;
  agent: string;
  detail: string;
  /** Where a new step goes, 1 for first; null for last. */
  at: number | null;
}

interface CommentDraft {
  plan: string;
  /** Null for a comment on the whole plan. */
  stepId: string | null;
  kind: CommentKind;
  body: string;
}

const KIND_TONE: Record<CommentKind, 'neutral' | 'info' | 'danger' | 'violet' | 'warn'> = {
  comment: 'neutral', split: 'info', remove: 'danger', why: 'violet', risky: 'warn',
};
const kindLabel = (kind: CommentKind) => COMMENT_KINDS.find((k) => k.id === kind)?.label ?? kind;

const FIELD = 'w-full rounded-sm border border-line bg-base px-2.5 py-1.5 text-[13px] text-ink placeholder:text-dim focus-visible:border-brand focus-visible:outline-none';

/** A step's label, owner and detail, as a form: editing one, or writing a new one. */
function StepEditor({ draft, names, busy, onChange, onSave, onCancel }: {
  draft: StepDraft; names: string[]; busy: boolean;
  onChange: (next: StepDraft) => void; onSave: (d: StepDraft) => void; onCancel: () => void;
}) {
  const owners = names.includes(draft.agent) || !draft.agent ? names : [draft.agent, ...names];
  return (
    <form className="space-y-1.5 bg-surface-2/40 px-3.5 py-2.5"
      onSubmit={(e) => { e.preventDefault(); if (draft.label.trim()) onSave(draft); }}>
      <p className="text-[12px] font-medium text-dim">
        {draft.stepId ? 'Edit this step' : draft.at ? `New step at ${draft.at}` : 'New step at the end'}
      </p>
      <div className="flex flex-col gap-1.5 sm:flex-row">
        <input autoFocus value={draft.label} maxLength={200} aria-label="Step"
          onChange={(e) => onChange({ ...draft, label: e.target.value })}
          placeholder="What this step does" className={cn(FIELD, 'min-w-0 flex-1')} />
        <select value={draft.agent} aria-label="Owner" onChange={(e) => onChange({ ...draft, agent: e.target.value })}
          className={cn(FIELD, 'h-[34px] sm:w-52')}>
          {owners.map((n) => <option key={n} value={n}>{n}</option>)}
        </select>
      </div>
      <textarea rows={2} value={draft.detail} maxLength={2000} aria-label="Detail"
        onChange={(e) => onChange({ ...draft, detail: e.target.value })}
        placeholder="Detail for the agent (optional)" className={cn(FIELD, 'resize-y')} />
      <div className="flex gap-1.5">
        <Button size="xs" type="submit" disabled={busy || !draft.label.trim() || !draft.agent}>
          {busy ? <Loader2 className="animate-spin" /> : <Check />}{draft.stepId ? 'Save step' : 'Add step'}
        </Button>
        <Button size="xs" type="button" variant="ghost" onClick={onCancel}>Cancel</Button>
      </div>
    </form>
  );
}

/** Writing a comment: its kind, and the words. On a step, or on the whole plan. */
function CommentComposer({ draft, steps, busy, onChange, onSave, onCancel }: {
  draft: CommentDraft; steps: Plan['steps']; busy: boolean;
  onChange: (next: CommentDraft) => void; onSave: (d: CommentDraft) => void; onCancel: () => void;
}) {
  const step = steps.find((s) => s.id === draft.stepId);
  // Split and remove are about one step; on the whole plan they have nothing to act on.
  const kinds = COMMENT_KINDS.filter((k) => step || (k.id !== 'split' && k.id !== 'remove'));
  return (
    <form className="space-y-1.5 px-3.5 py-2.5 md:pl-10"
      onSubmit={(e) => { e.preventDefault(); if (draft.body.trim()) onSave(draft); }}>
      <div className="flex flex-wrap items-center gap-1" role="radiogroup" aria-label="Kind of comment">
        {kinds.map((k) => (
          <button key={k.id} type="button" role="radio" aria-checked={draft.kind === k.id} title={k.hint}
            onClick={() => onChange({ ...draft, kind: k.id })}
            className={cn('rounded-sm border px-2 py-0.5 text-[12px] transition-colors',
              draft.kind === k.id ? 'border-brand bg-brand/10 font-medium text-brand' : 'border-line bg-surface text-soft hover:text-ink-2')}>
            {k.label}
          </button>
        ))}
        <span className="ml-1 text-[11.5px] text-dim">{step ? `on step ${step.n}` : 'on the whole plan'}</span>
      </div>
      <textarea autoFocus rows={2} value={draft.body} maxLength={4000} aria-label="Comment"
        onChange={(e) => onChange({ ...draft, body: e.target.value })}
        placeholder={COMMENT_KINDS.find((k) => k.id === draft.kind)?.hint + ' — say what, and why…'}
        className={cn(FIELD, 'resize-y')} />
      <div className="flex gap-1.5">
        <Button size="xs" type="submit" disabled={busy || !draft.body.trim()}>
          {busy ? <Loader2 className="animate-spin" /> : <MessageSquare />}Add comment
        </Button>
        <Button size="xs" type="button" variant="ghost" onClick={onCancel}>Cancel</Button>
      </div>
    </form>
  );
}

/** Comments on the plan, and the one button that hands the open ones to the compiler. */
function CommentsPanel({
  p, shapeable, loading, error, onRetry, thread, writing, working, onWrite, onChange, onSave, onCancel, onResolve, onRevise,
}: {
  p: ShapedPlan; shapeable: boolean; loading: boolean; error: string | null; onRetry: () => void;
  thread: PlanComment[]; writing: CommentDraft | null; working: string | null;
  onWrite: () => void; onChange: (d: CommentDraft) => void; onSave: (d: CommentDraft) => void; onCancel: () => void;
  onResolve: (c: PlanComment, resolved: boolean) => void; onRevise: () => void;
}) {
  const [showResolved, setShowResolved] = useState(false);
  const open = thread.filter((c) => !c.resolved);
  const resolved = thread.filter((c) => c.resolved);
  const row = (c: PlanComment) => (
    <div key={c.id} className="px-3.5 py-2.5">
      <div className="flex flex-wrap items-center gap-1.5">
        <Tag tone={KIND_TONE[c.kind]}>{kindLabel(c.kind)}</Tag>
        <span className="text-[12px] text-dim">{c.step ? `step ${c.step.n} · ${c.step.label}` : 'the whole plan'}</span>
        <span className="ml-auto text-[11.5px] text-dim">{c.by ?? 'someone'} · {ago(c.createdAt)}{c.revision !== (p.revision ?? 1) ? ` · revision ${c.revision}` : ''}</span>
      </div>
      <p className={cn('mt-1 text-[13px] [overflow-wrap:anywhere]', c.resolved ? 'text-soft' : 'text-ink-2')}>{c.body}</p>
      {c.reply && <p className="mt-1 border-l-2 border-line pl-2 text-[12.5px] text-soft"><span className="text-dim">Compiler: </span>{c.reply}</p>}
      {shapeable && (
        <Button size="xs" variant="ghost" className="mt-1 -ml-2" disabled={working !== null} onClick={() => onResolve(c, !c.resolved)}>
          {c.resolved ? <><RotateCcw />Reopen</> : <><Check />Resolve</>}
        </Button>
      )}
    </div>
  );
  return (
    <Panel
      eyebrow={open.length ? `${open.length} open` : 'For the next revision'}
      about="Revising hands open comments to the compiler. It writes the next revision and answers each."
      title={<span className="flex items-center gap-1.5"><MessageSquare className="size-3.5 text-brand" />Comments</span>}
      actions={shapeable && !writing ? <Button size="xs" variant="ghost" disabled={working !== null} onClick={onWrite}><Plus className="size-3" />Comment on plan</Button> : undefined}
      flush
    >
      {writing && <CommentComposer draft={writing} steps={p.steps} busy={working === 'comment'} onChange={onChange} onSave={onSave} onCancel={onCancel} />}
      {loading && thread.length === 0 ? (
        <p className="flex items-center gap-2 px-3.5 py-3 text-[13px] text-dim"><Loader2 className="size-3.5 animate-spin" />Reading the comments…</p>
      ) : error ? (
        <div className="flex items-center gap-2 px-3.5 py-3 text-[13px] text-danger">
          <span className="min-w-0 flex-1">The comments did not load: {error}</span>
          <Button size="xs" variant="outline" onClick={onRetry}>Try again</Button>
        </div>
      ) : thread.length === 0 && !writing ? (
        <p className="px-3.5 py-3 text-[13px] text-dim">
          {shapeable
            ? 'No comments yet. Leave one on a step or the whole plan.'
            : 'No comments.'}
        </p>
      ) : (
        <div className="divide-y divide-line">
          {open.map(row)}
          {resolved.length > 0 && (
            <button type="button" onClick={() => setShowResolved(!showResolved)}
              className="flex w-full items-center gap-1.5 px-3.5 py-2 text-left text-[12px] text-dim hover:text-ink-2">
              {showResolved ? <ChevronUp className="size-3" /> : <ChevronDown className="size-3" />}
              {resolved.length} resolved
            </button>
          )}
          {showResolved && resolved.map(row)}
        </div>
      )}
      {shapeable && (
        <div className="flex flex-wrap items-center gap-2 border-t border-line px-3.5 py-2.5">
          <Button size="sm" variant="outline" disabled={open.length === 0 || working !== null} onClick={onRevise}>
            {working === 'revise' ? <Loader2 className="animate-spin" /> : <Wand2 />}
            {working === 'revise' ? 'Revising…' : `Revise with comments${open.length ? ` (${open.length})` : ''}`}
          </Button>
          <span className="text-[11.5px] text-dim">Needs a model. Keeps answers and your criteria.</span>
        </div>
      )}
    </Panel>
  );
}

const OP_TONE: Record<StepChange['op'], 'ok' | 'danger' | 'warn' | 'info' | 'neutral'> = {
  added: 'ok', removed: 'danger', changed: 'warn', moved: 'info', same: 'neutral',
};

/** Every earlier revision, and what changed from it into the next: step by step, with the comments it answered. */
function RevisionsPanel({ p }: { p: ShapedPlan }) {
  const revisions = p.revisions ?? [];
  const [picked, setPicked] = useState<{ plan: string; revision: number } | null>(null);
  const [showSame, setShowSame] = useState(false);
  const chosen: PlanRevision | undefined = revisions.find((r) => picked?.plan === p.ref && r.revision === picked.revision)
    ?? revisions[revisions.length - 1];
  if (!chosen) {
    return (
      <Panel title={<span className="flex items-center gap-1.5"><History className="size-3.5 text-brand" />Revisions</span>}>
        <p className="text-[13px] text-dim">This is the first revision.</p>
      </Panel>
    );
  }
  const into = chosen.revision + 1;
  const changes = chosen.changes.filter((c) => showSame || c.op !== 'same');
  const same = chosen.changes.length - chosen.changes.filter((c) => c.op !== 'same').length;
  return (
    <Panel
      eyebrow={`${chosen.by} · ${ago(chosen.at)} · ${chosen.model}`}
      title={<span className="flex items-center gap-1.5"><History className="size-3.5 text-brand" />Revision {chosen.revision} → {into}</span>}
      actions={revisions.length > 1 ? (
        <select value={chosen.revision} aria-label="Revision"
          onChange={(e) => setPicked({ plan: p.ref, revision: Number(e.target.value) })}
          className="h-7 rounded-sm border border-line bg-base px-1.5 text-[12.5px] text-ink focus-visible:border-brand focus-visible:outline-none">
          {revisions.map((r) => <option key={r.revision} value={r.revision}>{r.revision} → {r.revision + 1}</option>)}
        </select>
      ) : undefined}
      flush
    >
      {chosen.summary && <p className="px-3.5 py-2.5 text-[13px] text-ink-2">{chosen.summary}</p>}
      <div className="divide-y divide-line border-t border-line">
        {changes.map((c, i) => (
          <div key={`${c.op}-${c.was ?? 'x'}-${c.n ?? 'x'}-${i}`} className="flex items-start gap-2.5 px-3.5 py-1.5">
            <span className="w-16 shrink-0"><Tag tone={OP_TONE[c.op]}>{c.op}</Tag></span>
            <span className="min-w-0 flex-1 text-[13px]">
              <span className={cn(c.op === 'removed' ? 'text-dim line-through' : 'text-ink-2')}>{c.label}</span>
              <span className="text-dim"> · {c.agent}</span>
              {c.before && c.before.label !== c.label && <span className="block text-[12px] text-dim">was “{c.before.label}”</span>}
              {c.before && c.before.agent !== c.agent && <span className="block text-[12px] text-dim">owner was {c.before.agent}</span>}
              {c.before && c.before.detail !== c.detail && c.before.label === c.label && <span className="block text-[12px] text-dim">detail reworded</span>}
            </span>
            <span className="tnum shrink-0 font-mono text-[11.5px] text-dim">
              {c.was ?? '·'}→{c.n ?? '·'}
            </span>
          </div>
        ))}
        {same > 0 && (
          <button type="button" onClick={() => setShowSame(!showSame)} className="w-full px-3.5 py-2 text-left text-[12px] text-dim hover:text-ink-2">
            {showSame ? 'Hide' : 'Show'} {same} unchanged step{same === 1 ? '' : 's'}
          </button>
        )}
      </div>
      {chosen.comments.length > 0 && (
        <div className="space-y-1.5 border-t border-line px-3.5 py-2.5">
          <p className="text-[12px] font-medium text-dim">The comments it answered</p>
          {chosen.comments.map((c) => (
            <div key={c.id} className="text-[12.5px]">
              <span className="text-ink-2"><Tag tone={KIND_TONE[c.kind]}>{kindLabel(c.kind)}</Tag> {c.body}</span>
              {c.reply && <span className="block pl-1 text-soft">→ {c.reply}</span>}
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}
