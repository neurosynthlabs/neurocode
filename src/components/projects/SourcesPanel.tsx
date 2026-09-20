import { useEffect, useState, type SyntheticEvent } from 'react';
import { ArrowDown, ArrowUp, Check, FolderOpen, Layers, Loader2, Pencil, Plus, RefreshCw, Trash2, X } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Dot, Empty, Field, Panel, Segmented, Tag } from '@/components/os';
import { FolderPicker } from '@/components/workbench/FolderPicker';
import { ApiError, api } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import {
  LABEL, SOURCE_DOT, labelFrom, readOnly, sourcesApi, sourcesOf, type ProjectSource, type SourceInput, type SourceRole,
} from '@/lib/live/sources';
import { cn } from '@/lib/utils';
import { useRemote } from '@/lib/remote';
import type { Project } from '@/types';

const reason = (e: unknown) => (e instanceof ApiError ? e.message : 'The local API did not answer. Is it still running?');
const NEW: SourceInput = { label: '', kind: 'git', repo: '', branch: 'main', role: 'code' };

/** Code is worked on; a reference is read for search and grounding and never written — two plain buttons, so a
    choice inside a form never submits it. */
export function RolePick({ value, onChange, label, disabled }: { value: SourceRole; onChange: (r: SourceRole) => void; label: string; disabled?: boolean }) {
  return (
    <span role="radiogroup" aria-label={label} className="inline-flex rounded-lg bg-surface-2 p-[3px] ring-1 ring-line/60 ring-inset">
      {(['code', 'reference'] as const).map((r) => (
        <button key={r} type="button" role="radio" aria-checked={value === r} disabled={disabled} onClick={() => onChange(r)}
          className={cn('rounded-md px-2.5 py-1 text-[12px] transition-colors disabled:opacity-60',
            value === r ? 'seg-thumb font-medium text-ink' : 'text-soft hover:text-ink-2')}>
          {r === 'code' ? 'Code' : 'Reference'}
        </button>
      ))}
    </span>
  );
}
const STATUS_WORD = { active: 'ready', onboarding: 'onboarding', failed: 'failed' } as const;

/**
 * A project's sources: the folder or repository it was onboarded from, then every further one, in the
 * order a person arranged them. Each is read, measured and indexed into the one project; its files
 * appear there under its label. Adding, moving, renaming, re-indexing and removing need projects:onboard.
 */
export function SourcesPanel({ p }: { p: Project }) {
  const { can, machine } = useAuth();
  const [version, setVersion] = useState(0);
  const summary = JSON.stringify(sourcesOf(p));
  const list = useRemote(p.source ? `sources:${p.id}:${summary}:${version}` : null, () => sourcesApi.list(p.id));
  const sources = list.data ?? [];
  const reload = () => setVersion((v) => v + 1);
  const mayChange = can('projects:onboard');
  const browse = machine;

  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState<SourceInput>(NEW);
  const [saving, setSaving] = useState(false);
  const [picking, setPicking] = useState(false);
  const [renaming, setRenaming] = useState<{ id: number; label: string } | null>(null);
  const [armed, setArmed] = useState<number | null>(null);
  const [busy, setBusy] = useState<number | 'first' | null>(null);

  // A source being onboarded changes on its own: the list is read again every few seconds until none is.
  const reading = sources.some((x) => x.status === 'onboarding');
  useEffect(() => {
    if (!reading) return;
    const timer = window.setInterval(() => setVersion((v) => v + 1), 3000);
    return () => window.clearInterval(timer);
  }, [reading]);

  const taken = sources.map((x) => x.label);
  const label = draft.label.trim();
  const problem = !draft.repo.trim() ? 'Enter a clone URL or a folder'
    : draft.kind === 'local' && !/^(\/|~\/)/.test(draft.repo.trim()) ? 'Use an absolute path — it starts with / or ~/'
      : !label ? 'Give it a label' : !LABEL.test(label) ? 'A label is lower-case letters, digits, dots, dashes or underscores'
        : taken.includes(label) ? `${label} is already a source of ${p.name}` : null;

  const act = async (what: () => Promise<unknown>, done: string, failed: string, detail?: string) => {
    try {
      await what();
      toast.success(done, detail ? { description: detail } : undefined);
      reload();
      return true;
    } catch (e) {
      toast.error(failed, { description: reason(e) });
      return false;
    }
  };

  const add = async (e: SyntheticEvent) => {
    e.preventDefault();
    if (problem) return;
    setSaving(true);
    const ok = await act(() => sourcesApi.add(p.id, { ...draft, label, repo: draft.repo.trim(), branch: draft.branch.trim() }),
      `${label} is onboarding`, `${label} was not added`,
      `${draft.kind === 'git' ? 'Cloning' : 'Reading'} it now; the whole project is indexed again with it. Each stage lands in Activity.`);
    setSaving(false);
    if (ok) {
      setAdding(false);
      setDraft(NEW);
    }
  };

  const move = (s: ProjectSource, by: -1 | 1) => {
    const extras = sources.filter((x) => !x.primary);
    const at = extras.findIndex((x) => x.id === s.id) + by;
    void act(() => sourcesApi.edit(p.id, s.id as number, { position: at }), `${s.label} moved`, `${s.label} was not moved`);
  };

  const rename = async (e: SyntheticEvent) => {
    e.preventDefault();
    if (!renaming) return;
    const was = sources.find((x) => x.id === renaming.id);
    const next = renaming.label.trim();
    if (!was || next === was.label) { setRenaming(null); return; }
    if (!LABEL.test(next)) { toast.error('Not a label', { description: 'A label is lower-case letters, digits, dots, dashes or underscores.' }); return; }
    const ok = await act(() => sourcesApi.edit(p.id, renaming.id, { label: next }), `${was.label} is now ${next}`,
      `${was.label} was not renamed`, 'Its files are indexed again under the new label.');
    if (ok) setRenaming(null);
  };

  const setRole = (s: ProjectSource, role: SourceRole) => {
    if ((s.role ?? 'code') === role) return;
    void act(() => sourcesApi.edit(p.id, s.id as number, { role }),
      role === 'reference' ? `${s.label} is now a reference` : `${s.label} is code again`,
      `${s.label} was not changed`,
      role === 'reference' ? 'It stays indexed and read for grounding; agents no longer write there.' : 'Agents may change it again in their worktrees.');
  };

  const reindex = async (s: ProjectSource) => {
    setBusy(s.id ?? 'first');
    await act(() => (s.primary ? api.code.reindex(p.id) : sourcesApi.reindex(p.id, s.id as number)),
      s.status === 'failed' ? `Onboarding ${s.label} again` : `Reading ${s.primary ? p.name : s.label} again`,
      'Not re-indexed', 'The index refreshes on its own when it is ready.');
    setBusy(null);
  };

  const remove = async (s: ProjectSource) => {
    if (armed !== s.id) { setArmed(s.id); return; }
    setArmed(null);
    await act(() => sourcesApi.remove(p.id, s.id as number), `${s.label} removed from ${p.name}`, `${s.label} was not removed`,
      'Its files left the index. The folder itself was not touched.');
  };

  return (
    <Panel flush eyebrow="Folders and repositories worked on as one project"
      title={<span className="flex items-center gap-1.5"><Layers className="size-3.5 text-brand" />Sources</span>}
      actions={p.source && mayChange ? (
        <Button size="sm" variant="outline" onClick={() => setAdding(true)}><Plus className="size-3.5" />Add source</Button>
      ) : undefined}>
      {!p.source ? (
        <Empty title="No sources on this machine" hint="Its code was not onboarded here, so there is nothing to add beside it." />
      ) : list.error ? (
        <Empty title="The sources did not load" hint={list.error} action={<Button size="sm" variant="outline" onClick={reload}>Try again</Button>} />
      ) : !list.data ? (
        <Empty icon={<Loader2 className="size-5 animate-spin" />} title="Reading the sources…" />
      ) : (
        <>
          <div className="divide-y divide-line/60">
            {sources.map((s, i) => {
              const extras = sources.filter((x) => !x.primary);
              const place = extras.findIndex((x) => x.id === s.id);
              return (
                <div key={s.id ?? 'first'} className="flex flex-wrap items-center gap-x-3 gap-y-2 px-5 py-3">
                  <Dot state={SOURCE_DOT[s.status]} pulse={s.status === 'onboarding'} />
                  <div className="min-w-0 flex-1 basis-56">
                    {renaming?.id === s.id ? (
                      <form onSubmit={rename} className="flex items-center gap-1.5">
                        <input autoFocus value={renaming.label} aria-label={`New label for ${s.label}`}
                          onChange={(e) => setRenaming({ id: s.id as number, label: e.target.value })}
                          className="h-8 w-40 rounded-lg border border-line-strong bg-surface-2 px-2 font-mono text-[12.5px] text-ink focus-visible:border-brand focus-visible:outline-none" />
                        <Button type="submit" size="icon-sm" variant="ghost" aria-label="Save the label"><Check className="size-3.5" /></Button>
                        <Button type="button" size="icon-sm" variant="ghost" aria-label="Keep the label" onClick={() => setRenaming(null)}><X className="size-3.5" /></Button>
                      </form>
                    ) : (
                      <span className="flex flex-wrap items-center gap-2">
                        <span className="font-mono text-[13px] font-medium text-ink">{s.primary ? s.label : `${s.label}/`}</span>
                        {s.primary && <Tag tone="brand">first source</Tag>}
                        <Tag>{s.kind === 'git' ? 'git' : 'folder'}</Tag>
                        {readOnly(s) && <Tag tone="violet">reference · read only</Tag>}
                        <span className="text-[12px] text-dim">{STATUS_WORD[s.status]}</span>
                      </span>
                    )}
                    <span className="mt-0.5 block truncate font-mono text-[11.5px] text-dim" title={s.repo}>
                      {s.repo}{s.branch ? ` @ ${s.branch}` : ''}
                    </span>
                    {s.status === 'failed' && s.note && <span className="mt-0.5 block text-[12px] text-danger [overflow-wrap:anywhere]">{s.note}</span>}
                    {s.primary && i === 0 && sources.length > 1 && (
                      <span className="mt-0.5 block text-[11.5px] text-dim">Its files keep their own paths; the others appear under their labels.</span>
                    )}
                  </div>
                  {mayChange && (
                    <span className="flex shrink-0 items-center gap-0.5">
                      {!s.primary && (
                        <RolePick value={s.role ?? 'code'} onChange={(r) => setRole(s, r)} label={`What ${s.label} is`}
                          disabled={s.status === 'onboarding'} />
                      )}
                      {!s.primary && (
                        <>
                          <Button size="icon-sm" variant="ghost" aria-label={`Move ${s.label} up`} disabled={place <= 0} onClick={() => move(s, -1)}><ArrowUp className="size-3.5" /></Button>
                          <Button size="icon-sm" variant="ghost" aria-label={`Move ${s.label} down`} disabled={place >= extras.length - 1} onClick={() => move(s, 1)}><ArrowDown className="size-3.5" /></Button>
                          <Button size="icon-sm" variant="ghost" aria-label={`Rename ${s.label}`} disabled={s.status === 'onboarding'}
                            onClick={() => setRenaming({ id: s.id as number, label: s.label })}><Pencil className="size-3.5" /></Button>
                        </>
                      )}
                      <Button size="xs" variant="ghost" disabled={s.status === 'onboarding' || busy === (s.id ?? 'first')}
                        onClick={() => void reindex(s)}>
                        {busy === (s.id ?? 'first') ? <Loader2 className="size-3 animate-spin" /> : <RefreshCw className="size-3" />}
                        {s.status === 'failed' ? 'Try again' : 'Re-index'}
                      </Button>
                      {!s.primary && (
                        <Button size="xs" variant={armed === s.id ? 'destructive' : 'ghost'} aria-label={`Remove ${s.label}`}
                          disabled={s.status === 'onboarding'} onClick={() => void remove(s)} onBlur={() => setArmed(null)}>
                          <Trash2 className="size-3" />{armed === s.id && 'Remove?'}
                        </Button>
                      )}
                    </span>
                  )}
                </div>
              );
            })}
          </div>
          {sources.length === 1 && (
            <p className="border-t border-line/60 px-5 py-3 text-[12.5px] text-dim">
              One source so far. Add the API beside the web app, a shared library or a data repository, and search,
              impact and runs span all of them — each file under its source's label.
            </p>
          )}
        </>
      )}

      <Dialog open={adding} onOpenChange={(o) => { setAdding(o); if (!o) setDraft(NEW); }}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>Add a source to {p.name}</DialogTitle>
            <DialogDescription>
              It is onboarded like a project — cloned or read in place, measured and indexed — and its files appear in {p.name} under its label.
            </DialogDescription>
          </DialogHeader>
          <form id="add-source" onSubmit={add} className="space-y-3">
            <Segmented options={[{ id: 'git', label: 'Git remote' }, { id: 'local', label: 'Local path' }]}
              value={draft.kind} onChange={(kind) => setDraft((d) => ({ ...d, kind }))} />
            <div className="flex items-end gap-2">
              <Field className="min-w-0 flex-1" mono autoFocus value={draft.repo}
                label={draft.kind === 'git' ? 'Clone URL' : 'Absolute path'}
                placeholder={draft.kind === 'git' ? 'git@github.com:org/api.git' : '/Users/you/code/api'}
                onChange={(v) => setDraft((d) => ({ ...d, repo: v, label: d.label && d.label !== labelFrom(d.repo) ? d.label : labelFrom(v) }))} />
              {draft.kind === 'local' && browse && (
                <Button type="button" variant="outline" size="sm" className="mb-px h-9" onClick={() => setPicking(true)}>
                  <FolderOpen className="size-3.5" />Browse…
                </Button>
              )}
            </div>
            <div className="flex flex-wrap items-center gap-2.5">
              <RolePick value={draft.role ?? 'code'} onChange={(role) => setDraft((d) => ({ ...d, role }))} label="What this source is" />
              <span className="text-[12px] text-dim">
                {draft.role === 'reference' ? 'Read only: indexed and read for grounding — documents, a design system, another team’s repository. Agents never write there.'
                  : 'Worked on: agents may change it in their own worktrees.'}
              </span>
            </div>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <Field label="Label" mono value={draft.label} placeholder="api" hint="The folder its files appear under"
                onChange={(v) => setDraft((d) => ({ ...d, label: v }))} />
              {draft.kind === 'git' && <Field label="Branch" mono value={draft.branch} onChange={(v) => setDraft((d) => ({ ...d, branch: v }))} />}
            </div>
            {problem && (draft.repo.trim() || draft.label.trim()) && <p className="text-[12.5px] text-dim">{problem}</p>}
          </form>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setAdding(false)}>Cancel</Button>
            <Button type="submit" form="add-source" disabled={!!problem || saving}>
              {saving && <Loader2 className="size-3.5 animate-spin" />}Add and onboard
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <FolderPicker open={picking} title="Choose a folder to add" onClose={() => setPicking(false)}
        onPick={(path) => { setDraft((d) => ({ ...d, repo: path, label: d.label || labelFrom(path) })); setPicking(false); }} />
    </Panel>
  );
}
