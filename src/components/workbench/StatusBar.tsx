import { ArrowDown, ArrowUp, CircleDot, CircleX, GitBranch, Loader2, TriangleAlert } from 'lucide-react';
import { problemsStore, useProblems } from '@/lib/live/diagnostics';
import type { MachineGit } from '@/lib/live/machine';

/** The Workbench's bottom line: git for the file's repository, the problems the newest checks found, the language
    server for the file shown, and where the cursor is in which language. */
export function StatusBar({ git, language, cursor, eol, unsaved }: {
  git: MachineGit | null;
  language: string | null;
  cursor: { line: number; col: number } | null;
  /** How the open file ends its lines; null with no text file open. */
  eol: 'LF' | 'CRLF' | null;
  /** Open files with changes not yet saved. */
  unsaved: number;
}) {
  const changed = git?.changed.length ?? 0;
  const { counts, checking, lsp } = useProblems();
  const server = lsp && lsp.status.state !== 'none' ? lsp.status : null;
  return (
    <footer className="flex h-7 shrink-0 items-center gap-4 overflow-x-auto border-t border-line/70 bg-surface px-3 text-[12px] whitespace-nowrap text-soft no-scrollbar">
      {git ? (
        <>
          <span className="inline-flex items-center gap-1.5" title={git.upstream ? `Tracking ${git.upstream}` : 'No upstream branch'}>
            <GitBranch className="size-3.5" />
            <span className="font-mono">{git.branch ?? (git.head ? `detached at ${git.head.slice(0, 7)}` : 'no commits yet')}</span>
            {git.ahead > 0 && <span className="inline-flex items-center tnum"><ArrowUp className="size-3" />{git.ahead}</span>}
            {git.behind > 0 && <span className="inline-flex items-center tnum"><ArrowDown className="size-3" />{git.behind}</span>}
          </span>
          <span className="inline-flex items-center gap-1.5 tnum">
            <CircleDot className={changed ? 'size-3.5 text-warn' : 'size-3.5'} />
            {changed === 0 ? 'No changes' : `${changed.toLocaleString()}${git.capped ? '+' : ''} changed`}
          </span>
        </>
      ) : (
        <span>Not in a git repository</span>
      )}
      {unsaved > 0 && <span className="text-warn tnum">{unsaved} unsaved</span>}
      {(counts || checking) && (
        <button type="button" onClick={() => problemsStore.showProblems()} title="Show the problems"
          aria-label={counts ? `${counts.error} errors and ${counts.warning} warnings — show the problems` : 'Checking — show the problems'}
          className="inline-flex items-center gap-2 rounded px-1 hover:bg-surface-2 hover:text-ink">
          {checking && <Loader2 className="size-3 animate-spin" />}
          {counts && (
            <>
              <span className="inline-flex items-center gap-1 tnum"><CircleX className={counts.error ? 'size-3.5 text-danger' : 'size-3.5'} />{counts.error.toLocaleString()}</span>
              <span className="inline-flex items-center gap-1 tnum"><TriangleAlert className={counts.warning ? 'size-3.5 text-warn' : 'size-3.5'} />{counts.warning.toLocaleString()}</span>
            </>
          )}
        </button>
      )}
      <span className="ml-auto" />
      {server && (
        <span title={server.message} className={server.state === 'failed' ? 'text-danger' : server.state === 'missing' ? 'text-dim' : undefined}>
          {server.state === 'ready' ? server.message
            : server.state === 'starting' ? `${server.server ?? 'Language server'} starting…`
              : server.state === 'available' ? `${server.server} available`
                : server.state === 'missing' ? `No ${server.language} language server`
                  : `${server.server ?? 'Language server'} failed`}
        </span>
      )}
      {cursor && <span className="tnum">Ln {cursor.line}, Col {cursor.col}</span>}
      {eol && <span>{eol}</span>}
      {eol && <span>UTF-8</span>}
      {language && <span>{language}</span>}
    </footer>
  );
}
