import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { toast } from 'sonner';
import {
  Bell, Check, ChevronDown, Code2, FolderGit2, FolderOpen, Layers, Menu, Palette, Plus, Search, ShieldAlert,
} from 'lucide-react';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { BlockBar, Dot } from '@/components/os';
import { FolderPicker } from '@/components/workbench/FolderPicker';
import { cn } from '@/lib/utils';
import { useAuth } from '@/lib/auth';
import { useTheme } from '@/lib/theme';
import { useProject } from '@/lib/project-context';
import { useData } from '@/lib/data';
import { SOURCE_DOT, sourcesOf } from '@/lib/live/sources';
import type { Project } from '@/types';
import { ThemeCustomizer } from './ThemeCustomizer';
import { MODES } from './theme-modes';

const ROUND = { borderRadius: 'calc(var(--radius) * 1.2)' };
const BTN = 'press flex h-9 items-center gap-2 px-2.5 text-[13px] text-soft transition-colors hover:bg-surface-2 hover:text-ink';
const ITEM = 'flex w-full items-center gap-2 px-2.5 py-2 text-left text-[13px] text-soft transition-colors hover:bg-surface-2 hover:text-ink';
/** What "Open folder…" leaves out, as the onboarding wizard does by default. */
const EXCLUDED = ['node_modules', 'bin', 'obj', 'dist', '**/*.designer.cs'];

/** A project matches when its name, codename, stack or any of its sources' labels hold every word typed. */
function matches(p: Project, q: string): boolean {
  const words = q.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return true;
  const hay = [p.name, p.codename, p.repo, ...p.stack, ...sourcesOf(p).map((x) => x.label)].join(' ').toLowerCase();
  return words.every((w) => hay.includes(w));
}

/** A macOS-style toolbar: the project navigator on the left, a few quiet controls on the right. The navigator
    searches projects and their sources, switches, opens one in the Workbench, starts a new one, and opens a
    folder on this machine as a project in one step. */
export function Topbar({
  onOpenSearch, onOpenNav, projectId, onProject,
}: { onOpenSearch: () => void; onOpenNav?: () => void; projectId: string | null; onProject: (id: string) => void }) {
  const { current, rail, availableColors } = useTheme();
  const [themeOpen, setThemeOpen] = useState(false);
  const [projOpen, setProjOpen] = useState(false);
  const [q, setQ] = useState('');
  const [folderOpen, setFolderOpen] = useState(false);
  const nav = useNavigate();
  const { can } = useAuth();
  const { all: projects, project } = useProject();
  const { approvals, createProject } = useData();
  // Null while nothing is onboarded, which is how every workspace starts: the button then says so.
  const active = projects.find((p) => p.id === projectId) ?? project;
  const activeSources = active ? sourcesOf(active) : [];
  const pending = approvals.filter((a) => a.status === 'pending');
  const workbench = can('machine:access');
  const found = useMemo(() => projects.filter((p) => matches(p, q)), [projects, q]);

  const pick = (id: string, to?: string) => {
    onProject(id);
    setProjOpen(false);
    setQ('');
    if (to) nav(to);
  };

  /** A folder on this machine, onboarded as a project in one step — or, when it already is one, switched to. */
  const openFolder = async (path: string) => {
    setFolderOpen(false);
    const known = projects.find((p) => p.source?.kind === 'local' && p.source.repo.replace(/\/+$/, '') === path.replace(/\/+$/, ''));
    if (known) {
      pick(known.id, `/projects/${encodeURIComponent(known.id)}`);
      toast(`${known.name} is already a project`, { description: 'Switched to it.' });
      return;
    }
    const doc = await createProject({ source: 'local', repo: path, branch: 'main', excluded: EXCLUDED, rules: [] });
    if (!doc) return;
    pick(doc.id, `/projects/${encodeURIComponent(doc.id)}`);
    toast.success(`${doc.name} is onboarding`, { description: 'Measuring and indexing it now. Each stage lands in Activity.' });
  };

  return (
    <header
      className={cn(
        'flex h-14 shrink-0 items-center gap-1 px-2 sm:px-2.5',
        rail === 'flush' ? 'border-b border-line bg-bg' : 'elevated border border-line/70 bg-surface',
      )}
      style={rail === 'flush' ? undefined : { borderRadius: 'calc(var(--radius) * 1.6)' }}
    >
      {onOpenNav && (
        <button onClick={onOpenNav} aria-label="Open navigation" className={cn(BTN, 'lg:hidden')} style={ROUND}>
          <Menu className="size-[18px]" />
        </button>
      )}

      {/* Project — a popup button, or the way to a first one */}
      {!active && (
        <button onClick={() => nav('/projects')} className={cn(BTN, 'min-w-0')} style={ROUND} title="Onboard a repository">
          <span className="truncate text-[14px] font-semibold text-ink">No project yet</span>
          <span className="hidden truncate text-dim sm:inline">· Onboard a repository</span>
        </button>
      )}
      {!active && workbench && can('projects:onboard') && (
        <button onClick={() => setFolderOpen(true)} className={BTN} style={ROUND} title="Open a folder on this machine as a project">
          <FolderOpen className="size-[18px]" /><span className="hidden sm:inline">Open folder…</span>
        </button>
      )}
      {active && (
        <Popover open={projOpen} onOpenChange={(o) => { setProjOpen(o); if (!o) setQ(''); }}>
          <PopoverTrigger className={cn(BTN, 'min-w-0 text-ink')} style={ROUND}>
            <span className="grid size-6 shrink-0 place-items-center rounded-md bg-brand/15 text-[10.5px] font-bold text-brand">
              {active.name.slice(0, 2).toUpperCase()}
            </span>
            <span className="max-w-[120px] truncate text-[14px] font-semibold sm:max-w-[220px]">{active.name}</span>
            {activeSources.length > 1 && (
              <span className="hidden items-center gap-1 text-[12px] text-dim sm:flex" title={activeSources.map((x) => x.label).join(', ')}>
                <Layers className="size-3.5" />{activeSources.length}
              </span>
            )}
            <Dot state={active.status} pulse={active.status === 'active'} />
            <ChevronDown className="size-3.5 text-dim" />
          </PopoverTrigger>
          <PopoverContent align="start" className="w-[min(420px,calc(100vw-1.5rem))] p-1.5">
            <div className="flex h-9 items-center gap-2 rounded-lg border border-line bg-surface-2 px-2.5 focus-within:border-brand">
              <Search className="size-3.5 shrink-0 text-dim" />
              <input
                autoFocus value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search projects and sources"
                placeholder="Search projects and their sources…"
                onKeyDown={(e) => { if (e.key === 'Enter' && found[0]) pick(found[0].id); }}
                className="min-w-0 flex-1 bg-transparent text-[13px] text-ink placeholder:text-dim focus-visible:outline-none"
              />
            </div>
            <div className="px-2.5 pt-2.5 pb-1.5 text-[12px] font-medium text-dim">
              {q.trim() ? `${found.length} of ${projects.length} projects` : 'Projects · context never leaks between them'}
            </div>
            <div className="max-h-[min(60vh,440px)] space-y-0.5 overflow-y-auto">
              {found.length === 0 && (
                <p className="px-2.5 py-6 text-center text-[13px] text-dim">No project or source matches “{q.trim()}”.</p>
              )}
              {found.map((p) => {
                const sources = sourcesOf(p);
                return (
                  <div key={p.id} className={cn('group flex items-start gap-1 transition-colors hover:bg-surface-2', p.id === projectId && 'bg-surface-2')}
                    style={{ borderRadius: 'var(--radius)' }}>
                    <button onClick={() => pick(p.id)} className="flex min-w-0 flex-1 items-start gap-3 px-2.5 py-2.5 text-left">
                      <Dot state={p.status} className="mt-1.5" />
                      <span className="min-w-0 flex-1">
                        <span className="flex items-center gap-2">
                          <span className="truncate text-[14px] font-medium text-ink">{p.name}</span>
                          {p.id === projectId && <Check className="size-3.5 shrink-0 text-brand" />}
                        </span>
                        <span className="mt-0.5 block truncate text-[12.5px] text-dim">{p.stack.slice(0, 3).join(' · ') || p.status}</span>
                        {sources.length > 1 && (
                          <span className="mt-1.5 flex flex-wrap gap-1">
                            {sources.map((x) => (
                              <span key={x.label} className="inline-flex items-center gap-1 rounded-full border border-line bg-surface px-1.5 py-px font-mono text-[11px] text-ink-2">
                                <Dot state={SOURCE_DOT[x.status]} className="size-1.5" />{x.id === null ? 'first source' : `${x.label}/`}
                              </span>
                            ))}
                          </span>
                        )}
                        <span className="mt-1.5 flex items-center gap-2">
                          {p.understoodPct === null
                            ? <span className="text-[12px] text-dim">Not indexed yet</span>
                            : (
                              <>
                                <BlockBar pct={p.understoodPct} width={12} />
                                <span className="tnum text-[12px] text-dim">{p.understoodPct}% of its files indexed</span>
                              </>
                            )}
                        </span>
                      </span>
                    </button>
                    {workbench && p.source && (
                      <button onClick={() => { pick(p.id, `/workbench?project=${encodeURIComponent(p.id)}`); }}
                        aria-label={`Open ${p.name} in Workbench`} title="Open in Workbench"
                        className="mt-1.5 mr-1.5 grid size-8 shrink-0 place-items-center rounded-md text-dim transition-colors hover:bg-surface-3 hover:text-ink">
                        <Code2 className="size-4" />
                      </button>
                    )}
                  </div>
                );
              })}
            </div>
            <div className="mt-1 grid grid-cols-1 gap-0.5 border-t border-line/60 pt-1 sm:grid-cols-2">
              {can('projects:onboard') && (
                <button onClick={() => { setProjOpen(false); nav('/projects?new=1'); }} className={ITEM} style={{ borderRadius: 'var(--radius)' }}>
                  <Plus className="size-3.5" />New project
                </button>
              )}
              {workbench && can('projects:onboard') && (
                <button onClick={() => { setProjOpen(false); setFolderOpen(true); }} className={ITEM} style={{ borderRadius: 'var(--radius)' }}>
                  <FolderOpen className="size-3.5" />Open folder…
                </button>
              )}
              {workbench && (
                <button onClick={() => pick(active.id, `/workbench?project=${encodeURIComponent(active.id)}`)} className={ITEM} style={{ borderRadius: 'var(--radius)' }}>
                  <Code2 className="size-3.5" />Open {active.name} in Workbench
                </button>
              )}
              <button onClick={() => { nav('/projects'); setProjOpen(false); }} className={ITEM} style={{ borderRadius: 'var(--radius)' }}>
                <FolderGit2 className="size-3.5" />Manage all projects
              </button>
            </div>
          </PopoverContent>
        </Popover>
      )}
      <FolderPicker open={folderOpen} title="Open a folder as a project" confirmLabel="Open as a project"
        onClose={() => setFolderOpen(false)} onPick={(path) => void openFolder(path)} />

      <div className="flex-1" />

      {/* The sidebar holds search on a desktop; below that, it lives here. */}
      <button onClick={onOpenSearch} aria-label="Search" className={cn(BTN, 'lg:hidden')} style={ROUND}>
        <Search className="size-[18px]" />
      </button>

      <button
        onClick={() => nav('/permissions')}
        title={`${pending.length} approvals waiting on you`}
        aria-label={`${pending.length} approvals waiting on you`}
        className={cn(BTN, pending.length && 'bg-warn/10 text-warn hover:bg-warn/15 hover:text-warn')}
        style={ROUND}
      >
        <ShieldAlert className="size-[18px]" />
        {pending.length > 0 && <span className="tnum font-semibold">{pending.length}</span>}
        <span className="hidden md:inline">{pending.length ? 'to approve' : 'Approvals'}</span>
      </button>

      <button onClick={() => nav('/activity')} aria-label="Activity" title="Activity" className={cn(BTN, 'hidden sm:flex')} style={ROUND}>
        <Bell className="size-[18px]" />
      </button>

      <button
        onClick={() => setThemeOpen(true)}
        className={BTN}
        style={ROUND}
        title={`Appearance — ${availableColors.length} palettes, ${MODES.length} modes, radius and type`}
      >
        <Palette className="size-[18px]" />
        <span className="hidden xl:inline">{current.name}</span>
        <span className="hidden overflow-hidden rounded-full ring-1 ring-line sm:flex">
          {current.swatch.map((c, i) => <span key={i} className="size-2.5" style={{ background: c }} />)}
        </span>
      </button>
      <ThemeCustomizer open={themeOpen} onOpenChange={setThemeOpen} />
    </header>
  );
}
