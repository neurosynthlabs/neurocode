import { useState, type SyntheticEvent } from 'react';
import { ChevronRight, File, Folder, FolderPlus, GitBranch, HardDrive, Link2, Loader2, RefreshCw } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Switch } from '@/components/ui/switch';
import { Empty, Tag } from '@/components/os';
import { ApiError } from '@/lib/api';
import { joinPath, machineApi, within, type MachineEntry, type MachineRoot } from '@/lib/live/machine';
import { useRemote } from '@/lib/remote';
import { cn } from '@/lib/utils';

const reason = (e: unknown) => (e instanceof ApiError ? e.message : 'The local API did not answer. Is it still running?');

/** The crumbs from the root that holds `path` down to `path` itself. */
function crumbs(path: string, root: MachineRoot | undefined): { label: string; path: string }[] {
  if (!root || !within(path, root.path)) return [{ label: path, path }];
  const rest = path.slice(root.path.length).split('/').filter(Boolean);
  const out = [{ label: root.label, path: root.path }];
  rest.forEach((part, i) => out.push({ label: part, path: joinPath(root.path, rest.slice(0, i + 1).join('/')) }));
  return out;
}

/**
 * A folder browser over the machine the API runs on: its roots, breadcrumbs, a marker on folders that
 * hold a repository, hidden files on request, "New folder", and "Use this folder". Only folders can be
 * opened; files are shown dimmed so a person can tell which folder they are in.
 */
export function FolderPicker({ open, onPick, onClose, title = 'Choose a folder', start, confirmLabel = 'Use this folder' }: {
  open: boolean;
  onPick: (path: string) => void;
  onClose: () => void;
  title?: string;
  /** Where to begin; the first root when absent or outside every root. */
  start?: string | null;
  confirmLabel?: string;
}) {
  const roots = useRemote(open ? 'machine-roots' : null, machineApi.roots);
  const [chosen, setChosen] = useState<string | null>(null);
  const [hidden, setHidden] = useState(false);
  const [version, setVersion] = useState(0);
  const [naming, setNaming] = useState<string | null>(null);
  const [making, setMaking] = useState(false);
  // It begins where it was asked to, or at the first root, until a folder is chosen.
  const begin = start && roots.data?.some((r) => within(start, r.path)) ? start : roots.data?.[0]?.path ?? null;
  const path = chosen ?? begin;
  const setPath = (p: string) => setChosen(p);
  const close = () => { setChosen(null); setNaming(null); onClose(); };

  const listing = useRemote(open && path ? `machine-list:${path}:${hidden}:${version}` : null, () => machineApi.list(path ?? '', hidden));
  const root = roots.data?.find((r) => path && within(path, r.path));
  const trail = path ? crumbs(path, root) : [];

  const enter = (entry: MachineEntry) => {
    if (entry.kind === 'dir' || entry.linkTo === 'dir') { setPath(entry.path); setNaming(null); }
  };

  const makeFolder = async (e: SyntheticEvent) => {
    e.preventDefault();
    const name = (naming ?? '').trim();
    if (!path || !name) return;
    setMaking(true);
    try {
      const made = await machineApi.mkdir(joinPath(path, name));
      setNaming(null);
      setPath(made.path);
      toast(`Made ${made.name}`);
    } catch (err) {
      toast.error('No folder made', { description: reason(err) });
    } finally {
      setMaking(false);
    }
  };

  const entries = listing.data?.entries ?? [];
  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) close(); }}>
      <DialogContent className="flex max-h-[min(88vh,720px)] flex-col gap-3 sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>Folders on the machine the NeuroCode API runs on.</DialogDescription>
        </DialogHeader>

        {roots.error ? (
          <Empty icon={<HardDrive className="size-6" />} title="The folders could not be listed" hint={roots.error}
            action={<Button size="sm" variant="outline" onClick={roots.reload}><RefreshCw className="size-3.5" />Try again</Button>} />
        ) : roots.data && roots.data.length === 0 ? (
          <Empty icon={<HardDrive className="size-6" />} title="No folder is open to the browser"
            hint="NEUROCODE_MACHINE_ROOTS in the server's .env names the folders it may open, separated by ':'. None of them exists on this machine." />
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-1.5">
              {(roots.data ?? []).map((r) => (
                <button key={r.path} type="button" onClick={() => { setPath(r.path); setNaming(null); }}
                  className={cn('inline-flex h-7 items-center gap-1.5 rounded-lg px-2.5 text-[12.5px] transition-colors',
                    root?.path === r.path ? 'bg-brand/12 text-brand' : 'text-soft hover:bg-surface-2 hover:text-ink')}>
                  <HardDrive className="size-3.5" />{r.label}
                </button>
              ))}
              <label className="ml-auto flex items-center gap-2 text-[12.5px] text-soft">
                Hidden files
                <Switch checked={hidden} onCheckedChange={setHidden} />
              </label>
            </div>

            <nav aria-label="Folder path" className="flex min-w-0 flex-wrap items-center gap-0.5 text-[12.5px]">
              {trail.map((c, i) => (
                <span key={c.path} className="flex min-w-0 items-center gap-0.5">
                  {i > 0 && <ChevronRight className="size-3 shrink-0 text-dim" />}
                  <button type="button" onClick={() => { setPath(c.path); setNaming(null); }}
                    className={cn('max-w-[200px] truncate rounded-md px-1.5 py-0.5 font-mono hover:bg-surface-2',
                      i === trail.length - 1 ? 'text-ink' : 'text-soft')}>
                    {c.label}
                  </button>
                </span>
              ))}
            </nav>

            <div className="min-h-[220px] flex-1 overflow-y-auto rounded-xl border border-line/70 bg-surface">
              {listing.error ? (
                <Empty title="This folder could not be read" hint={listing.error}
                  action={<Button size="sm" variant="outline" onClick={listing.reload}><RefreshCw className="size-3.5" />Try again</Button>} />
              ) : !listing.data ? (
                <div className="flex h-[220px] items-center justify-center gap-2 text-[13px] text-dim">
                  <Loader2 className="size-4 animate-spin" />Reading the folder…
                </div>
              ) : (
                <ul className="divide-y divide-line/60">
                  {naming !== null && (
                    <li className="px-4 py-2.5">
                      <form onSubmit={(e) => void makeFolder(e)} className="flex items-center gap-2">
                        <FolderPlus className="size-4 shrink-0 text-brand" />
                        <input autoFocus value={naming} onChange={(e) => setNaming(e.target.value)} placeholder="Folder name"
                          aria-label="New folder name"
                          onKeyDown={(e) => { if (e.key === 'Escape') { e.stopPropagation(); setNaming(null); } }}
                          className="h-7 min-w-0 flex-1 rounded-md border border-line bg-surface-2/60 px-2 font-mono text-[12.5px] text-ink focus-visible:border-brand focus-visible:outline-none" />
                        <Button size="xs" type="submit" disabled={!naming.trim() || making}>{making && <Loader2 className="size-3 animate-spin" />}Make</Button>
                        <Button size="xs" variant="ghost" type="button" onClick={() => setNaming(null)}>Cancel</Button>
                      </form>
                    </li>
                  )}
                  {entries.length === 0 && naming === null && (
                    <li><Empty title="This folder is empty" hint={listing.data.hidden ? `${listing.data.hidden} hidden ${listing.data.hidden === 1 ? 'entry' : 'entries'} not shown.` : 'Use it as it is, or make a folder inside it.'} /></li>
                  )}
                  {entries.map((e) => {
                    const folder = e.kind === 'dir' || e.linkTo === 'dir';
                    return (
                      <li key={e.path}>
                        <button type="button" disabled={!folder} onClick={() => enter(e)}
                          className={cn('flex w-full items-center gap-2.5 px-4 py-2 text-left text-[13px]',
                            folder ? 'text-ink-2 hover:bg-surface-2/70' : 'cursor-default text-dim')}>
                          {e.kind === 'link' ? <Link2 className="size-4 shrink-0" /> : folder ? <Folder className="size-4 shrink-0 text-brand" /> : <File className="size-4 shrink-0" />}
                          <span className="min-w-0 flex-1 truncate">{e.name}</span>
                          {e.git && <Tag tone="info"><GitBranch className="mr-1 inline size-3" />repository</Tag>}
                          {e.kind === 'link' && !e.linkTo && <span className="text-[12px] text-dim">points outside</span>}
                          {folder && <ChevronRight className="size-3.5 shrink-0 text-dim" />}
                        </button>
                      </li>
                    );
                  })}
                  {listing.data.capped && (
                    <li className="px-4 py-2.5 text-[12.5px] text-dim">Showing the first {entries.length.toLocaleString()} of {listing.data.total.toLocaleString()} entries.</li>
                  )}
                </ul>
              )}
            </div>
          </>
        )}

        <DialogFooter className="flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <div className="min-w-0 truncate font-mono text-[12px] text-dim" title={path ?? ''}>{path ?? ''}</div>
          <div className="flex shrink-0 flex-wrap gap-2">
            <Button variant="outline" size="sm" disabled={!path || naming !== null} onClick={() => setNaming('')}><FolderPlus className="size-3.5" />New folder</Button>
            <Button variant="outline" size="sm" onClick={() => { setVersion((v) => v + 1); }} disabled={!path}><RefreshCw className="size-3.5" /><span className="sr-only">Refresh</span></Button>
            <Button variant="ghost" size="sm" onClick={close}>Cancel</Button>
            <Button size="sm" disabled={!path || !listing.data} onClick={() => { if (path) { setChosen(null); onPick(path); } }}>{confirmLabel}</Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
