import { lazy, Suspense, useState } from 'react';
import { ChevronDown, ChevronUp, Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

// xterm.js and the debugger's screens load with the panel, not with the editor.
const TerminalPanel = lazy(() => import('@/components/workbench/TerminalPanel').then((m) => ({ default: m.TerminalPanel })));
const RunPanel = lazy(() => import('@/components/workbench/RunPanel').then((m) => ({ default: m.RunPanel })));
const DebugPanel = lazy(() => import('@/components/workbench/DebugPanel').then((m) => ({ default: m.DebugPanel })));

/** Where the debugger stands: an absolute path on this machine and a 1-based line. */
export interface PausedAt { path: string; line: number }

export type PanelTab = 'terminal' | 'run' | 'debug';

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
}

const PANEL_TABS: { id: PanelTab; label: string }[] = [
  { id: 'terminal', label: 'Terminal' },
  { id: 'run', label: 'Run' },
  { id: 'debug', label: 'Debug' },
];

/** The strip over the bottom panel: its tabs, and the button that folds it away. */
export function PanelStrip({ tab, onTab, open, onOpenChange }: {
  tab: PanelTab; onTab: (t: PanelTab) => void; open: boolean; onOpenChange: (open: boolean) => void;
}) {
  return (
    <div role="tablist" aria-label="Panel" className="flex h-9 shrink-0 items-center gap-1 border-b border-line/60 px-2">
      {PANEL_TABS.map((t) => (
        <button key={t.id} type="button" role="tab" aria-selected={open && tab === t.id}
          onClick={() => { onTab(t.id); onOpenChange(true); }}
          className={cn('h-7 rounded-md px-2.5 text-[12.5px] transition-colors',
            open && tab === t.id ? 'bg-surface-2 text-ink' : 'text-soft hover:text-ink')}>
          {t.label}
        </button>
      ))}
      <Button size="icon-xs" variant="ghost" className="ml-auto" aria-label={open ? 'Fold the panel away' : 'Show the panel'} onClick={() => onOpenChange(!open)}>
        {open ? <ChevronDown className="size-3.5" /> : <ChevronUp className="size-3.5" />}
      </Button>
    </div>
  );
}

/** The bottom panel: Terminal, Run and Debug, each the terminal build's own component. A tab stays mounted
 *  once it has been shown — folding the panel or switching tabs must not drop a terminal's connection or a
 *  debug session's stream — and is only hidden. */
export function WorkbenchPanels(props: PanelProps) {
  const [tab, setTab] = useState<PanelTab>('terminal');
  const [seen, setSeen] = useState<Set<PanelTab>>(() => new Set());
  if (props.open && !seen.has(tab)) setSeen(new Set(seen).add(tab));
  const shown = (t: PanelTab) => cn('absolute inset-0 overflow-hidden', !(props.open && tab === t) && 'hidden');
  const waiting = <div className="flex h-full items-center justify-center gap-2 text-[13px] text-dim"><Loader2 className="size-4 animate-spin" />Loading…</div>;
  return (
    <section className={cn('flex shrink-0 flex-col border-t border-line/70 bg-surface', props.open ? 'h-[360px] md:h-[300px]' : 'h-9')}>
      <PanelStrip tab={tab} onTab={setTab} open={props.open} onOpenChange={props.onOpenChange} />
      <div className={cn('relative min-h-0 flex-1', !props.open && 'hidden')}>
        <Suspense fallback={waiting}>
          {seen.has('terminal') && <div className={shown('terminal')}><TerminalPanel projectId={props.projectId} cwd={props.cwd} /></div>}
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
