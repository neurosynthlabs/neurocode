import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { ArrowLeft, Bookmark, Compass, Download, FileUp, Loader2, Plus, RotateCcw, Save, Trash2 } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Empty, Field, Page, PageBody, PageHeader, Panel, Segmented, Tag } from '@/components/os';
import { ArchitectureEditor, type Section } from '@/components/blueprints/Editor';
import { Diagram } from '@/components/blueprints/Diagram';
import { Choices, NewBlueprint } from '@/components/blueprints/NewBlueprint';
import { FinalizePanel, ScaffoldPanel } from '@/components/blueprints/Outcome';
import { ReviewPanel } from '@/components/blueprints/Review';
import { TemplateBank } from '@/components/blueprints/TemplateBank';
import { ApiError } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import {
  STATUS_LABEL, STATUS_TONE, blueprintsApi, download, toMermaid, type Answers, type Architecture, type BlueprintDoc,
  type BlueprintRow,
} from '@/lib/live/blueprints';
import { useRemote } from '@/lib/remote';
import { ago } from '@/lib/time';
import { cn } from '@/lib/utils';

/* Blueprints: a system designed before it is built. A person says what they are building and answers a
   short questionnaire, starts from the template that fits (or a blank page), edits the architecture layer
   by layer with a live diagram, may ask a model for a review and accept or reject each proposal, finalizes
   it into a document, a diagram and decisions in Memory, and scaffolds it into a new repository — through
   the normal plan → run → review → signature path, so nothing lands unsigned. */

const DESIGN = 'plans:compile';
const failed = (e: unknown) => (e instanceof ApiError ? e.message : 'The API did not answer.');

export default function Blueprints() {
  const { blueprintId } = useParams();
  return blueprintId ? <BlueprintPage id={blueprintId} /> : <BlueprintList />;
}

/** Reads a file the person picked and hands its text on. */
function useFileImport(onText: (text: string) => void) {
  const input = useRef<HTMLInputElement>(null);
  const element = (
    <input ref={input} type="file" accept=".json,.yaml,.yml,application/json,application/yaml,text/yaml" className="hidden"
      onChange={(e) => {
        const file = e.target.files?.[0];
        e.target.value = '';
        if (!file) return;
        if (file.size > 512 * 1024) { toast.error('That file is larger than 512 KB; a blueprint is a few kilobytes.'); return; }
        void file.text().then(onText);
      }} />
  );
  return { element, open: () => input.current?.click() };
}

/* ── the list and the template bank ───────────────────────────── */
function BlueprintList() {
  const nav = useNavigate();
  const { can } = useAuth();
  const [params, setParams] = useSearchParams();
  const [tab, setTab] = useState<'blueprints' | 'bank'>(params.get('tab') === 'bank' ? 'bank' : 'blueprints');
  const [version, setVersion] = useState(0);
  const catalogue = useRemote(`blueprint-catalogue:${version}`, blueprintsApi.catalogue);
  const list = useRemote(`blueprints:${version}`, () => blueprintsApi.list(200));
  // `n` counts openings: each one mounts a fresh wizard, so nothing typed last time is still there.
  const [wizard, setWizard] = useState<{ open: boolean; from: string | null; n: number }>({ open: params.get('new') === '1', from: null, n: 0 });
  const canDesign = can(DESIGN);

  const importer = useFileImport(async (text) => {
    try {
      const got = await blueprintsApi.importDoc(text);
      if (got.kind === 'blueprint') {
        toast.success(`${got.blueprint.name} imported`);
        nav(`/blueprints/${got.blueprint.id}`);
      } else {
        toast.success(`Template ${got.template.name} imported`);
        setTab('bank');
        setVersion((v) => v + 1);
      }
    } catch (e) {
      toast.error('Not imported', { description: failed(e) });
    }
  });

  const openWizard = (from: string | null) => {
    setWizard((w) => ({ open: true, from, n: w.n + 1 }));
    if (params.get('new')) { params.delete('new'); setParams(params, { replace: true }); }
  };

  return (
    <Page>
      <PageHeader
        title="Blueprints"
        subtitle="Design a system before it is built, then scaffold it."
        actions={<>
          {importer.element}
          <Button size="sm" variant="outline" disabled={!canDesign} onClick={importer.open} title={canDesign ? 'Import a blueprint or template (JSON or YAML)' : 'Needs the plans:compile permission'}>
            <FileUp className="size-3.5" />Import
          </Button>
          <Button size="sm" disabled={!canDesign || !catalogue.data} onClick={() => openWizard(null)} title={canDesign ? undefined : 'Needs the plans:compile permission'}>
            <Plus className="size-3.5" />New blueprint
          </Button>
        </>}
      >
        <Segmented options={[{ id: 'blueprints', label: 'Your blueprints' }, { id: 'bank', label: 'Template bank' }]} value={tab} onChange={setTab} />
      </PageHeader>
      <PageBody>
        {tab === 'blueprints' ? (
          <Rows list={list} canDesign={canDesign} onNew={() => openWizard(null)} onRetry={list.reload} />
        ) : catalogue.error ? (
          <Panel><Empty title="The template bank did not load" hint={catalogue.error} action={<Button size="sm" variant="outline" onClick={catalogue.reload}>Try again</Button>} /></Panel>
        ) : !catalogue.data ? (
          <p className="flex items-center gap-2 text-[13px] text-dim"><Loader2 className="size-4 animate-spin" />Reading the template bank…</p>
        ) : (
          <TemplateBank catalogue={catalogue.data} onUse={(id) => openWizard(id)} onChanged={() => setVersion((v) => v + 1)} />
        )}
      </PageBody>
      {catalogue.data && (
        <NewBlueprint key={wizard.n} open={wizard.open} catalogue={catalogue.data}
          onOpenChange={(open) => {
            setWizard((w) => ({ ...w, open }));
            if (!open && params.get('new')) { params.delete('new'); setParams(params, { replace: true }); }
          }}
          startFrom={wizard.from} onCreated={(bp) => nav(`/blueprints/${bp.id}`)} />
      )}
    </Page>
  );
}

function Rows({ list, canDesign, onNew, onRetry }: {
  list: { data: { items: BlueprintRow[]; total: number } | null; error: string | null; loading: boolean };
  canDesign: boolean; onNew: () => void; onRetry: () => void;
}) {
  const nav = useNavigate();
  if (list.error) {
    return <Panel><Empty title="The blueprints did not load" hint={list.error} action={<Button size="sm" variant="outline" onClick={onRetry}>Try again</Button>} /></Panel>;
  }
  if (!list.data) return <p className="flex items-center gap-2 text-[13px] text-dim"><Loader2 className="size-4 animate-spin" />Reading the blueprints…</p>;
  if (list.data.items.length === 0) {
    return (
      <Panel>
        <Empty icon={<Compass className="size-6" />} title="No blueprint yet"
          action={canDesign && <Button size="sm" onClick={onNew}><Plus className="size-3.5" />Design a new system</Button>} />
      </Panel>
    );
  }
  return (
    <Panel title={`${list.data.total} blueprint${list.data.total === 1 ? '' : 's'}`} flush>
      <div className="divide-y divide-line/60">
        {list.data.items.map((b) => (
          <button key={b.id} onClick={() => nav(`/blueprints/${b.id}`)} className="flex w-full flex-col gap-1.5 px-5 py-4 text-left transition-colors hover:bg-surface-2/60">
            <span className="flex flex-wrap items-center gap-2">
              <span className="text-[14.5px] font-medium text-ink">{b.name}</span>
              <Tag tone={STATUS_TONE[b.status]}>{STATUS_LABEL[b.status]}</Tag>
              {b.reviewed && <Tag tone="info">reviewed</Tag>}
              <span className="ml-auto text-[12px] text-dim">revision {b.revision} · {ago(b.updatedAt)}</span>
            </span>
            {(b.idea || b.summary) && <span className="line-clamp-2 text-[13px] text-soft">{b.idea || b.summary}</span>}
            <span className="flex flex-wrap items-center gap-1.5 text-[12px] text-dim">
              <span>{b.templateName ? `From ${b.templateName}` : 'From a blank page'}</span>
              <span>· {Object.keys(b.layers).length} layers · {b.services} services</span>
              {b.projectName && <span>· scaffolded into {b.projectName}</span>}
              {b.createdBy && <span>· {b.createdBy}</span>}
            </span>
          </button>
        ))}
      </div>
    </Panel>
  );
}

/* ── one blueprint ────────────────────────────────────────────── */
type Tab = 'answers' | 'architecture' | 'review' | 'finalize' | 'scaffold';
const TABS: { id: Tab; label: string }[] = [
  { id: 'answers', label: '1 · Idea and answers' }, { id: 'architecture', label: '2 · Architecture' }, { id: 'review', label: '3 · Review' },
  { id: 'finalize', label: '4 · Finalize' }, { id: 'scaffold', label: '5 · Scaffold' },
];

interface Draft { base: string; name: string; answers: Answers; spec: Architecture }

function BlueprintPage({ id }: { id: string }) {
  const nav = useNavigate();
  const { can, machine } = useAuth();
  const [version, setVersion] = useState(0);
  const got = useRemote(`blueprint:${id}:${version}`, () => blueprintsApi.get(id));
  const catalogue = useRemote('blueprint-catalogue', blueprintsApi.catalogue);
  const [local, setLocal] = useState<BlueprintDoc | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [tab, setTab] = useState<Tab>('architecture');
  const [section, setSection] = useState<Section>('layers');
  const [resets, setResets] = useState(0);
  const [saving, setSaving] = useState(false);
  const [asTemplate, setAsTemplate] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const bp = local && local.id === id ? local : got.data;
  const base = bp ? `${bp.id}:${bp.revision}` : '';
  const current: Draft | null = useMemo(
    () => (bp ? (draft && draft.base === base ? draft : { base, name: bp.name, answers: bp.answers, spec: bp.spec }) : null),
    [bp, draft, base]);
  const dirty = !!(bp && current && (current.name !== bp.name || JSON.stringify(current.answers) !== JSON.stringify(bp.answers)
    || JSON.stringify(current.spec) !== JSON.stringify(bp.spec)));
  const canDesign = can(DESIGN);
  const locked = bp?.status === 'scaffolded';

  // Leaving with unsaved edits asks first.
  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault(); };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);

  const byId = useMemo(() => new Map((catalogue.data?.tech ?? []).map((t) => [t.id, t])), [catalogue.data]);
  const layerLabel = useMemo(() => {
    const m = new Map((catalogue.data?.layers ?? []).map((l) => [l.id, l.label]));
    return (x: string) => m.get(x) ?? x;
  }, [catalogue.data]);
  const liveDiagram = useMemo(() => (current && catalogue.data
    ? toMermaid(current.spec, byId, layerLabel, catalogue.data.layers.map((l) => l.id)) : bp?.diagram ?? ''), [current, catalogue.data, byId, layerLabel, bp]);

  const edit = (fn: (d: Draft) => Draft) => {
    if (!current) return;
    setDraft(fn(current));
  };
  const took = (next: BlueprintDoc) => { setLocal(next); setDraft(null); setResets((r) => r + 1); };

  const save = async () => {
    if (!bp || !current) return;
    setSaving(true);
    try {
      const next = await blueprintsApi.save(bp.id, bp.revision, {
        ...(current.name !== bp.name ? { name: current.name } : {}),
        ...(JSON.stringify(current.answers) !== JSON.stringify(bp.answers) ? { answers: current.answers } : {}),
        ...(JSON.stringify(current.spec) !== JSON.stringify(bp.spec) ? { spec: current.spec } : {}),
      });
      took(next);
      toast.success(`Saved · revision ${next.revision}`);
    } catch (e) {
      const conflict = e instanceof ApiError && e.status === 409;
      toast.error('Not saved', {
        description: failed(e),
        action: conflict ? { label: 'Reload', onClick: () => { setLocal(null); setDraft(null); setVersion((v) => v + 1); } } : undefined,
      });
    } finally {
      setSaving(false);
    }
  };
  const discard = () => { setDraft(null); setResets((r) => r + 1); };

  const exportAs = async (format: 'json' | 'yaml') => {
    if (!bp) return;
    try { download(await blueprintsApi.exportDoc(bp.id, format)); } catch (e) { toast.error('Not exported', { description: failed(e) }); }
  };
  const remove = async () => {
    if (!bp) return;
    try {
      await blueprintsApi.remove(bp.id);
      toast.success(`${bp.name} deleted`);
      nav('/blueprints');
    } catch (e) {
      toast.error('Not deleted', { description: failed(e) });
    }
  };

  if (got.error && !bp) {
    return (
      <Page><PageBody>
        <Empty title="This blueprint did not load" hint={got.error}
          action={<div className="flex gap-2"><Button size="sm" variant="outline" onClick={got.reload}>Try again</Button>
            <Button size="sm" variant="ghost" onClick={() => nav('/blueprints')}>All blueprints</Button></div>} />
      </PageBody></Page>
    );
  }
  if (!bp || !current || !catalogue.data) {
    return (
      <Page><PageBody>
        {catalogue.error
          ? <Empty title="The technology catalogue did not load" hint={catalogue.error} action={<Button size="sm" variant="outline" onClick={catalogue.reload}>Try again</Button>} />
          : <p className="flex items-center gap-2 text-[13px] text-dim"><Loader2 className="size-4 animate-spin" />Reading the blueprint…</p>}
      </PageBody></Page>
    );
  }

  const canScaffold = canDesign && can('projects:onboard') && machine;
  const why = canScaffold
    ? null
    : !machine && can('machine:access')
      ? 'Scaffolding makes folders on the machine the API runs on, and this server opens none: it was started with NEUROCODE_MACHINE_ACCESS=false.'
      : `Scaffolding needs ${[!canDesign && 'plans:compile', !can('projects:onboard') && 'projects:onboard', !can('machine:access') && 'machine:access'].filter(Boolean).join(', ')} — it makes folders on this machine and onboards a project.`;

  return (
    <Page>
      <PageHeader
        title={bp.name}
        subtitle={(bp.answers.idea as string) || bp.spec.summary || undefined}
        actions={<>
          <Button size="sm" variant="ghost" onClick={() => nav('/blueprints')}><ArrowLeft className="size-3.5" />Blueprints</Button>
          <Button size="sm" variant="outline" onClick={() => void exportAs('json')}><Download className="size-3.5" />JSON</Button>
          <Button size="sm" variant="outline" onClick={() => void exportAs('yaml')}><Download className="size-3.5" />YAML</Button>
          <Button size="sm" variant="outline" disabled={!canDesign || dirty} onClick={() => setAsTemplate(true)}
            title={dirty ? 'Save your changes first' : 'Save this architecture as one of your templates'}>
            <Bookmark className="size-3.5" />Save as template
          </Button>
          {!locked && <Button size="sm" variant="ghost" className="text-danger" disabled={!canDesign} onClick={() => setConfirmDelete(true)} aria-label="Delete blueprint"><Trash2 className="size-3.5" /></Button>}
        </>}
      >
        <div className="flex flex-wrap items-center gap-2 text-[12.5px] text-dim">
          <Tag tone={STATUS_TONE[bp.status]}>{STATUS_LABEL[bp.status]}</Tag>
          <span>revision {bp.revision}</span>
          <span>· {bp.templateName ? <>from <Link to="/blueprints?tab=bank" className="hover:text-ink">{bp.templateName}</Link></> : 'from a blank page'}</span>
          <span>· updated {ago(bp.updatedAt)}</span>
          {bp.projectId && <span>· <Link to={`/projects/${bp.projectId}`} className="text-brand hover:underline">{bp.projectName ?? bp.projectId}</Link></span>}
        </div>
        <div className="mt-4"><Segmented options={TABS} value={tab} onChange={setTab} /></div>
      </PageHeader>
      <PageBody>
        {dirty && (
          <div className="sticky top-0 z-10 mb-4 flex flex-wrap items-center gap-3 rounded-xl border border-warn/30 bg-surface px-4 py-2.5 shadow-sm">
            <span className="text-[13px] text-ink-2">Unsaved changes</span>
            <span className="ml-auto flex gap-2">
              <Button size="sm" variant="ghost" onClick={discard}><RotateCcw className="size-3.5" />Discard</Button>
              <Button size="sm" onClick={() => void save()} disabled={saving || !canDesign}>
                {saving ? <Loader2 className="size-3.5 animate-spin" /> : <Save className="size-3.5" />}Save
              </Button>
            </span>
          </div>
        )}
        {locked && tab !== 'scaffold' && (
          <p className="mb-4 rounded-xl bg-surface-2/70 px-4 py-2.5 text-[13px] text-soft">
            Scaffolded into {bp.projectName ?? bp.projectId}: this blueprint is kept as it was built. Save it as a template to design the next version.
          </p>
        )}

        {tab === 'answers' && (
          <div className="mx-auto max-w-3xl space-y-4">
            <Panel title="The idea">
              <div className="space-y-4">
                <Field label="Name" value={current.name} onChange={(v) => edit((d) => ({ ...d, name: v }))} disabled={!canDesign} />
                <label className="block">
                  <span className="mb-1.5 block text-[12.5px] font-medium text-soft">What are you building?</span>
                  <textarea rows={4} value={(current.answers.idea as string) ?? ''} disabled={!canDesign || locked}
                    onChange={(e) => edit((d) => {
                      const answers = { ...d.answers };
                      if (e.target.value) answers.idea = e.target.value; else delete answers.idea;
                      return { ...d, answers };
                    })}
                    className="focus-brand w-full resize-y rounded-lg border border-line bg-surface-2/60 px-3 py-2 text-[13.5px] text-ink focus-visible:outline-none" />
                </label>
              </div>
            </Panel>
            <Panel title="The answers" eyebrow="Every question may be skipped. A review and the document read them.">
              <fieldset disabled={!canDesign || locked} className="space-y-5">
                {catalogue.data.questions.filter((q) => q.kind !== 'text').map((q) => (
                  <Choices key={q.id} q={q} value={current.answers[q.id]}
                    onChange={(v) => edit((d) => { const answers = { ...d.answers }; if (v === undefined) delete answers[q.id]; else answers[q.id] = v; return { ...d, answers }; })} />
                ))}
              </fieldset>
            </Panel>
          </div>
        )}

        {tab === 'architecture' && (
          <div className="flex min-w-0 flex-col gap-5 xl:flex-row xl:items-start">
            <div className="min-w-0 flex-1">
              <ArchitectureEditor key={`${base}:${resets}`} draft={current.spec} catalogue={catalogue.data} readOnly={!canDesign || locked}
                section={section} onSection={setSection} change={(fn) => edit((d) => ({ ...d, spec: fn(d.spec) }))} />
            </div>
            <aside className="min-w-0 xl:sticky xl:top-0 xl:w-[440px] xl:shrink-0">
              <Panel title="Diagram" eyebrow={dirty ? 'Follows your unsaved changes' : 'As saved'}>
                <Diagram text={liveDiagram} />
              </Panel>
              {bp.checks.length > 0 && (
                <Panel title="To fix before finalizing" className="mt-4">
                  <ul className="space-y-1.5">{bp.checks.map((c) => <li key={c.problem} className="text-[13px] text-warn">{c.problem}</li>)}</ul>
                </Panel>
              )}
            </aside>
          </div>
        )}

        {tab === 'review' && <div className="mx-auto max-w-4xl"><ReviewPanel bp={bp} canDesign={canDesign} dirty={dirty} onChanged={took} /></div>}
        {tab === 'finalize' && <div className="mx-auto max-w-4xl"><FinalizePanel bp={bp} canDesign={canDesign} dirty={dirty} onChanged={took} /></div>}
        {tab === 'scaffold' && <div className="mx-auto max-w-3xl"><ScaffoldPanel bp={bp} canScaffold={canScaffold} why={why} dirty={dirty} onChanged={took} /></div>}
      </PageBody>

      <SaveAsTemplate open={asTemplate} onOpenChange={setAsTemplate} bp={bp} />
      <Dialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Delete {bp.name}?</DialogTitle>
            <DialogDescription>The blueprint and its review go. Decisions it wrote into Memory stay there; facts are never deleted.</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setConfirmDelete(false)}>Keep it</Button>
            <Button variant="destructive" onClick={() => void remove()}>Delete</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Page>
  );
}

function SaveAsTemplate({ open, onOpenChange, bp }: { open: boolean; onOpenChange: (o: boolean) => void; bp: BlueprintDoc }) {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [busy, setBusy] = useState(false);
  const save = async () => {
    setBusy(true);
    try {
      const t = await blueprintsApi.saveTemplate(bp.id, name.trim() || bp.name, description.trim());
      toast.success(`Template ${t.name} saved`, { description: 'It is in the template bank under Yours.' });
      onOpenChange(false);
      setName('');
      setDescription('');
    } catch (e) {
      toast.error('Not saved', { description: failed(e) });
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Save as a template</DialogTitle>
          <DialogDescription>The saved architecture becomes a starting point for new blueprints, in the template bank under Yours.</DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <Field label="Name" value={name} onChange={setName} placeholder={bp.name} autoFocus />
          <label className="block">
            <span className="mb-1.5 block text-[12.5px] font-medium text-soft">Description</span>
            <textarea rows={3} value={description} onChange={(e) => setDescription(e.target.value)} placeholder={bp.spec.summary || 'When to start from it.'}
              className="focus-brand w-full resize-y rounded-lg border border-line bg-surface-2/60 px-3 py-2 text-[13.5px] text-ink placeholder:text-dim focus-visible:outline-none" />
          </label>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button onClick={() => void save()} disabled={busy} className={cn(busy && 'opacity-80')}>
            {busy && <Loader2 className="size-3.5 animate-spin" />}Save template
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
