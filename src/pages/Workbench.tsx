import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore, type KeyboardEvent as ReactKeyboardEvent } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  BookOpen, Code2, Eye, EyeOff, File, FileText, FileWarning, FolderOpen, FolderTree, Loader2, Lock, PanelBottom, RefreshCw, Search,
  ShieldAlert, Table2, X,
} from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Sheet, SheetContent, SheetTitle } from '@/components/ui/sheet';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { Empty, Kbd, Page } from '@/components/os';
import type { EditorHandle } from '@/components/workbench/Editor';
import type { NotebookHandle } from '@/components/workbench/NotebookView';
import { FileTree, type TreeRoot } from '@/components/workbench/FileTree';
import { FolderPicker } from '@/components/workbench/FolderPicker';
import { StatusBar } from '@/components/workbench/StatusBar';
import { WorkbenchPanels, type PausedAt } from '@/components/workbench/Panels';
import { ApiError } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import {
  baseName, bytes, dirName, joinPath, machineApi, within, type MachineEntry, type MachineGit,
} from '@/lib/live/machine';
import { readOnly, referencesApi, sourcesApi, type ProjectReference, type ProjectSource } from '@/lib/live/sources';
import { useProject } from '@/lib/project-context';
import { useRemote } from '@/lib/remote';
import { useTheme } from '@/lib/theme';
import { cn } from '@/lib/utils';
import type { Project } from '@/types';

// CodeMirror is the heaviest thing here; it loads with the first file, not with the screen.
const Editor = lazy(() => import('@/components/workbench/Editor'));
// A notebook brings CodeMirror and a kernel's socket; a data file its grid. Each loads when one is opened.
const NotebookView = lazy(() => import('@/components/workbench/NotebookView'));
const DataView = lazy(() => import('@/components/workbench/DataView'));

const reason = (e: unknown) => (e instanceof ApiError ? e.message : 'The local API did not answer. Is it still running?');

/** What the Workbench has open: the active project's folders, or one folder opened from the machine. */
type Place = { kind: 'project' } | { kind: 'folder'; path: string };

/** Files shown in a view of their own rather than as text: a notebook, or a table of data. */
type View = 'notebook' | 'data';
function viewOf(path: string): View | null {
  const name = path.toLowerCase();
  if (name.endsWith('.ipynb')) return 'notebook';
  if (/\.(csv|tsv|parquet|jsonl|ndjson|db|sqlite|sqlite3)$/.test(name)) return 'data';
  return null;
}
/** Of those, the ones that are text underneath, which can also be opened as text. */
const TEXTUAL = /\.(ipynb|csv|tsv|jsonl|ndjson)$/i;

interface Tab {
  path: string;
  sha1: string;
  /** The text as opened or last reloaded — the editor holds the live text. Null for a file it cannot show. */
  text: string | null;
  version: number;
  dirty: boolean;
  size: number;
  reason: 'binary' | 'not UTF-8' | null;
  language: string | null;
  /** Shown in its own view (a notebook, a table) instead of the editor; null when shown as text. */
  view: View | null;
}

/** A tab for a file its own view reads: nothing is read here, the view reads what it needs. */
const viewTab = (path: string, view: View): Tab => ({
  path, sha1: '', text: null, version: 1, dirty: false, size: 0, reason: null, language: view === 'notebook' ? 'Jupyter' : 'Data', view,
});

/* Per-browser conveniences only: what was open, and the breakpoints set per project. Nothing here is a
   record anyone else needs, so it stays in this browser; a failure to read or write it is said in the
   console and otherwise ignored. */
const PLACE_KEY = 'nc.workbench.place';
/** One empty list, so a file without breakpoints does not hand the editor a new array on every keystroke. */
const NO_LINES: number[] = [];
const TABS_KEY = 'nc.workbench.tabs';
const BREAKPOINTS_KEY = 'nc.workbench.breakpoints.';

function stored<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch (e) {
    console.warn(`[NeuroCode] ${key} could not be read from this browser:`, e);
    return fallback;
  }
}
function store(key: string, value: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch (e) {
    console.warn(`[NeuroCode] ${key} could not be kept in this browser:`, e);
  }
}

export default function Workbench() {
  const { can, machine } = useAuth();
  if (!machine) {
    return (
      <Page>
        {can('machine:access') ? (
          <Empty icon={<ShieldAlert className="size-6" />} title="This server opens no folders"
            hint="The Workbench works on the machine the NeuroCode API runs on, and this API was started with NEUROCODE_MACHINE_ACCESS=false — so it opens no files, terminals, runs or debuggers. Run NeuroCode on your own machine for those; everything else here works either way." />
        ) : (
          <Empty icon={<ShieldAlert className="size-6" />} title="The Workbench opens folders on this machine"
            hint="It reads and saves files, and opens terminals, on the machine the NeuroCode API runs on. That needs the “Use this machine” permission, which the Owner role holds." />
        )}
      </Page>
    );
  }
  return <Bench />;
}

/** A project's sources as tree roots. A source whose folder is not here says why instead of listing. A reference
    source is read only. */
function sourceRoots(sources: ProjectSource[]): TreeRoot[] {
  return [...sources].sort((a, b) => (a.primary ? -1 : b.primary ? 1 : a.position - b.position)).map((s) => ({
    key: s.primary ? 'primary' : `source-${s.id}`,
    label: s.label,
    path: s.root ?? null,
    git: null,
    readOnly: readOnly(s),
    note: s.status === 'onboarding' ? 'Still onboarding. Its folder appears here once it is on this machine.'
      : s.status === 'failed' ? `Onboarding failed${s.note ? `: ${s.note}` : '.'}`
        : `Not on this machine${s.kind === 'git' ? ` — ${s.repo} has no checkout here` : ''}.`,
  }));
}

/** The projects this one references, each source a read-only root under a "References" heading. */
function referenceRoots(refs: { ref: ProjectReference; sources: ProjectSource[] }[]): TreeRoot[] {
  return refs.flatMap(({ ref, sources }) => sourceRoots(sources).map((r) => ({
    ...r,
    key: `ref:${ref.project.id}:${r.key}`,
    label: sources.length > 1 ? `${ref.project.name} · ${r.label}` : ref.project.name,
    readOnly: true,
    group: 'References',
  })));
}

/** The primary folder of a project whose server does not list sources yet: a local project's own path. */
function fallbackSources(project: Project): ProjectSource[] {
  if (project.source?.kind !== 'local') return [];
  return [{
    id: null, label: project.id, kind: 'local', repo: project.source.repo, branch: '', position: 0, status: 'active', note: '',
    primary: true, createdAt: null, root: project.source.repo,
  }];
}

/** A project path as a session cites it — label-prefixed for an additional source, plain for the primary — made absolute. */
function locate(path: string, roots: TreeRoot[]): string | null {
  if (path.startsWith('/') || path.startsWith('~')) return path;
  // `payments:app/charge.py` — a referenced project's file, as a session names it.
  const prefixed = /^([a-z0-9][a-z0-9-]*):(.+)$/.exec(path);
  if (prefixed) {
    const theirs = roots.filter((r) => r.key.startsWith(`ref:${prefixed[1]}:`));
    return theirs.length ? locate(prefixed[2], theirs.map((r) => ({ ...r, key: r.key.endsWith(':primary') ? 'primary' : r.key }))) : null;
  }
  const clean = path.replace(/^\.\//, '');
  const extra = roots.find((r) => r.key !== 'primary' && r.path && clean.startsWith(`${r.label}/`));
  if (extra?.path) return joinPath(extra.path, clean.slice(extra.label.length + 1));
  const primary = roots.find((r) => r.key === 'primary' && r.path) ?? roots.find((r) => r.path);
  return primary?.path ? joinPath(primary.path, clean) : null;
}

/** A quick-open score: every character in order, consecutive runs and name matches count more. Null when it does not match. */
function score(query: string, candidate: string): number | null {
  const q = query.toLowerCase();
  const s = candidate.toLowerCase();
  let qi = 0;
  let total = 0;
  let last = -2;
  for (let i = 0; i < s.length && qi < q.length; i++) {
    if (s[i] !== q[qi]) continue;
    total += (i === last + 1 ? 3 : 1) + (i === 0 || s[i - 1] === '/' || s[i - 1] === '_' || s[i - 1] === '-' || s[i - 1] === '.' ? 2 : 0);
    last = i;
    qi++;
  }
  if (qi < q.length) return null;
  const name = s.slice(s.lastIndexOf('/') + 1);
  return total + (name.includes(q) ? 12 : 0) + (name.startsWith(q) ? 6 : 0) - s.length * 0.01;
}

/** Whether the tree fits beside the editor (Tailwind's md), so it is drawn once: beside it, or in a drawer. */
const WIDE = '(min-width: 768px)';
const onWidth = (change: () => void) => {
  const mq = window.matchMedia(WIDE);
  mq.addEventListener('change', change);
  return () => mq.removeEventListener('change', change);
};
const useWide = () => useSyncExternalStore(onWidth, () => window.matchMedia(WIDE).matches, () => true);

function Bench() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const { project, projectId, setProjectId } = useProject();
  const { baseMode } = useTheme();
  const [place, setPlace] = useState<Place>(() => {
    const folder = new URLSearchParams(window.location.search).get('folder');
    if (folder) return { kind: 'folder', path: folder };
    return stored<Place>(PLACE_KEY, { kind: 'project' });
  });
  useEffect(() => { store(PLACE_KEY, place); }, [place]);

  const [picking, setPicking] = useState(false);
  const [treeOpen, setTreeOpen] = useState(false);
  const wide = useWide();
  const [hidden, setHidden] = useState(false);
  const [panelOpen, setPanelOpen] = useState(true);
  const [treeNonce, setTreeNonce] = useState(0);
  const [gitNonce, setGitNonce] = useState(0);
  const [quickOpen, setQuickOpen] = useState(false);

  // ── what is open: the project's sources, or one folder ──────────
  const sources = useRemote(place.kind === 'project' && project ? `wb-sources:${project.id}:${treeNonce}` : null, async () => {
    if (!project) return [];
    try {
      return await sourcesApi.list(project.id);
    } catch (e) {
      // A server from before projects held several sources has no such route; a local project's own
      // folder is still its one root.
      if (e instanceof ApiError && (e.status === 404 || e.status === 405) && project.source?.kind === 'local') return fallbackSources(project);
      throw e;
    }
  });

  // The projects it references, opened read only beside its own sources. Their failure never hides the project's own tree.
  const refs = useRemote(place.kind === 'project' && project ? `wb-refs:${project.id}:${treeNonce}` : null, async () => {
    if (!project) return [];
    try {
      const listed = await referencesApi.list(project.id);
      return await Promise.all(listed.references.map(async (ref) => ({
        ref, sources: await sourcesApi.list(ref.project.id).catch((e: unknown) => {
          console.warn(`[NeuroCode] the folders of ${ref.project.name} were not read:`, e);
          return [] as ProjectSource[];
        }),
      })));
    } catch (e) {
      console.warn('[NeuroCode] the referenced projects were not read:', e);
      return [];
    }
  });

  const baseRoots: TreeRoot[] = useMemo(() => {
    if (place.kind === 'folder') return [{ key: 'folder', label: baseName(place.path), path: place.path, git: null }];
    return [...sourceRoots(sources.data ?? []), ...referenceRoots(refs.data ?? [])];
  }, [place, sources.data, refs.data]);
  const lockedRoots = baseRoots.filter((r) => r.readOnly && r.path);
  const isLocked = useCallback((path: string) => lockedRoots.some((r) => r.path && within(path, r.path)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [lockedRoots.map((r) => r.path).join('\n')]);

  const rootPaths = baseRoots.map((r) => r.path).filter((p): p is string => !!p);
  const gits = useRemote(rootPaths.length ? `wb-git:${rootPaths.join('\n')}:${gitNonce}` : null, async () => {
    const found = await Promise.all(rootPaths.map((p) => machineApi.git(p).catch((e: unknown) => {
      console.warn(`[NeuroCode] git status for ${p} was not read:`, e);
      return null;
    })));
    return Object.fromEntries(rootPaths.map((p, i) => [p, found[i]])) as Record<string, MachineGit | null>;
  });
  // The last answer stays on screen while a fresh one is read, so the colours do not blink on every save.
  const [lastGits, setLastGits] = useState<Record<string, MachineGit | null>>({});
  if (gits.data && gits.data !== lastGits) setLastGits(gits.data);
  const roots = useMemo(() => baseRoots.map((r) => ({ ...r, git: r.path ? lastGits[r.path] ?? null : null })), [baseRoots, lastGits]);

  // Git colours follow the files: look again whenever the tab comes back into view.
  useEffect(() => {
    const again = () => { if (document.visibilityState === 'visible') setGitNonce((n) => n + 1); };
    document.addEventListener('visibilitychange', again);
    return () => document.removeEventListener('visibilitychange', again);
  }, []);

  const placeKey = place.kind === 'folder' ? `f:${place.path}` : `p:${projectId ?? ''}`;

  // ── tabs ───────────────────────────────────────────────────────
  const [tabs, setTabs] = useState<Tab[]>([]);
  const [active, setActive] = useState<string | null>(null);
  const [opening, setOpening] = useState<string | null>(null);
  const [reveal, setReveal] = useState<{ line: number; nonce: number } | null>(null);
  const [cursor, setCursor] = useState<{ line: number; col: number } | null>(null);
  const [language, setLanguage] = useState<string | null>(null);
  const [conflict, setConflict] = useState<{ path: string; text: string } | null>(null);
  const [closing, setClosing] = useState<string | null>(null);
  const editor = useRef<EditorHandle>(null);
  const tabsRef = useRef(tabs);
  useEffect(() => { tabsRef.current = tabs; });

  const openFile = useCallback(async (path: string, line?: number) => {
    const open = tabsRef.current.find((t) => t.path === path);
    if (open) { setActive(path); setTreeOpen(false); if (line) setReveal({ line, nonce: Date.now() }); return; }
    const view = viewOf(path);
    if (view) {
      setTabs((was) => (was.some((t) => t.path === path) ? was : [...was, viewTab(path, view)]));
      setActive(path);
      setTreeOpen(false);
      return;
    }
    setOpening(path);
    try {
      const file = await machineApi.file(path);
      // The server answers with the real path; a link opened by its own name lands on its target's tab.
      setTabs((was) => (was.some((t) => t.path === file.path) ? was : [...was, {
        path: file.path, sha1: file.sha1, text: file.text, version: 1, dirty: false, size: file.size, reason: file.reason, language: file.language,
        view: null,
      }]));
      setActive(file.path);
      setTreeOpen(false);
      if (line) setReveal({ line, nonce: Date.now() });
    } catch (e) {
      toast.error(`${baseName(path)} did not open`, { description: reason(e) });
    } finally {
      setOpening(null);
    }
  }, []);

  // Reopen what was open last time in this browser; a file that is gone is quietly left closed.
  const restored = useRef(false);
  useEffect(() => {
    if (restored.current) return;
    restored.current = true;
    const last = stored<{ paths: string[]; active: string | null }>(TABS_KEY, { paths: [], active: null });
    void (async () => {
      for (const p of last.paths.slice(0, 20)) {
        const view = viewOf(p);
        if (view) { setTabs((was) => (was.some((t) => t.path === p) ? was : [...was, viewTab(p, view)])); continue; }
        try {
          const file = await machineApi.file(p);
          setTabs((was) => (was.some((t) => t.path === file.path) ? was : [...was, {
            path: file.path, sha1: file.sha1, text: file.text, version: 1, dirty: false, size: file.size, reason: file.reason, language: file.language,
            view: null,
          }]));
        } catch { /* moved, deleted or no longer inside the roots: not reopened */ }
      }
      if (last.active) setActive((now) => now ?? last.active);
    })();
  }, []);
  useEffect(() => { store(TABS_KEY, { paths: tabs.map((t) => t.path), active }); }, [tabs, active]);

  const activeTab = tabs.find((t) => t.path === active) ?? null;
  const openPaths = useMemo(() => tabs.map((t) => t.path), [tabs]);
  // The file the editor holds: the active one when it is text, else the text file shown before it.
  const [lastText, setLastText] = useState<string | null>(null);
  if (activeTab?.text != null && lastText !== activeTab.path) setLastText(activeTab.path);
  const textTab = tabs.find((t) => t.path === (activeTab?.text != null ? activeTab.path : lastText) && t.text !== null) ?? null;
  const unsaved = tabs.filter((t) => t.dirty).length;

  // Open notebooks, for saving from outside them and for shutting their kernel down when their tab closes.
  const notebookHandles = useRef(new Map<string, NotebookHandle>());
  const handleRefs = useRef(new Map<string, (h: NotebookHandle | null) => void>());
  const notebookRef = (path: string) => {
    let set = handleRefs.current.get(path);
    if (!set) {
      set = (h) => { if (h) notebookHandles.current.set(path, h); else notebookHandles.current.delete(path); };
      handleRefs.current.set(path, set);
    }
    return set;
  };
  const markDirty = useCallback((path: string, dirty: boolean) => {
    setTabs((was) => was.map((t) => (t.path === path && t.dirty !== dirty ? { ...t, dirty } : t)));
  }, []);

  // A tab closed while active hands the focus to its neighbour. A notebook's kernel ends with its tab.
  const dropTab = (path: string) => {
    const was = tabsRef.current;
    if (was.find((t) => t.path === path)?.view === 'notebook') void notebookHandles.current.get(path)?.close();
    const i = was.findIndex((t) => t.path === path);
    const next = was.filter((t) => t.path !== path);
    setTabs(next);
    if (active === path) setActive(next[Math.min(i, next.length - 1)]?.path ?? null);
    setClosing(null);
  };
  const closeTab = (path: string) => {
    if (tabsRef.current.find((t) => t.path === path)?.dirty) setClosing(path);
    else dropTab(path);
  };

  const save = useCallback(async (path: string, text: string, expect?: string): Promise<boolean> => {
    const tab = tabsRef.current.find((t) => t.path === path);
    if (!tab || tab.text === null) return false;
    if (isLocked(path)) {
      toast(`${baseName(path)} is read only`, { description: 'It is in a reference: read for search and grounding, never written from here.' });
      return false;
    }
    try {
      const saved = await machineApi.save(path, text, expect ?? tab.sha1);
      setTabs((was) => was.map((t) => (t.path === path ? { ...t, sha1: saved.sha1, size: saved.size } : t)));
      editor.current?.saved(path, text);
      setGitNonce((n) => n + 1);
      return true;
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 && e.message.includes('changed on disk')) setConflict({ path, text });
      else toast.error(`${baseName(path)} was not saved`, { description: reason(e) });
      return false;
    }
  }, [isLocked]);

  const reload = async (path: string) => {
    try {
      const file = await machineApi.file(path);
      setTabs((was) => was.map((t) => (t.path === path ? { ...t, sha1: file.sha1, text: file.text, size: file.size, reason: file.reason, version: t.version + 1, dirty: false } : t)));
      setConflict(null);
    } catch (e) {
      toast.error(`${baseName(path)} was not read again`, { description: reason(e) });
    }
  };

  const overwrite = async (path: string, text: string) => {
    try {
      const now = await machineApi.file(path);
      if (await save(path, text, now.sha1)) setConflict(null);
    } catch (e) {
      toast.error(`${baseName(path)} was not saved`, { description: reason(e) });
    }
  };

  // A notebook or a CSV file can also be read as the text it is, and back.
  const showAs = async (path: string, asText: boolean) => {
    const tab = tabsRef.current.find((t) => t.path === path);
    if (!tab) return;
    if (tab.dirty) { toast(`Save ${baseName(path)} first`, { description: 'Switching how it is shown drops what is not saved.' }); return; }
    const view = viewOf(path);
    if (!asText) {
      if (view) setTabs((was) => was.map((t) => (t.path === path ? viewTab(path, view) : t)));
      return;
    }
    try {
      const file = await machineApi.file(path);
      setTabs((was) => was.map((t) => (t.path === path ? {
        ...t, sha1: file.sha1, text: file.text, size: file.size, reason: file.reason, language: file.language, version: t.version + 1, dirty: false, view: null,
      } : t)));
    } catch (e) {
      toast.error(`${baseName(path)} did not open as text`, { description: reason(e) });
    }
  };

  // ── breakpoints and the debugger's position ─────────────────────
  const [breakpoints, setBreakpoints] = useState<Record<string, number[]>>(() => stored(BREAKPOINTS_KEY + placeKey, {}));
  const [bpKey, setBpKey] = useState(placeKey);
  if (bpKey !== placeKey) { setBpKey(placeKey); setBreakpoints(stored(BREAKPOINTS_KEY + placeKey, {})); }
  useEffect(() => { store(BREAKPOINTS_KEY + bpKey, breakpoints); }, [bpKey, breakpoints]);
  const toggleBreakpoint = useCallback((path: string, line: number) => {
    setBreakpoints((was) => {
      const lines = was[path] ?? [];
      const next = lines.includes(line) ? lines.filter((l) => l !== line) : [...lines, line].sort((a, b) => a - b);
      const out = { ...was, [path]: next };
      if (!next.length) delete out[path];
      return out;
    });
  }, []);
  const [pausedAt, setPausedAt] = useState<PausedAt | null>(null);
  const onPaused = useCallback((at: PausedAt | null) => {
    setPausedAt(at);
    if (at) void openFile(at.path, at.line);
  }, [openFile]);

  // ── links from elsewhere: ?project=…&path=…&line=… and ?folder=… ─
  // A link to a folder (?folder=) opens it, also when the Workbench is already on screen.
  const folderParam = params.get('folder');
  const [seenFolder, setSeenFolder] = useState(folderParam);
  if (folderParam !== seenFolder) {
    setSeenFolder(folderParam);
    if (folderParam) setPlace({ kind: 'folder', path: folderParam });
  }

  const wanted = params.get('project');
  // A link names a project once: it is switched to when the link arrives, and the person may switch away after.
  const [seenWanted, setSeenWanted] = useState<string | null>(null);
  if (wanted !== seenWanted) {
    setSeenWanted(wanted);
    if (wanted) setPlace({ kind: 'project' });
  }
  const applied = useRef<string | null>(null);
  useEffect(() => {
    if (!wanted || applied.current === wanted) return;
    applied.current = wanted;
    if (wanted !== projectId) setProjectId(wanted);
  }, [wanted, projectId, setProjectId]);
  const handled = useRef<string | null>(null);
  useEffect(() => {
    const path = params.get('path');
    if (!path) return;
    const key = params.toString();
    if (handled.current === key) return;
    const absolute = path.startsWith('/') || path.startsWith('~');
    if (!absolute && (place.kind !== 'project' || !sources.data || (wanted && wanted !== projectId))) return;
    const target = locate(path, roots);
    handled.current = key;
    if (!target) { toast.error(`${path} is not in a folder on this machine`); return; }
    const line = Number(params.get('line')) || undefined;
    // Opening reads the file from the machine: the link is synchronised with something outside React.
    // oxlint-disable-next-line react/set-state-in-effect
    void openFile(target, line);
  }, [params, place.kind, sources.data, roots, wanted, projectId, openFile]);

  // ── keys: ⌘P finds a file, ⌘S saves when the editor does not have the focus ─
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.metaKey || e.ctrlKey) || e.altKey) return;
      const key = e.key.toLowerCase();
      if (key === 'p' && !e.shiftKey) { e.preventDefault(); setQuickOpen(true); }
      if (key === 's' && !e.defaultPrevented && active) {
        e.preventDefault();
        const notebook = notebookHandles.current.get(active);
        if (notebook && tabsRef.current.find((t) => t.path === active)?.view === 'notebook') { void notebook.save(); return; }
        const text = editor.current?.text(active);
        if (text !== null && text !== undefined) void save(active, text);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [active, save]);

  // Leaving with unsaved text: the browser asks first.
  useEffect(() => {
    if (!unsaved) return;
    const stay = (e: BeforeUnloadEvent) => { e.preventDefault(); };
    window.addEventListener('beforeunload', stay);
    return () => window.removeEventListener('beforeunload', stay);
  }, [unsaved]);

  const onCreated = (entry: MachineEntry) => {
    setGitNonce((n) => n + 1);
    if (entry.kind === 'file') void openFile(entry.path);
  };

  // The repository shown in the status bar: the one holding the open file, else the first root's.
  const statusGit = useMemo(() => {
    const all = roots.map((r) => r.git).filter((g): g is MachineGit => !!g);
    if (active) {
      const holding = all.filter((g) => within(active, g.root)).sort((a, b) => b.root.length - a.root.length)[0];
      if (holding) return holding;
    }
    return all[0] ?? null;
  }, [roots, active]);

  // Which of the project's own sources that repository is, so a commit goes to the right checkout:
  // the first source needs no label, a further one is named by the label the project gave it.
  const gitSource = useMemo(() => {
    if (place.kind !== 'project' || !statusGit) return null;
    const holding = roots.find((r) => r.path && r.git && r.git.root === statusGit.root && !r.readOnly);
    return holding && holding.key !== 'primary' && !holding.key.startsWith('ref:') ? holding.label : null;
  }, [place.kind, roots, statusGit]);
  // A repository of a referenced project is read only here, so it has nothing to commit through this one.
  const ownGit = useMemo(() => {
    if (!statusGit) return null;
    const holding = roots.find((r) => r.path && r.git && r.git.root === statusGit.root);
    return holding?.readOnly ? null : statusGit;
  }, [roots, statusGit]);

  const cwd = useMemo(() => {
    if (active) {
      const holding = roots.filter((r) => r.path && within(active, r.path)).sort((a, b) => (b.path?.length ?? 0) - (a.path?.length ?? 0))[0];
      if (holding?.path) return holding.path;
    }
    return roots.find((r) => r.path)?.path ?? null;
  }, [roots, active]);

  const title = place.kind === 'folder' ? baseName(place.path) : project?.name ?? 'No project';
  const subtitle = place.kind === 'folder' ? place.path : project ? `${roots.length} ${roots.length === 1 ? 'folder' : 'folders'}` : '';

  const tree = (
    <div className="min-h-0 flex-1 overflow-y-auto">
      {place.kind === 'project' && !project ? (
        <p className="px-4 py-4 text-[12.5px] leading-relaxed text-dim">No project yet. Open a folder, or onboard a project in Projects.</p>
      ) : place.kind === 'project' && sources.error ? (
        <div className="px-4 py-4 text-[12.5px] leading-relaxed">
          <p className="text-danger">The project's folders could not be read: {sources.error}</p>
          <Button size="xs" variant="outline" className="mt-2" onClick={sources.reload}><RefreshCw className="size-3" />Try again</Button>
        </div>
      ) : place.kind === 'project' && !sources.data ? (
        <div className="flex items-center gap-2 px-4 py-4 text-[12.5px] text-dim"><Loader2 className="size-3.5 animate-spin" />Reading the project's folders…</div>
      ) : roots.length === 0 ? (
        <p className="px-4 py-4 text-[12.5px] leading-relaxed text-dim">This project has no folder on this machine. It was onboarded somewhere else, or not from code. Open a folder to work on one here.</p>
      ) : (
        <FileTree roots={roots} activePath={active} hidden={hidden} refresh={treeNonce} onOpen={(p) => void openFile(p)} onCreated={onCreated} />
      )}
    </div>
  );

  return (
    <Page>
      {/* The toolbar: what is open, and the ways to open something else. */}
      <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-line/70 bg-bg px-3 py-2 sm:px-4">
        <div className="flex min-w-0 basis-full items-center gap-2 sm:basis-0 sm:flex-1">
          <Code2 className="size-4 shrink-0 text-brand" />
          <div className="min-w-0">
            <h1 className="truncate text-[14px] font-semibold text-ink">{title}</h1>
            {subtitle && <p className="truncate font-mono text-[11.5px] text-dim">{subtitle}</p>}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {!wide && <Button size="sm" variant="outline" onClick={() => setTreeOpen(true)}><FolderTree className="size-3.5" />Files</Button>}
          {place.kind === 'folder' && project && (
            <Button size="sm" variant="ghost" onClick={() => setPlace({ kind: 'project' })}>Back to {project.name}</Button>
          )}
          <Button size="sm" variant="outline" onClick={() => setPicking(true)}><FolderOpen className="size-3.5" />Open folder…</Button>
          <Tooltip><TooltipTrigger render={<Button size="sm" variant="ghost" aria-label="Go to file" onClick={() => setQuickOpen(true)} />}>
            <Search className="size-3.5" /><span className="hidden sm:inline">Go to file</span><span className="hidden sm:inline"><Kbd>⌘P</Kbd></span>
          </TooltipTrigger><TooltipContent>Find a file by name</TooltipContent></Tooltip>
          <Tooltip><TooltipTrigger render={<Button size="icon-sm" variant="ghost" aria-label={hidden ? 'Hide hidden files' : 'Show hidden files'} onClick={() => setHidden((h) => !h)} />}>
            {hidden ? <EyeOff className="size-3.5" /> : <Eye className="size-3.5" />}
          </TooltipTrigger><TooltipContent>{hidden ? 'Hide dot files' : 'Show dot files'}</TooltipContent></Tooltip>
          <Tooltip><TooltipTrigger render={<Button size="icon-sm" variant="ghost" aria-label="Read the folders again" onClick={() => { setTreeNonce((n) => n + 1); setGitNonce((n) => n + 1); }} />}>
            <RefreshCw className="size-3.5" />
          </TooltipTrigger><TooltipContent>Refresh files and git</TooltipContent></Tooltip>
          <Tooltip><TooltipTrigger render={<Button size="icon-sm" variant={panelOpen ? 'secondary' : 'ghost'} aria-label={panelOpen ? 'Hide the panel' : 'Show the panel'} onClick={() => setPanelOpen((o) => !o)} />}>
            <PanelBottom className="size-3.5" />
          </TooltipTrigger><TooltipContent>Terminal, Run and Debug</TooltipContent></Tooltip>
        </div>
      </div>

      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto md:flex-row md:overflow-hidden">
        {wide ? (
          <aside className="flex w-[272px] shrink-0 flex-col border-r border-line/70 bg-surface">{tree}</aside>
        ) : (
          <Sheet open={treeOpen} onOpenChange={setTreeOpen}>
            <SheetContent side="left" className="gap-0 p-0 pt-10 data-[side=left]:w-[300px] data-[side=left]:max-w-[86vw]">
              <SheetTitle className="sr-only">Files</SheetTitle>
              {tree}
            </SheetContent>
          </Sheet>
        )}

        <div className="flex min-w-0 flex-1 flex-col md:min-h-0">
          {/* Tabs */}
          <div role="tablist" aria-label="Open files" className="flex h-9 shrink-0 items-stretch overflow-x-auto border-b border-line/70 bg-surface no-scrollbar">
            {tabs.map((t) => {
              const on = t.path === active;
              return (
                <div key={t.path} role="tab" aria-selected={on} title={t.path}
                  className={cn('group flex max-w-[220px] shrink-0 items-center gap-1.5 border-r border-line/60 pr-1.5 pl-3 text-[12.5px]',
                    on ? 'bg-bg text-ink shadow-[inset_0_-2px_0_var(--os-brand)]' : 'text-soft hover:bg-surface-2/60 hover:text-ink')}
                  onAuxClick={(e) => { if (e.button === 1) closeTab(t.path); }}>
                  {isLocked(t.path) && <Lock className="size-3 shrink-0 text-dim" aria-label="read only" />}
                  <button type="button" className="min-w-0 truncate py-2 text-left" onClick={() => setActive(t.path)}>{baseName(t.path)}</button>
                  <button type="button" aria-label={t.dirty ? `Close ${baseName(t.path)}, not saved` : `Close ${baseName(t.path)}`}
                    onClick={() => closeTab(t.path)}
                    className="relative grid size-5 shrink-0 place-items-center rounded-md text-dim hover:bg-surface-3 hover:text-ink">
                    {t.dirty && <span className="size-2 rounded-full bg-ink-2 group-hover:hidden" />}
                    <X className={cn('size-3', t.dirty && 'hidden group-hover:block')} />
                  </button>
                </div>
              );
            })}
            {opening && !tabs.some((t) => t.path === opening) && (
              <div className="flex shrink-0 items-center gap-1.5 px-3 text-[12.5px] text-dim"><Loader2 className="size-3 animate-spin" />{baseName(opening)}</div>
            )}
            {activeTab && TEXTUAL.test(activeTab.path) && viewOf(activeTab.path) && (
              <div className="sticky right-0 ml-auto flex shrink-0 items-center bg-surface pr-2 pl-1">
                <Button size="xs" variant="ghost" onClick={() => void showAs(activeTab.path, !!activeTab.view)}>
                  {activeTab.view ? <><FileText className="size-3" />Open as text</>
                    : viewOf(activeTab.path) === 'notebook' ? <><BookOpen className="size-3" />Open as notebook</>
                      : <><Table2 className="size-3" />Open as table</>}
                </Button>
              </div>
            )}
          </div>

          {/* The editor. It stays mounted while a binary file is shown, so the other tabs keep their history. */}
          <div className="relative h-[60vh] min-h-[320px] shrink-0 md:h-auto md:min-h-0 md:flex-1 md:shrink">
            {!activeTab && (
              <Empty icon={<File className="size-6" />} title={roots.length ? 'Open a file from the tree' : 'Open a folder to start'}
                hint={roots.length
                  ? '⌘P finds a file by name. ⌘S saves it to the file on this machine; if someone changed it meanwhile, you are asked first.'
                  : 'Pick any folder on this machine, or onboard a project whose code is here.'}
                action={roots.length ? <Button size="sm" variant="outline" onClick={() => setQuickOpen(true)}><Search className="size-3.5" />Go to file</Button>
                  : <div className="flex flex-wrap justify-center gap-2">
                      <Button size="sm" onClick={() => setPicking(true)}><FolderOpen className="size-3.5" />Open folder…</Button>
                      <Button size="sm" variant="outline" onClick={() => navigate('/projects')}>Projects</Button>
                    </div>} />
            )}
            {activeTab && activeTab.text === null && !activeTab.view && (
              <Empty icon={<FileWarning className="size-6" />} title={activeTab.reason === 'binary' ? 'A binary file' : 'Not UTF-8 text'}
                hint={`${baseName(activeTab.path)} is ${bytes(activeTab.size)}${activeTab.reason === 'binary' ? ' of binary data' : ' in an encoding other than UTF-8'}. The editor opens UTF-8 text, so it is not shown rather than shown wrong.`} />
            )}
            {textTab && (
              <div className={cn('h-full', textTab.path !== active && 'hidden')}>
                <Suspense fallback={<div className="flex h-full items-center justify-center gap-2 text-[13px] text-dim"><Loader2 className="size-4 animate-spin" />Loading the editor…</div>}>
                  <Editor
                    ref={editor}
                    doc={{ path: textTab.path, text: textTab.text ?? '', version: textTab.version }}
                    open={openPaths}
                    dark={baseMode === 'dark'}
                    breakpoints={breakpoints[textTab.path] ?? NO_LINES}
                    onToggleBreakpoint={(line) => toggleBreakpoint(textTab.path, line)}
                    pausedLine={pausedAt && pausedAt.path === textTab.path ? pausedAt.line : null}
                    reveal={reveal}
                    onDirty={(path, dirty) => setTabs((was) => was.map((t) => (t.path === path && t.dirty !== dirty ? { ...t, dirty } : t)))}
                    onSave={(path, text) => void save(path, text)}
                    onCursor={(line, col) => setCursor({ line, col })}
                    onLanguage={setLanguage}
                    readOnly={isLocked(textTab.path)}
                  />
                </Suspense>
              </div>
            )}
            {/* Notebooks and data files stay mounted while another tab is shown: a notebook keeps its kernel's
                socket and its unsaved cells, a table its page and sort. */}
            {tabs.filter((t) => t.view).map((t) => (
              <div key={t.path} className={cn('absolute inset-0', t.path !== active && 'hidden')}>
                <Suspense fallback={<div className="flex h-full items-center justify-center gap-2 text-[13px] text-dim"><Loader2 className="size-4 animate-spin" />Opening {baseName(t.path)}…</div>}>
                  {t.view === 'notebook' ? (
                    <NotebookView ref={notebookRef(t.path)} path={t.path} projectId={place.kind === 'project' ? projectId : null}
                      dark={baseMode === 'dark'} readOnly={isLocked(t.path)} onDirty={markDirty} />
                  ) : (
                    <DataView path={t.path} projectId={place.kind === 'project' ? projectId : null} />
                  )}
                </Suspense>
              </div>
            ))}
          </div>

          <WorkbenchPanels
            open={panelOpen}
            onOpenChange={setPanelOpen}
            projectId={place.kind === 'project' ? projectId : null}
            cwd={cwd}
            breakpoints={breakpoints}
            currentFile={active}
            onPaused={onPaused}
            openFile={(path, line) => void openFile(path, line)}
            git={ownGit}
            gitSource={gitSource}
            onGitChanged={() => { setGitNonce((n) => n + 1); setTreeNonce((n) => n + 1); }}
          />

          <StatusBar git={statusGit} unsaved={unsaved}
            language={activeTab?.text != null ? (language ?? activeTab.language) : activeTab?.view ? activeTab.language : null}
            cursor={activeTab?.text != null ? cursor : null}
            eol={activeTab?.text != null ? (activeTab.text.includes('\r\n') ? 'CRLF' : 'LF') : null} />
        </div>
      </div>

      <FolderPicker open={picking} title="Open a folder" confirmLabel="Open"
        start={place.kind === 'folder' ? place.path : roots.find((r) => r.path)?.path ?? null}
        onClose={() => setPicking(false)}
        onPick={(path) => { setPicking(false); setPlace({ kind: 'folder', path }); setGitNonce((n) => n + 1); }} />

      <QuickOpen open={quickOpen} onClose={() => setQuickOpen(false)} roots={roots} onPick={(path) => { setQuickOpen(false); void openFile(path); }} />

      {/* Saving over a file someone changed: the person decides, never the editor. */}
      <Dialog open={!!conflict} onOpenChange={(o) => { if (!o) setConflict(null); }}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{conflict ? baseName(conflict.path) : ''} changed on disk</DialogTitle>
            <DialogDescription>Something else wrote this file after you opened it. Nothing was saved.</DialogDescription>
          </DialogHeader>
          <p className="font-mono text-[12px] break-all text-dim">{conflict?.path}</p>
          <DialogFooter className="flex-col gap-2 sm:flex-row">
            <Button variant="ghost" onClick={() => setConflict(null)}>Keep editing</Button>
            <Button variant="outline" onClick={() => conflict && void reload(conflict.path)}>Reload from disk</Button>
            <Button variant="destructive" onClick={() => conflict && void overwrite(conflict.path, conflict.text)}>Overwrite with mine</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={!!closing} onOpenChange={(o) => { if (!o) setClosing(null); }}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Close {closing ? baseName(closing) : ''} without saving?</DialogTitle>
            <DialogDescription>It has changes that are not saved to the file.</DialogDescription>
          </DialogHeader>
          <DialogFooter className="flex-col gap-2 sm:flex-row">
            <Button variant="ghost" onClick={() => setClosing(null)}>Cancel</Button>
            <Button variant="outline" onClick={() => closing && dropTab(closing)}>Close without saving</Button>
            <Button onClick={() => {
              if (!closing) return;
              const notebook = tabs.find((t) => t.path === closing)?.view === 'notebook' ? notebookHandles.current.get(closing) : undefined;
              if (notebook) { void notebook.save().then((ok) => { if (ok) dropTab(closing); }); return; }
              const text = editor.current?.text(closing);
              if (text == null) return;
              void save(closing, text).then((ok) => { if (ok) dropTab(closing); });
            }}>Save and close</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Page>
  );
}

/** ⌘P: every file under every root (git's list in a repository), found by the letters of its name. */
function QuickOpen({ open, onClose, roots, onPick }: { open: boolean; onClose: () => void; roots: TreeRoot[]; onPick: (path: string) => void }) {
  const paths = roots.filter((r) => r.path);
  const key = open && paths.length ? `wb-files:${paths.map((r) => r.path).join('\n')}` : null;
  const found = useRemote(key, async () => {
    const lists = await Promise.all(paths.map((r) => machineApi.files(r.path as string).then((f) => ({ root: r, list: f }))));
    return lists;
  });
  const [query, setQuery] = useState('');
  const [cursor, setCursor] = useState(0);
  const close = () => { setQuery(''); setCursor(0); onClose(); };
  const pick = (path: string) => { setQuery(''); setCursor(0); onPick(path); };

  const several = paths.length > 1;
  const all = useMemo(() => (found.data ?? []).flatMap(({ root, list }) =>
    list.files.map((f) => ({ shown: several ? `${root.label}/${f}` : f, path: joinPath(list.root, f) }))), [found.data, several]);
  const capped = (found.data ?? []).some((l) => l.list.capped);
  const hits = useMemo(() => {
    const q = query.trim();
    if (!q) return all.slice(0, 60);
    return all.map((f) => ({ f, s: score(q, f.shown) })).filter((x): x is { f: typeof all[number]; s: number } => x.s !== null)
      .sort((a, b) => b.s - a.s).slice(0, 60).map((x) => x.f);
  }, [all, query]);

  const onKey = (e: ReactKeyboardEvent) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setCursor((c) => Math.min(c + 1, hits.length - 1)); }
    if (e.key === 'ArrowUp') { e.preventDefault(); setCursor((c) => Math.max(c - 1, 0)); }
    if (e.key === 'Enter' && hits[cursor]) { e.preventDefault(); pick(hits[cursor].path); }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) close(); }}>
      <DialogContent className="top-[18%] translate-y-0 gap-0 p-0 sm:max-w-xl" showCloseButton={false}>
        <DialogTitle className="sr-only">Go to file</DialogTitle>
        <DialogDescription className="sr-only">Type part of a file's name or path.</DialogDescription>
        <div className="flex items-center gap-2 border-b border-line/70 px-4">
          <Search className="size-4 shrink-0 text-dim" />
          <input autoFocus value={query} onChange={(e) => { setQuery(e.target.value); setCursor(0); }} onKeyDown={onKey}
            placeholder="Go to file" aria-label="File name"
            className="h-12 min-w-0 flex-1 bg-transparent text-[14px] text-ink placeholder:text-dim focus-visible:outline-none" />
        </div>
        <div className="max-h-[50vh] overflow-y-auto py-1">
          {!paths.length ? (
            <p className="px-4 py-6 text-center text-[13px] text-dim">Open a folder first.</p>
          ) : found.error ? (
            <p className="px-4 py-6 text-center text-[13px] text-danger">{found.error}</p>
          ) : !found.data ? (
            <div className="flex items-center justify-center gap-2 px-4 py-6 text-[13px] text-dim"><Loader2 className="size-4 animate-spin" />Listing the files…</div>
          ) : hits.length === 0 ? (
            <p className="px-4 py-6 text-center text-[13px] text-dim">{all.length ? `No file matches “${query}”.` : 'These folders hold no files.'}</p>
          ) : hits.map((h, i) => {
            const cut = h.shown.lastIndexOf('/');
            return (
              <button key={h.path} type="button" onMouseEnter={() => setCursor(i)} onClick={() => pick(h.path)}
                className={cn('flex w-full items-baseline gap-2 px-4 py-1.5 text-left', i === cursor ? 'bg-brand/10' : '')}>
                <span className="shrink-0 text-[13px] text-ink">{h.shown.slice(cut + 1)}</span>
                <span className="min-w-0 truncate font-mono text-[11.5px] text-dim">{cut > 0 ? dirName(h.shown) : ''}</span>
              </button>
            );
          })}
        </div>
        {found.data && (
          <div className="border-t border-line/70 px-4 py-2 text-[11.5px] text-dim">
            {all.length.toLocaleString()} files{capped ? ' (the list stops at 20,000 per folder)' : ''} · ↑↓ to move · Enter to open
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
