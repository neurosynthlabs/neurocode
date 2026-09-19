import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { AlertTriangle, BookCheck, Download, FolderOpen, FolderPlus, GitBranchPlus, Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Empty, KV, Mono, Panel, Tag } from '@/components/os';
import { FolderPicker } from '@/components/workbench/FolderPicker';
import { ApiError } from '@/lib/api';
import { blueprintsApi, download, type BlueprintDoc } from '@/lib/live/blueprints';
import { ago } from '@/lib/time';
import { Diagram } from './Diagram';

/* What a blueprint becomes. Finalizing writes the architecture document, the diagram, and a decision for
   every key choice into Memory (Decisions, with the blueprint as evidence). Scaffolding makes an empty
   repository per recipe entry in a folder you pick, onboards them as a project, and compiles a plan whose
   requirement is the recipe — the files are written by agents in worktrees and land only after the review
   and your signature. */

const failed = (e: unknown) => (e instanceof ApiError ? e.message : 'The API did not answer.');

export function FinalizePanel({ bp, canDesign, dirty, onChanged }: {
  bp: BlueprintDoc; canDesign: boolean; dirty: boolean; onChanged: (bp: BlueprintDoc) => void;
}) {
  const [busy, setBusy] = useState(false);
  const final = bp.final;
  const locked = bp.status === 'scaffolded';
  const chosen = Object.values(bp.spec.layers).some((l) => l.choice);

  const run = async () => {
    setBusy(true);
    try {
      const next = await blueprintsApi.finalize(bp.id, bp.revision);
      onChanged(next);
      toast.success(`${next.name} finalized`, { description: `${next.final?.facts.length ?? 0} decisions in Memory` });
    } catch (e) {
      toast.error('Not finalized', { description: failed(e) });
    } finally {
      setBusy(false);
    }
  };

  const blockers = [
    ...(!chosen ? ['Choose a technology for at least one layer.'] : []),
    ...bp.checks.map((c) => c.problem),
  ];
  const action = !locked && (
    <Button size="sm" variant={final && !final.stale ? 'outline' : 'default'} onClick={() => void run()}
      disabled={!canDesign || busy || dirty || blockers.length > 0}
      title={!canDesign ? 'Needs the plans:compile permission' : dirty ? 'Save your changes first' : undefined}>
      {busy ? <Loader2 className="size-3.5 animate-spin" /> : <BookCheck className="size-3.5" />}
      {final ? 'Finalize again' : 'Finalize'}
    </Button>
  );

  return (
    <div className="space-y-4">
      {!final ? (
        <Panel>
          <Empty icon={<BookCheck className="size-6" />} title="Not finalized yet"
            hint="Finalizing writes the architecture document and the diagram, and records a decision for every key choice in Memory under Decisions. It needs no model: everything is written from the blueprint."
            action={action} />
          {blockers.length > 0 && <Blockers items={blockers} />}
        </Panel>
      ) : (
        <>
          <Panel title="Architecture document" actions={<>
            <Button size="sm" variant="ghost" onClick={() => download({ filename: `${bp.name.toLowerCase().replace(/[^a-z0-9]+/g, '-')}-architecture.md`, mime: 'text/markdown', text: final.document })}>
              <Download className="size-3.5" />Download .md
            </Button>
            {action}
          </>}>
            <p className="mb-3 text-[12.5px] text-dim">
              Written from revision {final.revision} by {final.by} {ago(final.at)}.
            </p>
            {final.stale && (
              <p className="mb-3 flex items-start gap-2 rounded-lg bg-warn/10 px-3 py-2 text-[12.5px] text-warn">
                <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
                The architecture changed since (it is at revision {bp.revision}). Finalize again before scaffolding.
              </p>
            )}
            {blockers.length > 0 && <Blockers items={blockers} />}
            <pre className="max-h-[520px] overflow-auto rounded-lg bg-base p-4 font-mono text-[12.5px] leading-relaxed whitespace-pre-wrap text-ink-2 ring-1 ring-line/60 ring-inset">{final.document}</pre>
          </Panel>
          <Panel title="Diagram"><Diagram text={final.diagram} /></Panel>
          <Panel title={`Decisions in Memory · ${final.facts.length}`} flush>
            {final.facts.length === 0 ? <p className="px-5 py-5 text-[13px] text-dim">No decision was written.</p> : (
              <div className="flex flex-wrap gap-1.5 px-5 py-4">
                {final.facts.map((ref) => (
                  <Link key={ref} to={`/memory?ref=${ref}`} className="rounded-full border border-line bg-surface-2/60 px-2.5 py-1 font-mono text-[12px] text-brand hover:bg-surface-2">{ref}</Link>
                ))}
              </div>
            )}
          </Panel>
        </>
      )}
    </div>
  );
}

function Blockers({ items }: { items: string[] }) {
  return (
    <ul className="mx-5 mb-4 space-y-1 rounded-lg bg-warn/10 px-3 py-2 text-[12.5px] text-warn">
      {items.map((x) => <li key={x} className="flex items-start gap-2"><AlertTriangle className="mt-0.5 size-3.5 shrink-0" />{x}</li>)}
    </ul>
  );
}

export function ScaffoldPanel({ bp, canScaffold, why, dirty, onChanged }: {
  bp: BlueprintDoc;
  canScaffold: boolean;
  /** Why it cannot, when it cannot: a permission, or machine access. */
  why: string | null;
  dirty: boolean;
  onChanged: (bp: BlueprintDoc) => void;
}) {
  const nav = useNavigate();
  const repos = bp.spec.scaffold.repos;
  const [picking, setPicking] = useState(false);
  const [folder, setFolder] = useState<string | null>(null);
  const [only, setOnly] = useState<string[] | null>(null);
  const [busy, setBusy] = useState(false);
  const chosen = only ?? repos.map((r) => r.label);
  const done = bp.scaffolded;

  if (done) {
    return (
      <Panel title="Scaffolded">
        <KV k="Project" v={<Link to={`/projects/${done.projectId}`} className="text-brand hover:underline">{bp.projectName ?? done.projectId}</Link>} />
        <KV k="Plan" v={<Link to={`/plans?ref=${done.planRef}`} className="font-mono text-brand hover:underline">{done.planRef}</Link>} />
        <KV k="Task" v={<Mono>{done.taskRef}</Mono>} />
        <KV k="Folder" v={done.folder} mono wrap />
        {done.repos.map((r) => <KV key={r.label} k={`Repository ${r.label}`} v={r.path} mono wrap />)}
        <KV k="When" v={`${ago(done.at)} by ${done.by}`} />
        <p className="mt-4 text-[13px] leading-relaxed text-soft">
          The plan waits in Plans. Dispatch it and agents write the files in worktrees; the change lands only after the review and your signature.
        </p>
        <div className="mt-4 flex flex-wrap gap-2">
          <Button size="sm" onClick={() => nav(`/plans?ref=${done.planRef}`)}><GitBranchPlus className="size-3.5" />Open the plan</Button>
          <Button size="sm" variant="outline" onClick={() => nav(`/workbench?project=${done.projectId}`)}><FolderOpen className="size-3.5" />Open in Workbench</Button>
        </div>
      </Panel>
    );
  }

  const ready = bp.status === 'final' && bp.final && !bp.final.stale;
  const go = async () => {
    if (!folder) return;
    setBusy(true);
    try {
      const made = await blueprintsApi.scaffold(bp.id, folder, only ?? undefined);
      onChanged(made.blueprint);
      toast.success(`${made.project.name} created`, { description: `${made.planRef} waits in Plans for you to dispatch it.` });
    } catch (e) {
      toast.error('Not scaffolded', { description: failed(e) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel title="Scaffold into a folder">
      {!ready ? (
        <Empty icon={<FolderPlus className="size-6" />} title="Finalize it first"
          hint="The scaffold is built from the finalized architecture, so it can only follow a finalize of the current revision." />
      ) : repos.length === 0 ? (
        <Empty icon={<FolderPlus className="size-6" />} title="The recipe names no repository"
          hint="Add one in Architecture → Scaffold, save, and finalize again." />
      ) : (
        <div className="space-y-5">
          <p className="text-[13px] leading-relaxed text-soft">
            Pick an empty folder. {repos.length > 1 ? 'Each repository becomes a folder inside it' : 'It becomes the repository'}, with one empty commit so a run can branch —
            no file is written now. A project is onboarded from {repos.length > 1 ? 'them' : 'it'} and a plan is compiled from the recipe; it needs a model.
          </p>
          {repos.length > 1 && (
            <div>
              <span className="mb-2 block text-[12.5px] font-medium text-soft">Repositories</span>
              <div className="flex flex-wrap gap-1.5">
                {repos.map((r) => {
                  const on = chosen.includes(r.label);
                  return (
                    <button key={r.label} type="button" aria-pressed={on}
                      onClick={() => setOnly(on ? chosen.filter((x) => x !== r.label) : [...chosen, r.label])}
                      className={on ? 'rounded-full border border-brand/50 bg-brand/10 px-3 py-1.5 font-mono text-[12.5px] text-ink' : 'rounded-full border border-line px-3 py-1.5 font-mono text-[12.5px] text-soft'}>
                      {r.label} · {r.files.length} files
                    </button>
                  );
                })}
              </div>
            </div>
          )}
          <div className="flex flex-wrap items-center gap-3">
            <Button size="sm" variant="outline" onClick={() => setPicking(true)} disabled={!canScaffold}><FolderOpen className="size-3.5" />{folder ? 'Change folder…' : 'Choose a folder…'}</Button>
            {folder && <span className="min-w-0 font-mono text-[12.5px] break-all text-ink-2">{folder}</span>}
          </div>
          {why && <p className="text-[12.5px] text-warn">{why}</p>}
          <div className="flex flex-wrap items-center gap-3">
            <Button onClick={() => void go()} disabled={!canScaffold || !folder || busy || dirty || chosen.length === 0}>
              {busy ? <Loader2 className="size-4 animate-spin" /> : <FolderPlus className="size-4" />}Scaffold
            </Button>
            {dirty && <Tag tone="warn">Save your changes first</Tag>}
          </div>
        </div>
      )}
      <FolderPicker open={picking} onClose={() => setPicking(false)} title="Where should it go?" confirmLabel="Use this empty folder"
        onPick={(p) => { setFolder(p); setPicking(false); }} />
    </Panel>
  );
}
