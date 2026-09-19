import { useCallback, useEffect, useMemo, useRef, useState, type SyntheticEvent } from 'react';
import { ChevronRight, File, FilePlus, Folder, FolderOpen, FolderPlus, GitBranch, Link2, Loader2, Lock, RefreshCw } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { ApiError } from '@/lib/api';
import { onPathMenu } from '@/lib/desktop';
import {
  dirName, joinPath, machineApi, within, type GitLetter, type MachineEntry, type MachineGit, type MachineListing,
} from '@/lib/live/machine';
import { cn } from '@/lib/utils';

const reason = (e: unknown) => (e instanceof ApiError ? e.message : 'The local API did not answer. Is it still running?');

/** One folder at the top of the tree: a project's source, or a folder opened from the machine. */
export interface TreeRoot {
  key: string;
  label: string;
  /** Absolute; null when this source has no folder on this machine (then `note` says why). */
  path: string | null;
  git: MachineGit | null;
  note?: string;
  /** Only read here: a reference source, or a project this one references. Nothing is made in it from the tree. */
  readOnly?: boolean;
  /** A heading drawn above the first root of a group ("References"); roots without one come first. */
  group?: string;
}

type Loaded = { state: 'loading' } | { state: 'error'; error: string } | { state: 'ok'; listing: MachineListing };

/** How git's letter reads in the tree: a colour, and the word a tooltip uses. */
const GIT_TONE: Record<GitLetter, { cls: string; word: string }> = {
  M: { cls: 'text-warn', word: 'modified' },
  T: { cls: 'text-warn', word: 'type changed' },
  A: { cls: 'text-ok', word: 'added' },
  '?': { cls: 'text-ok', word: 'untracked' },
  C: { cls: 'text-ok', word: 'copied' },
  R: { cls: 'text-info', word: 'renamed' },
  D: { cls: 'text-danger', word: 'deleted' },
  U: { cls: 'text-danger', word: 'conflicted' },
};

/** Every changed path of every root, made absolute, and the folders that hold one. */
function gitMarks(roots: TreeRoot[]) {
  const files = new Map<string, GitLetter>();
  const folders = new Set<string>();
  for (const r of roots) {
    if (!r.git) continue;
    for (const c of r.git.changed) {
      const abs = joinPath(r.git.root, c.path.replace(/\/$/, ''));
      files.set(abs, c.status);
      let up = dirName(abs);
      while (within(up, r.git.root) && up !== r.git.root && !folders.has(up)) { folders.add(up); up = dirName(up); }
    }
  }
  return { files, folders };
}

/**
 * The Workbench's file tree: one section per root, folders read from the machine as they are opened,
 * git's status as colours, and new files and folders made in the folder last chosen.
 */
export function FileTree({ roots, activePath, hidden, refresh, onOpen, onCreated }: {
  roots: TreeRoot[];
  activePath: string | null;
  hidden: boolean;
  /** Bump to read every open folder again. */
  refresh: number;
  onOpen: (path: string) => void;
  /** A file or folder was made; the Workbench refreshes git and opens a new file. */
  onCreated: (entry: MachineEntry) => void;
}) {
  const [dirs, setDirs] = useState<Record<string, Loaded>>({});
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const [focusDir, setFocusDir] = useState<string | null>(null);
  const [creating, setCreating] = useState<{ dir: string; kind: 'file' | 'dir'; name: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);

  const load = useCallback((path: string) => {
    setDirs((d) => (d[path]?.state === 'ok' ? d : { ...d, [path]: { state: 'loading' } }));
    machineApi.list(path, hidden).then(
      (listing) => { if (alive.current) setDirs((d) => ({ ...d, [path]: { state: 'ok', listing } })); },
      (e: unknown) => { if (alive.current) setDirs((d) => ({ ...d, [path]: { state: 'error', error: reason(e) } })); },
    );
  }, [hidden]);

  // Every root starts open; a refresh (or the hidden-files switch) reads every open folder again. Folders
  // left open under a root that is no longer shown are forgotten.
  const rootPaths = roots.map((r) => r.path).filter((p): p is string => !!p).join('\n');
  const shownRef = useRef(expanded);
  useEffect(() => { shownRef.current = expanded; });
  useEffect(() => {
    const paths = rootPaths ? rootPaths.split('\n') : [];
    const next = new Set([...shownRef.current].filter((p) => paths.some((r) => within(p, r))));
    paths.forEach((p) => next.add(p));
    setExpanded(next);
    next.forEach(load);
  }, [rootPaths, refresh, load]);

  const marks = useMemo(() => gitMarks(roots), [roots]);

  const toggle = (path: string) => {
    setFocusDir(path);
    const next = new Set(expanded);
    if (next.has(path)) next.delete(path);
    else { next.add(path); if (dirs[path]?.state !== 'ok') load(path); }
    setExpanded(next);
  };

  const startCreate = (root: TreeRoot, kind: 'file' | 'dir') => {
    if (!root.path) return;
    const dir = focusDir && within(focusDir, root.path) ? focusDir : root.path;
    setExpanded((was) => new Set(was).add(dir));
    if (dirs[dir]?.state !== 'ok') load(dir);
    setCreating({ dir, kind, name: '' });
  };

  const create = async (e: SyntheticEvent) => {
    e.preventDefault();
    if (!creating || !creating.name.trim()) return;
    setBusy(true);
    try {
      const target = joinPath(creating.dir, creating.name.trim());
      const made = creating.kind === 'dir' ? await machineApi.mkdir(target) : await machineApi.newFile(target);
      setCreating(null);
      load(creating.dir);
      if (made.kind === 'dir') { setExpanded((was) => new Set(was).add(made.path)); setFocusDir(made.path); load(made.path); }
      onCreated(made);
    } catch (err) {
      toast.error(creating.kind === 'dir' ? 'No folder made' : 'No file made', { description: reason(err) });
    } finally {
      setBusy(false);
    }
  };

  const creator = (dir: string, depth: number) => creating && creating.dir === dir && (
    <li key="creating">
      <form onSubmit={(e) => void create(e)} className="flex items-center gap-1.5 py-1 pr-2" style={{ paddingLeft: 10 + depth * 14 }}>
        {creating.kind === 'dir' ? <FolderPlus className="size-3.5 shrink-0 text-brand" /> : <FilePlus className="size-3.5 shrink-0 text-brand" />}
        <input autoFocus value={creating.name} disabled={busy}
          aria-label={creating.kind === 'dir' ? 'New folder name' : 'New file name'}
          placeholder={creating.kind === 'dir' ? 'folder name' : 'file name'}
          onChange={(e) => setCreating({ ...creating, name: e.target.value })}
          onKeyDown={(e) => { if (e.key === 'Escape') setCreating(null); }}
          onBlur={() => { if (!creating.name.trim()) setCreating(null); }}
          className="h-6 min-w-0 flex-1 rounded-md border border-line bg-surface-2/60 px-1.5 font-mono text-[12.5px] text-ink focus-visible:border-brand focus-visible:outline-none" />
        {busy && <Loader2 className="size-3.5 animate-spin text-dim" />}
      </form>
    </li>
  );

  const children = (dir: string, depth: number) => {
    const got = dirs[dir];
    if (!got || got.state === 'loading') {
      return <li className="flex items-center gap-2 py-1 text-[12.5px] text-dim" style={{ paddingLeft: 14 + depth * 14 }}><Loader2 className="size-3 animate-spin" />Reading…</li>;
    }
    if (got.state === 'error') {
      return (
        <li className="py-1 pr-2 text-[12.5px] text-danger" style={{ paddingLeft: 14 + depth * 14 }}>
          {got.error} <button type="button" className="text-soft underline-offset-2 hover:underline" onClick={() => load(dir)}>Try again</button>
        </li>
      );
    }
    const { entries, capped, total } = got.listing;
    return (
      <>
        {creator(dir, depth)}
        {entries.length === 0 && !(creating?.dir === dir) && (
          <li className="py-1 text-[12.5px] text-dim" style={{ paddingLeft: 14 + depth * 14 }}>Empty folder</li>
        )}
        {entries.map((e) => node(e, depth))}
        {capped && <li className="py-1 text-[12px] text-dim" style={{ paddingLeft: 14 + depth * 14 }}>First {entries.length.toLocaleString()} of {total.toLocaleString()}</li>}
      </>
    );
  };

  const node = (e: MachineEntry, depth: number) => {
    const folder = e.kind === 'dir' || e.linkTo === 'dir';
    const openable = folder || e.kind === 'file' || e.linkTo === 'file';
    const open = folder && expanded.has(e.path);
    const letter = marks.files.get(e.path);
    const tone = letter ? GIT_TONE[letter] : null;
    const holdsChange = folder && marks.folders.has(e.path);
    const active = activePath === e.path;
    return (
      <li key={e.path}>
        <button type="button" disabled={!openable} title={e.kind === 'link' && !e.linkTo ? `${e.name} points outside the folders this server opens` : e.path}
          onClick={() => { if (folder) toggle(e.path); else { setFocusDir(dirName(e.path)); onOpen(e.path); } }}
          // The desktop app's menu for it: Open in the editor, Reveal in Finder, Copy Path. Nothing changes in a browser.
          onContextMenu={(ev) => { if (openable) onPathMenu(ev, e.path); }}
          className={cn('group flex w-full items-center gap-1.5 py-[3px] pr-2 text-left text-[13px] transition-colors',
            active ? 'bg-brand/10 text-ink' : 'text-ink-2 hover:bg-surface-2/70', !openable && 'cursor-default opacity-60')}
          style={{ paddingLeft: 6 + depth * 14 }}>
          <ChevronRight className={cn('size-3 shrink-0 text-dim transition-transform', folder ? '' : 'invisible', open && 'rotate-90')} />
          {e.kind === 'link' ? <Link2 className="size-3.5 shrink-0 text-dim" />
            : folder ? (open ? <FolderOpen className="size-3.5 shrink-0 text-brand" /> : <Folder className="size-3.5 shrink-0 text-brand" />)
            : <File className="size-3.5 shrink-0 text-dim" />}
          <span className={cn('min-w-0 flex-1 truncate', tone?.cls, e.name.startsWith('.') && !tone && 'text-soft')}>{e.name}</span>
          {e.git && <GitBranch className="size-3 shrink-0 text-info" aria-label="repository" />}
          {holdsChange && !letter && <span className="size-1.5 shrink-0 rounded-full bg-warn/80" aria-label="holds changes" />}
          {tone && <span className={cn('shrink-0 font-mono text-[11px]', tone.cls)} title={tone.word}>{letter}</span>}
        </button>
        {open && <ul>{children(e.path, depth + 1)}</ul>}
      </li>
    );
  };

  if (roots.length === 0) return null;
  return (
    <div className="flex flex-col gap-1 pb-3">
      {roots.map((root, i) => {
        const open = !!root.path && expanded.has(root.path);
        const heading = root.group && root.group !== roots[i - 1]?.group ? root.group : null;
        return (
          <section key={root.key} className="min-w-0">
            {heading && <h3 className="eyebrow mt-2 border-t border-line/60 px-3 pt-3 pb-1">{heading}</h3>}
            <div className="sticky top-0 z-10 flex items-center gap-1 bg-surface/95 px-2 py-1.5 backdrop-blur">
              <button type="button" disabled={!root.path} onClick={() => root.path && toggle(root.path)}
                onContextMenu={(ev) => onPathMenu(ev, root.path)}
                className="flex min-w-0 flex-1 items-center gap-1 text-left">
                <ChevronRight className={cn('size-3 shrink-0 text-dim transition-transform', open && 'rotate-90')} />
                <span className="truncate text-[12.5px] font-semibold text-ink">{root.label}</span>
                {root.git?.branch && (
                  <span className="ml-1 inline-flex min-w-0 items-center gap-1 truncate font-mono text-[11px] text-dim">
                    <GitBranch className="size-3 shrink-0" />{root.git.branch}
                  </span>
                )}
                {root.readOnly && (
                  <span className="ml-1 inline-flex shrink-0 items-center gap-1 rounded-full border border-line px-1.5 text-[10.5px] text-dim"
                    title="Read for search and grounding; agents never write here, and the editor opens its files read only.">
                    <Lock className="size-2.5" />read only
                  </span>
                )}
              </button>
              {root.path && root.readOnly && (
                <Tooltip><TooltipTrigger render={<Button size="icon-xs" variant="ghost" aria-label={`Read ${root.label} again`} onClick={() => root.path && load(root.path)} />}>
                  <RefreshCw className="size-3.5" /></TooltipTrigger><TooltipContent>Refresh</TooltipContent></Tooltip>
              )}
              {root.path && !root.readOnly && (
                <span className="flex shrink-0 items-center">
                  <Tooltip><TooltipTrigger render={<Button size="icon-xs" variant="ghost" aria-label={`New file in ${root.label}`} onClick={() => startCreate(root, 'file')} />}>
                    <FilePlus className="size-3.5" /></TooltipTrigger><TooltipContent>New file</TooltipContent></Tooltip>
                  <Tooltip><TooltipTrigger render={<Button size="icon-xs" variant="ghost" aria-label={`New folder in ${root.label}`} onClick={() => startCreate(root, 'dir')} />}>
                    <FolderPlus className="size-3.5" /></TooltipTrigger><TooltipContent>New folder</TooltipContent></Tooltip>
                  <Tooltip><TooltipTrigger render={<Button size="icon-xs" variant="ghost" aria-label={`Read ${root.label} again`} onClick={() => root.path && load(root.path)} />}>
                    <RefreshCw className="size-3.5" /></TooltipTrigger><TooltipContent>Refresh</TooltipContent></Tooltip>
                </span>
              )}
            </div>
            {!root.path ? (
              <p className="px-4 pb-2 text-[12.5px] leading-relaxed text-dim">{root.note ?? 'This folder is not on this machine.'}</p>
            ) : open && <ul>{children(root.path, 0)}</ul>}
          </section>
        );
      })}
    </div>
  );
}
