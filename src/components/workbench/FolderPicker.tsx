import { useEffect, useRef, useState, type SyntheticEvent } from 'react';
import {
  AppWindow, ChevronRight, Download, File, FileArchive, FileText, Folder, FolderPlus, GitBranch, HardDrive, Link2, Loader2, Monitor,
  RefreshCw, ShieldAlert,
} from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Switch } from '@/components/ui/switch';
import { Empty, Tag } from '@/components/os';
import { ApiError } from '@/lib/api';
import { desktop } from '@/lib/desktop';
import {
  bytes, dirName, joinPath, machineApi, osPermission, within, type MachineEntry, type MachineListing, type MachineRoot,
  type OsPermission,
} from '@/lib/live/machine';
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

/* The last folder a picker was in, per purpose ("import", "project-folder"…): a per-browser convenience only,
   so a failure to read or keep it is said in the console and otherwise ignored. */
const LAST_KEY = 'nc.picker.last.';
function lastFolder(purpose: string | undefined): string | null {
  if (!purpose) return null;
  try {
    return localStorage.getItem(LAST_KEY + purpose);
  } catch (e) {
    console.warn('[NeuroCode] the last folder could not be read from this browser:', e);
    return null;
  }
}
function keepFolder(purpose: string | undefined, path: string) {
  if (!purpose) return;
  try {
    localStorage.setItem(LAST_KEY + purpose, path);
  } catch (e) {
    console.warn('[NeuroCode] the last folder could not be kept in this browser:', e);
  }
}

const PLACE_ICON: Record<string, typeof Monitor> = { Desktop: Monitor, Downloads: Download, Documents: FileText };
const when = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }) : '');
const matches = (name: string, accept: string[]) => accept.some((a) => name.toLowerCase().endsWith(a.toLowerCase()));

type Read = { listing: MachineListing; blocked?: undefined } | { blocked: OsPermission; listing?: undefined };

/** What macOS said, and the one thing that fixes it: the switch in System Settings for the app that started the API. */
export function OsPermissionCard({ blocked, onRetry }: { blocked: OsPermission; onRetry: () => void }) {
  const who = blocked.app ?? 'the app that started NeuroCode';
  return (
    <div className="flex flex-col items-center gap-3 px-6 py-8 text-center">
      <span className="grid size-10 place-items-center rounded-full bg-warn/12 text-warn"><ShieldAlert className="size-5" /></span>
      <div className="max-w-md space-y-1.5">
        <p className="text-[14px] font-semibold text-ink">macOS keeps {blocked.place} private from {who}</p>
        <p className="text-[13px] leading-relaxed text-soft">
          macOS has not given {who} access to {blocked.place}. Open {blocked.settings}, turn it on for {who}, then try again.
        </p>
        <p className="text-[12px] text-dim">Some apps only see the change after they are quit and opened again.</p>
        <p className="font-mono text-[11.5px] break-all text-dim">{blocked.folder}</p>
      </div>
      <Button size="sm" variant="outline" onClick={onRetry}><RefreshCw className="size-3.5" />Try again</Button>
    </div>
  );
}

/**
 * A browser over the machine the API runs on: its roots and quick places, breadcrumbs, a marker on folders that
 * hold a repository, hidden files on request. In folder mode (the default) it picks a folder — "New folder" and
 * "Use this folder"; files are shown dimmed so a person can tell where they are. In file mode it picks one file
 * whose name ends with one of `accept`, showing each file's size and date. With `purpose` it opens where it was
 * last used for that purpose.
 *
 * In the desktop app the Mac's own dialog comes first, in the same mode (a folder, or a file with one of `accept`'s
 * endings), opening where this browser would have. What it picks still has to be inside the folders the API may
 * open: a pick outside them, or one the dialog could not make, falls back to this browser with the reason said,
 * and "Finder…" there asks the Mac's dialog again. Cancelling the Mac's dialog cancels the picker.
 */
export function FolderPicker({
  open, onPick, onClose, title = 'Choose a folder', start, confirmLabel, mode = 'folder', accept = [], purpose, description,
}: {
  open: boolean;
  onPick: (path: string) => void;
  onClose: () => void;
  title?: string;
  /** Where to begin; the folder last used for this purpose, else the first root, when absent or outside every root. */
  start?: string | null;
  confirmLabel?: string;
  mode?: 'folder' | 'file';
  /** File mode: the endings a file may have to be chosen, e.g. ['.zip', '.tar.gz']. */
  accept?: string[];
  /** Remember the last folder under this name, in this browser. */
  purpose?: string;
  description?: string;
}) {
  const fileMode = mode === 'file';
  const roots = useRemote(open ? 'machine-roots' : null, machineApi.roots);
  // An older server has no quick places; the picker then shows only the roots.
  const places = useRemote(open ? 'machine-places' : null, () => machineApi.places().catch(() => [] as MachineRoot[]));
  const [chosen, setChosen] = useState<string | null>(null);
  const [file, setFile] = useState<string | null>(null);
  const [hidden, setHidden] = useState(false);
  const [version, setVersion] = useState(0);
  const [naming, setNaming] = useState<string | null>(null);
  const [making, setMaking] = useState(false);
  // The desktop's own dialog: 'asking' while it is up (this dialog stays hidden behind it), then 'browse' when the
  // pick has to be made here after all. `note` says why.
  const [native, setNative] = useState<{ phase: 'asking' | 'browse'; note: string | null } | null>(null);
  const askedFor = useRef(false);
  const inRoots = (p: string | null | undefined): p is string => !!p && !!roots.data?.some((r) => within(p, r.path));
  const remembered = lastFolder(purpose);
  // It begins where it was asked to, or where it was last used, or at the first root — until a folder is chosen.
  const begin = inRoots(start) ? start : inRoots(remembered) ? remembered : roots.data?.[0]?.path ?? null;
  const path = chosen ?? begin;
  const go = (p: string) => { setChosen(p); setNaming(null); setFile(null); };
  const close = () => { setChosen(null); setNaming(null); setFile(null); setNative(null); askedFor.current = false; onClose(); };
  const pick = (p: string) => {
    keepFolder(purpose, fileMode ? dirName(p) : p);
    setChosen(null);
    setFile(null);
    setNative(null);
    askedFor.current = false;
    onPick(p);
  };

  /** The Mac's dialog, then the API's own checks (inside a root, readable) and, in file mode, an accepted ending. */
  const askMac = async () => {
    if (!desktop) return;
    setNative({ phase: 'asking', note: null });
    const opening = start ?? remembered ?? null;
    let got: string | null;
    try {
      const options = { title, start: opening, confirmLabel, accept };
      got = fileMode ? await desktop.pickFile(options) : await desktop.pickFolder(options);
    } catch (e) {
      setNative({ phase: 'browse', note: `The Mac's dialog did not open: ${e instanceof Error ? e.message : String(e)}` });
      return;
    }
    if (!got) { close(); return; }
    // The API is asked, not a string compared: it resolves links (/var is /private/var on a Mac) and holds the
    // pick to its roots exactly as it holds this browser, and what it answers is the real path every later call uses.
    const folder = fileMode ? dirName(got) : got;
    try {
      const listed = await machineApi.list(folder, true);
      if (!fileMode) { pick(listed.path); return; }
      const name = got.slice(folder.length).replace(/^\/+/, '');
      // A folder of more than 2 000 entries is listed in part; a file past that is taken by its name.
      const entry = listed.entries.find((e) => e.name === name)
        ?? (listed.capped ? { name, path: joinPath(listed.path, name) } : null);
      if (!entry || !matches(entry.name, accept)) {
        go(listed.path);
        setNative({ phase: 'browse', note: `${name} is not a ${accept.join(', ')} file NeuroCode can open here.` });
        return;
      }
      pick(entry.path);
    } catch (e) {
      setNative({ phase: 'browse', note: `${got} cannot be used: ${reason(e)}` });
    }
  };
  // Opened in the desktop app: straight to the Mac's dialog, once per opening (React's development double run included).
  useEffect(() => {
    if (!open) askedFor.current = false;
    if (!open || !desktop || askedFor.current) return;
    askedFor.current = true;
    void askMac();
  });
  const hiddenForMac = !!desktop && open && native?.phase !== 'browse';

  const listing = useRemote<Read>(open && path ? `machine-list:${path}:${hidden}:${version}` : null,
    () => machineApi.list(path ?? '', hidden).then((l): Read => ({ listing: l }), (e: unknown) => {
      const blocked = osPermission(e);
      if (blocked) return { blocked };
      throw e;
    }));
  const root = roots.data?.find((r) => path && within(path, r.path));
  const trail = path ? crumbs(path, root) : [];

  const enter = (entry: MachineEntry) => {
    if (entry.kind === 'dir' || entry.linkTo === 'dir') go(entry.path);
    else if (fileMode && matches(entry.name, accept)) setFile(entry.path);
  };

  const makeFolder = async (e: SyntheticEvent) => {
    e.preventDefault();
    const name = (naming ?? '').trim();
    if (!path || !name) return;
    setMaking(true);
    try {
      const made = await machineApi.mkdir(joinPath(path, name));
      go(made.path);
      toast(`Made ${made.name}`);
    } catch (err) {
      toast.error('No folder made', { description: reason(err) });
    } finally {
      setMaking(false);
    }
  };

  const read = listing.data;
  const entries = read?.listing?.entries ?? [];
  const ready = fileMode ? !!file : !!path && !!read?.listing;
  const shortcuts = [...(roots.data ?? []), ...(places.data ?? []).filter((p) => !(roots.data ?? []).some((r) => r.path === p.path))];
  const shortcutOn = (s: MachineRoot) => !!path && within(path, s.path)
    && !shortcuts.some((o) => o !== s && o.path.length > s.path.length && within(path, o.path));
  return (
    <Dialog open={open && !hiddenForMac} onOpenChange={(o) => { if (!o) close(); }}>
      <DialogContent className="flex max-h-[min(88vh,720px)] flex-col gap-3 sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>
            {description ?? (fileMode
              ? `Files on the machine the NeuroCode API runs on${accept.length ? ` — ${accept.join(', ')}` : ''}.`
              : 'Folders on the machine the NeuroCode API runs on.')}
          </DialogDescription>
        </DialogHeader>

        {native?.note && (
          <p role="status" className="rounded-lg border border-warn/30 bg-warn/8 px-3 py-2 text-[12.5px] leading-relaxed text-ink-2">{native.note}</p>
        )}

        {roots.error ? (
          <Empty icon={<HardDrive className="size-6" />} title="The folders could not be listed" hint={roots.error}
            action={<Button size="sm" variant="outline" onClick={roots.reload}><RefreshCw className="size-3.5" />Try again</Button>} />
        ) : roots.data && roots.data.length === 0 ? (
          <Empty icon={<HardDrive className="size-6" />} title="No folder is open to the browser"
            hint="NEUROCODE_MACHINE_ROOTS in the server's .env names the folders it may open, separated by ':'. None of them exists on this machine." />
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-1.5">
              {shortcuts.map((r) => {
                const Icon = PLACE_ICON[r.label] ?? HardDrive;
                return (
                  <button key={r.path} type="button" onClick={() => go(r.path)} title={r.path}
                    className={cn('inline-flex h-7 items-center gap-1.5 rounded-lg px-2.5 text-[12.5px] transition-colors',
                      shortcutOn(r) ? 'bg-brand/12 text-brand' : 'text-soft hover:bg-surface-2 hover:text-ink')}>
                    <Icon className="size-3.5" />{r.label}
                  </button>
                );
              })}
              <label className="ml-auto flex items-center gap-2 text-[12.5px] text-soft">
                Hidden files
                <Switch checked={hidden} onCheckedChange={setHidden} />
              </label>
            </div>

            <nav aria-label="Folder path" className="flex min-w-0 flex-wrap items-center gap-0.5 text-[12.5px]">
              {trail.map((c, i) => (
                <span key={c.path} className="flex min-w-0 items-center gap-0.5">
                  {i > 0 && <ChevronRight className="size-3 shrink-0 text-dim" />}
                  <button type="button" onClick={() => go(c.path)}
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
              ) : read?.blocked ? (
                <OsPermissionCard blocked={read.blocked} onRetry={() => setVersion((v) => v + 1)} />
              ) : !read?.listing ? (
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
                    <li><Empty title="This folder is empty" hint={read.listing.hidden ? `${read.listing.hidden} hidden ${read.listing.hidden === 1 ? 'entry' : 'entries'} not shown.` : fileMode ? `No ${accept.join(', ')} file here.` : 'Use it as it is, or make a folder inside it.'} /></li>
                  )}
                  {entries.map((e) => {
                    const folder = e.kind === 'dir' || e.linkTo === 'dir';
                    const choosable = fileMode && !folder && (e.kind === 'file' || e.linkTo === 'file') && matches(e.name, accept);
                    const selected = choosable && file === e.path;
                    return (
                      <li key={e.path}>
                        <button type="button" disabled={!folder && !choosable} onClick={() => enter(e)}
                          onDoubleClick={() => { if (choosable) pick(e.path); }}
                          aria-pressed={choosable ? selected : undefined}
                          className={cn('flex w-full items-center gap-2.5 px-4 py-2 text-left text-[13px]',
                            selected ? 'bg-brand/10 text-ink' : folder || choosable ? 'text-ink-2 hover:bg-surface-2/70' : 'cursor-default text-dim')}>
                          {e.kind === 'link' ? <Link2 className="size-4 shrink-0" />
                            : folder ? <Folder className="size-4 shrink-0 text-brand" />
                              : choosable ? <FileArchive className="size-4 shrink-0 text-brand" /> : <File className="size-4 shrink-0" />}
                          <span className="min-w-0 flex-1 truncate">{e.name}</span>
                          {e.git && <Tag tone="info"><GitBranch className="mr-1 inline size-3" />repository</Tag>}
                          {e.kind === 'link' && !e.linkTo && <span className="text-[12px] text-dim">points outside</span>}
                          {fileMode && !folder && e.size !== null && <span className="tnum hidden shrink-0 text-[12px] text-dim sm:inline">{bytes(e.size)}</span>}
                          {fileMode && <span className="tnum hidden w-24 shrink-0 text-right text-[12px] text-dim sm:inline">{when(e.modified)}</span>}
                          {folder && <ChevronRight className="size-3.5 shrink-0 text-dim" />}
                        </button>
                      </li>
                    );
                  })}
                  {read.listing.capped && (
                    <li className="px-4 py-2.5 text-[12.5px] text-dim">Showing the first {entries.length.toLocaleString()} of {read.listing.total.toLocaleString()} entries.</li>
                  )}
                </ul>
              )}
            </div>
          </>
        )}

        <DialogFooter className="flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <div className="min-w-0 truncate font-mono text-[12px] text-dim" title={(fileMode ? file : path) ?? ''}>
            {fileMode ? file ?? `Choose a ${accept.join(', ')} file` : path ?? ''}
          </div>
          <div className="flex shrink-0 flex-wrap gap-2">
            {!fileMode && <Button variant="outline" size="sm" disabled={!path || naming !== null} onClick={() => setNaming('')}><FolderPlus className="size-3.5" />New folder</Button>}
            <Button variant="outline" size="sm" onClick={() => { setVersion((v) => v + 1); }} disabled={!path}><RefreshCw className="size-3.5" /><span className="sr-only">Refresh</span></Button>
            {desktop && <Button variant="outline" size="sm" onClick={() => void askMac()}><AppWindow className="size-3.5" />Finder…</Button>}
            <Button variant="ghost" size="sm" onClick={close}>Cancel</Button>
            <Button size="sm" disabled={!ready} onClick={() => { const p = fileMode ? file : path; if (p) pick(p); }}>
              {confirmLabel ?? (fileMode ? 'Use this file' : 'Use this folder')}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
