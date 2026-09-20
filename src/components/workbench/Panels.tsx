import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { ChevronDown, ChevronUp, Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { announce, problemsStore, useProblems } from '@/lib/live/diagnostics';
import type { MachineGit } from '@/lib/live/machine';
import { cn } from '@/lib/utils';

// xterm.js and the debugger's screens load with the panel, not with the editor.
const TerminalPanel = lazy(() => import('@/components/workbench/TerminalPanel').then((m) => ({ default: m.TerminalPanel })));
const RunPanel = lazy(() => import('@/components/workbench/RunPanel').then((m) => ({ default: m.RunPanel })));
const DebugPanel = lazy(() => import('@/components/workbench/DebugPanel').then((m) => ({ default: m.DebugPanel })));
const ProblemsPanel = lazy(() => import('@/components/workbench/ProblemsPanel').then((m) => ({ default: m.ProblemsPanel })));
const ChangesPanel = lazy(() => import('@/components/workbench/ChangesPanel').then((m) => ({ default: m.ChangesPanel })));

/** Where the debugger stands: an absolute path on this machine and a 1-based line. */
export interface PausedAt { path: string; line: number }

export type PanelTab = 'terminal' | 'problems' | 'changes' | 'run' | 'debug';

/** What the Workbench hands its bottom panel: where a terminal starts, and the editor ↔ debugger contract. */
export interface PanelProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The active project, or null while a folder from the machine is open instead. */
  projectId: string | null;
  /** The root holding the open file (or the first root): where a new terminal starts. */
  cwd: string | null;
  /** Breakpoint lines by absolute path; the editor's gutter toggles them, the Debug panel sends them. */
  breakpoints: Record<string, number[]>;
  /** The file open in the editor; the Debug panel offers it as "This file". */
  currentFile: string | null;
  /** The Debug panel says where it stopped (null when it runs again or ends); the editor highlights and scrolls. */
  onPaused: (at: PausedAt | null) => void;
  openFile: (path: string, line?: number) => void;
  /** The repository holding the open file (or the first root), as git last reported it. */
  git: MachineGit | null;
  /** The label that checkout has as a further source of the project; null for the project's first source. */
  gitSource: string | null;
  /** A commit or a discard changes every letter the tree draws: this reads git again. */
  onGitChanged: () => void;
}

const PANEL_TABS: { id: PanelTab; label: string }[] = [
  { id: 'terminal', label: 'Terminal' },
  { id: 'problems', label: 'Problems' },
  { id: 'changes', label: 'Changes' },
  { id: 'run', label: 'Run' },
  { id: 'debug', label: 'Debug' },
];

/** The strip over the bottom panel: its tabs, and the button that folds it away. */
export function PanelStrip({ tab, onTab, open, onOpenChange, changed = 0 }: {
  tab: PanelTab; onTab: (t: PanelTab) => void; open: boolean; onOpenChange: (open: boolean) => void;
  /** How many files this checkout has changed since its last commit — the number that blocks a merge. */
  changed?: number;
}) {
  // The Problems tab carries what the newest checks found, so it is worth opening before it is opened.
  const { counts } = useProblems();
  const found = counts ? counts.error + counts.warning : 0;
  return (
    <div role="tablist" aria-label="Panel" className="flex h-9 shrink-0 items-center gap-1 border-b border-line/60 px-2">
      {PANEL_TABS.map((t) => (
        <button key={t.id} type="button" role="tab" aria-selected={open && tab === t.id}
          onClick={() => { onTab(t.id); onOpenChange(true); }}
          className={cn('h-7 rounded-md px-2.5 text-[12.5px] transition-colors',
            open && tab === t.id ? 'bg-surface-2 text-ink' : 'text-soft hover:text-ink')}>
          {t.label}
          {t.id === 'changes' && changed > 0 && (
            <span className="ml-1.5 rounded-full bg-warn/15 px-1.5 text-[11px] tnum text-warn">{changed > 999 ? '999+' : changed}</span>
          )}
          {t.id === 'problems' && found > 0 && (
            <span className={cn('ml-1.5 rounded-full px-1.5 text-[11px] tnum', counts?.error ? 'bg-danger/15 text-danger' : 'bg-warn/15 text-warn')}>
              {found > 999 ? '999+' : found}
            </span>
          )}
        </button>
      ))}
      <Button size="icon-xs" variant="ghost" className="ml-auto" aria-label={open ? 'Fold the panel away' : 'Show the panel'} onClick={() => onOpenChange(!open)}>
        {open ? <ChevronDown className="size-3.5" /> : <ChevronUp className="size-3.5" />}
      </Button>
    </div>
  );
}

/** The bottom panel: Terminal, Problems, Run and Debug, each the terminal build's own component. A tab stays
 *  mounted once it has been shown — folding the panel or switching tabs must not drop a terminal's connection or
 *  a debug session's stream — and is only hidden. */
export function WorkbenchPanels(props: PanelProps) {
  const [tab, setTab] = useState<PanelTab>('terminal');
  const [seen, setSeen] = useState<Set<PanelTab>>(() => new Set());
  if (props.open && !seen.has(tab)) setSeen(new Set(seen).add(tab));

  // The editor's go to definition and the status bar's counts reach the Workbench through here: the panel is
  // what the Workbench hands its way to open a file, and the counts belong to what it shows.
  const { openFile, onOpenChange, projectId, cwd } = props;
  useEffect(() => {
    problemsStore.setOpener(openFile);
    return () => problemsStore.setOpener(null);
  }, [openFile]);
  const shows = projectId ? `p:${projectId}` : cwd ? `f:${cwd}` : '';
  useEffect(() => {
    problemsStore.clear();
    void announce(shows.startsWith('p:') ? { projectId: shows.slice(2) } : shows ? { folder: shows.slice(2) } : null, false);
  }, [shows]);
  // The status bar's counts bring the Problems tab forward.
  const { reveal } = useProblems();
  const [seenReveal, setSeenReveal] = useState(reveal);
  if (reveal !== seenReveal) {
    setSeenReveal(reveal);
    setTab('problems');
  }
  const revealAtStart = useRef(reveal);
  useEffect(() => {
    if (reveal !== revealAtStart.current) onOpenChange(true);
  }, [reveal, onOpenChange]);
  const shown = (t: PanelTab) => cn('absolute inset-0 overflow-hidden', !(props.open && tab === t) && 'hidden');
  const waiting = <div className="flex h-full items-center justify-center gap-2 text-[13px] text-dim"><Loader2 className="size-4 animate-spin" />Loading…</div>;
  return (
    <section className={cn('flex shrink-0 flex-col border-t border-line/70 bg-surface', props.open ? 'h-[360px] md:h-[300px]' : 'h-9')}>
      <PanelStrip tab={tab} onTab={setTab} open={props.open} onOpenChange={props.onOpenChange}
        changed={props.git?.changed.length ?? 0} />
      <div className={cn('relative min-h-0 flex-1', !props.open && 'hidden')}>
        <Suspense fallback={waiting}>
          {seen.has('terminal') && <div className={shown('terminal')}><TerminalPanel projectId={props.projectId} cwd={props.cwd} /></div>}
          {seen.has('problems') && <div className={shown('problems')}><ProblemsPanel projectId={props.projectId} cwd={props.cwd} openFile={props.openFile} /></div>}
          {seen.has('changes') && (
            <div className={shown('changes')}>
              <ChangesPanel projectId={props.projectId} git={props.git} source={props.gitSource}
                onChanged={props.onGitChanged} openFile={props.openFile} />
            </div>
          )}
          {seen.has('run') && <div className={shown('run')}><RunPanel projectId={props.projectId} /></div>}
          {seen.has('debug') && (
            <div className={shown('debug')}>
              <DebugPanel projectId={props.projectId} breakpoints={props.breakpoints} onPausedAt={props.onPaused}
                openFile={props.openFile} currentFile={props.currentFile} />
            </div>
          )}
        </Suspense>
      </div>
    </section>
  );
}
