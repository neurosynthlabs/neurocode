import { useMemo, useState, type SyntheticEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { BookOpen, Check, Loader2, Pencil, Plus, Search, Trash2, X } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Dot, Empty, Field, Panel, Tag } from '@/components/os';
import { ApiError } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { referencesApi, referencesOf, type ProjectReference } from '@/lib/live/sources';
import { useProject } from '@/lib/project-context';
import { useRemote } from '@/lib/remote';
import { cn } from '@/lib/utils';
import type { Project } from '@/types';

const reason = (e: unknown) => (e instanceof ApiError ? e.message : 'The local API did not answer. Is it still running?');

/**
 * The projects a project reads from, and those that read from it. A referenced project's code, documents and
 * memory are searched beside this project's own and handed to models labelled as a reference; sessions read its
 * files as `<id>:<path>`; nothing there is ever written from here. Adding, changing the note of and removing a
 * reference need projects:onboard, and each is in the activity and audit logs.
 */
export function ReferencesPanel({ p }: { p: Project }) {
  const nav = useNavigate();
  const { can } = useAuth();
  const { all: projects } = useProject();
  const mayChange = can('projects:onboard');
  const [version, setVersion] = useState(0);
  // The project card carries its references: a change made anywhere streams in and reads the list again.
  const list = useRemote(`references:${p.id}:${referencesOf(p).join(',')}:${version}`, () => referencesApi.list(p.id));
  const reload = () => setVersion((v) => v + 1);
  const data = list.data;

  const [adding, setAdding] = useState(false);
  const [q, setQ] = useState('');
  const [picked, setPicked] = useState<string | null>(null);
  const [note, setNote] = useState('');
  const [saving, setSaving] = useState(false);
  const [editing, setEditing] = useState<{ id: number; note: string } | null>(null);
  const [armed, setArmed] = useState<number | null>(null);

  const taken = new Set([p.id, ...(data?.references ?? []).map((r) => r.project.id)]);
  const choices = useMemo(() => {
    const words = q.trim().toLowerCase().split(/\s+/).filter(Boolean);
    return projects.filter((x) => !taken.has(x.id))
      .filter((x) => words.every((w) => `${x.name} ${x.codename} ${x.id} ${x.stack.join(' ')}`.toLowerCase().includes(w)));
    // `taken` is rebuilt from the list each render; the list and the query are what change it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projects, q, data, p.id]);

  const close = () => { setAdding(false); setQ(''); setPicked(null); setNote(''); };

  const add = async (e: SyntheticEvent) => {
    e.preventDefault();
    if (!picked) return;
    setSaving(true);
    try {
      const made = await referencesApi.add(p.id, picked, note.trim());
      toast.success(`${p.name} now reads ${made.project.name}`, { description: 'Its pieces are searched beside this project’s own, labelled as a reference. Nothing there is written from here.' });
      close();
      reload();
    } catch (err) {
      toast.error('No reference added', { description: reason(err) });
    } finally {
      setSaving(false);
    }
  };

  const saveNote = async (e: SyntheticEvent) => {
    e.preventDefault();
    if (!editing) return;
    try {
      await referencesApi.note(p.id, editing.id, editing.note.trim());
      setEditing(null);
      reload();
    } catch (err) {
      toast.error('The note was not saved', { description: reason(err) });
    }
  };

  const remove = async (r: ProjectReference) => {
    if (armed !== r.id) { setArmed(r.id); return; }
    setArmed(null);
    try {
      await referencesApi.remove(p.id, r.id);
      toast.success(`${p.name} no longer reads ${r.project.name}`);
      reload();
    } catch (err) {
      toast.error('Not removed', { description: reason(err) });
    }
  };

  const row = (r: ProjectReference, i: number | null) => (
    <div key={r.id} className="flex flex-wrap items-center gap-x-3 gap-y-2 px-5 py-3">
      <Dot state={r.project.status} />
      <div className="min-w-0 flex-1 basis-56">
        <span className="flex flex-wrap items-center gap-2">
          <button type="button" onClick={() => nav(`/projects/${encodeURIComponent(r.project.id)}`)}
            className="text-[13.5px] font-medium text-ink hover:underline">{r.project.name}</button>
          <span className="font-mono text-[11.5px] text-dim">{r.project.id}:</span>
          {i !== null && <Tag>read only</Tag>}
          {i !== null && data && i >= data.readAtMost && (
            <span title={`Only the first ${data.readAtMost} are searched; sessions still read its files.`}>
              <Tag tone="warn">not searched</Tag>
            </span>
          )}
          <span className="text-[12px] text-dim">{r.project.understoodPct === null ? 'not indexed' : `${r.project.understoodPct}% understood`}</span>
        </span>
        {editing?.id === r.id ? (
          <form onSubmit={(e) => void saveNote(e)} className="mt-1.5 flex items-center gap-1.5">
            <input autoFocus value={editing.note} maxLength={500} aria-label={`Why ${p.name} reads ${r.project.name}`}
              onChange={(e) => setEditing({ id: r.id, note: e.target.value })}
              onKeyDown={(e) => { if (e.key === 'Escape') setEditing(null); }}
              className="h-8 min-w-0 flex-1 rounded-lg border border-line-strong bg-surface-2 px-2 text-[12.5px] text-ink focus-visible:border-brand focus-visible:outline-none" />
            <Button type="submit" size="icon-sm" variant="ghost" aria-label="Save the note"><Check className="size-3.5" /></Button>
            <Button type="button" size="icon-sm" variant="ghost" aria-label="Keep the note" onClick={() => setEditing(null)}><X className="size-3.5" /></Button>
          </form>
        ) : (
          <span className={cn('mt-0.5 block text-[12.5px] [overflow-wrap:anywhere]', r.note ? 'text-soft' : 'text-dim')}>
            {r.note || (i !== null ? 'No note: say why it is read. A model reads it too.' : 'No note')}
          </span>
        )}
      </div>
      {i !== null && mayChange && editing?.id !== r.id && (
        <span className="flex shrink-0 items-center gap-0.5">
          <Button size="icon-sm" variant="ghost" aria-label={`Edit the note on ${r.project.name}`} onClick={() => setEditing({ id: r.id, note: r.note })}>
            <Pencil className="size-3.5" />
          </Button>
          <Button size="xs" variant={armed === r.id ? 'destructive' : 'ghost'} aria-label={`Stop reading ${r.project.name}`}
            onClick={() => void remove(r)} onBlur={() => setArmed(null)}>
            <Trash2 className="size-3" />{armed === r.id && 'Remove?'}
          </Button>
        </span>
      )}
    </div>
  );

  return (
    <Panel flush
      about="Other projects this one reads from. Their code, documents and memory are searched beside its own, never written."
      title={<span className="flex items-center gap-1.5"><BookOpen className="size-3.5 text-brand" />References</span>}
      actions={mayChange ? <Button size="sm" variant="outline" onClick={() => setAdding(true)}><Plus className="size-3.5" />Add reference</Button> : undefined}>
      {list.error ? (
        <Empty title="The references did not load" hint={list.error} action={<Button size="sm" variant="outline" onClick={reload}>Try again</Button>} />
      ) : !data ? (
        <Empty icon={<Loader2 className="size-5 animate-spin" />} title="Reading the references…" />
      ) : (
        <>
          {data.references.length === 0 ? (
            <Empty title="No references yet" hint="Add a service it calls, or a library it uses." />
          ) : (
            <div className="divide-y divide-line/60">{data.references.map((r, i) => row(r, i))}</div>
          )}
          <div className="border-t border-line/60 px-5 pt-3 pb-1">
            <p className="text-[12px] font-medium text-dim">Referenced by</p>
          </div>
          {data.referencedBy.length === 0 ? (
            <p className="px-5 pb-3 text-[12.5px] text-dim">No project reads from {p.name}.</p>
          ) : (
            <div className="divide-y divide-line/60">{data.referencedBy.map((r) => row(r, null))}</div>
          )}
        </>
      )}

      <Dialog open={adding} onOpenChange={(o) => { if (!o) close(); }}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>Read another project from {p.name}</DialogTitle>
            <DialogDescription>
              Searched beside {p.name}'s own and handed to models as a reference. Sessions read its files
              as <span className="font-mono">id:path</span>. Agents never write there.
            </DialogDescription>
          </DialogHeader>
          <form id="add-reference" onSubmit={(e) => void add(e)} className="space-y-3">
            <Field autoFocus value={q} onChange={setQ} placeholder="Search projects…" icon={<Search className="size-3.5" />} label="Project" />
            <div className="max-h-56 overflow-y-auto rounded-lg border border-line">
              {choices.length === 0 ? (
                <p className="px-3 py-5 text-center text-[12.5px] text-dim">
                  {projects.length <= 1 ? 'No other project yet.' : q.trim() ? `No project matches “${q.trim()}”.` : 'Every other project is already referenced.'}
                </p>
              ) : choices.map((x) => (
                <button key={x.id} type="button" onClick={() => setPicked(x.id)} aria-pressed={picked === x.id}
                  className={cn('flex w-full items-center gap-2.5 px-3 py-2 text-left transition-colors',
                    picked === x.id ? 'bg-brand/10' : 'hover:bg-surface-2')}>
                  <Dot state={x.status} />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[13px] font-medium text-ink">{x.name}</span>
                    <span className="block truncate text-[11.5px] text-dim">{x.stack.slice(0, 3).join(' · ') || x.status}</span>
                  </span>
                  {picked === x.id && <Check className="size-3.5 shrink-0 text-brand" />}
                </button>
              ))}
            </div>
            <Field label="Why it is read (optional)" value={note} onChange={setNote} placeholder="The payments service checkout calls" />
          </form>
          <DialogFooter>
            <Button variant="ghost" onClick={close}>Cancel</Button>
            <Button type="submit" form="add-reference" disabled={!picked || saving}>
              {saving && <Loader2 className="size-3.5 animate-spin" />}Add reference
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Panel>
  );
}
