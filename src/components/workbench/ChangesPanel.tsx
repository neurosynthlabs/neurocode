import { useMemo, useState } from 'react';
import { Check, GitCommitHorizontal, Loader2, Undo2 } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Empty, Mono, Tag } from '@/components/os';
import { ApiError } from '@/lib/api';
import { DiffLines } from '@/components/workbench/DiffView';
import { baseName, checkoutApi, dirName, GIT_TONE, joinPath, type GitChange, type MachineGit } from '@/lib/live/machine';
import { useAuth } from '@/lib/auth';
import { useRemote } from '@/lib/remote';
import { cn } from '@/lib/utils';

/* Changes: what this checkout has that its last commit does not, and the two things a person can do
   about them from here — commit the files they picked, or put them back.

   This exists because the editor writes into the real working tree, and a working tree with changes
   that are not committed refuses every merge. Before it, the product told you a file was modified
   (the orange M in the tree), would not show you what changed in it, and had no way to commit or
   undo it: the only way on was a terminal. Staging, branching and stashing are still git's — a stash
   this product could make but could never bring back would be a trap. */

const reason = (e: unknown) => (e instanceof ApiError ? e.message : 'The local API did not answer.');

export interface ChangesProps {
  /** The active project; null while a folder was opened on its own, when there is nothing to commit through. */
  projectId: string | null;
  /** The repository holding the open file, as git last reported it. */
  git: MachineGit | null;
  /** The label this checkout has as a further source of the project; null for the project's first source. */
  source: string | null;
  /** Read git again: a commit or a discard changes every letter the tree draws. */
  onChanged: () => void;
  openFile: (path: string) => void;
}

export function ChangesPanel({ projectId, git, source, onChanged, openFile }: ChangesProps) {
  const { can } = useAuth();
  const [picked, setPicked] = useState<string | null>(null);
  // Which files a commit would take. Null until a person touches one: then every changed file is in it.
  const [chosen, setChosen] = useState<readonly string[] | null>(null);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [arming, setArming] = useState<string | null>(null);

  const changed = useMemo(() => git?.changed ?? [], [git]);
  const paths = useMemo(() => changed.map((c) => c.path), [changed]);
  const inCommit = chosen ?? paths;
  const file = picked && paths.includes(picked) ? picked : paths[0] ?? null;
  const mine = can('machine:access');

  // Whenever git has been asked again — a save, a commit, coming back to the tab — the file's diff is
  // read again with it. Counted rather than compared: the same letter on the same file can still be a
  // different diff, which is exactly what saving a file again does.
  const [asked, setAsked] = useState(git);
  const [reads, setReads] = useState(0);
  if (asked !== git) { setAsked(git); setReads((n) => n + 1); }
  const diff = useRemote(projectId && file ? `wb-diff:${projectId}:${source ?? ''}:${file}:${reads}` : null,
    () => checkoutApi.fileDiff(projectId ?? '', file ?? '', source));

  if (!git) {
    return <Empty title="Not a git repository" hint="This folder has no repository, so there is nothing to commit or take back here." />;
  }
  if (!projectId) {
    return (
      <Empty title="Opened as a folder, not a project"
        hint="Committing goes through the project a repository belongs to, so its changes are read and written under that project's own rights. Switch back to the project to commit from here." />
    );
  }
  if (changed.length === 0) {
    return <Empty icon={<Check className="size-5" />} title="Nothing has changed" hint={`Everything in this checkout matches ${git.branch ?? 'the commit it stands on'}.`} />;
  }

  const toggle = (path: string) =>
    setChosen(inCommit.includes(path) ? inCommit.filter((p) => p !== path) : [...inCommit, path]);

  const commit = async () => {
    if (!inCommit.length || !message.trim()) return;
    setBusy(true);
    try {
      const done = await checkoutApi.commit(projectId, [...inCommit], message.trim(), source);
      toast.success(`Committed ${done.files === 1 ? '1 file' : `${done.files} files`} as ${done.commit}`,
        { description: `On ${done.branch}, by ${done.by}.` });
      setMessage('');
      setChosen(null);
      onChanged();
    } catch (e) {
      toast.error('Nothing was committed', { description: reason(e) });
    } finally {
      setBusy(false);
    }
  };

  const discard = async (path: string) => {
    if (arming !== path) { setArming(path); window.setTimeout(() => setArming((a) => (a === path ? null : a)), 4000); return; }
    setArming(null);
    setBusy(true);
    try {
      await checkoutApi.discard(projectId, [path], source);
      toast(`${baseName(path)} is back as the last commit had it`);
      setChosen(null);
      onChanged();
    } catch (e) {
      toast.error(`${baseName(path)} was not taken back`, { description: reason(e) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex h-full min-h-0 flex-col md:flex-row">
      {/* what changed */}
      {/* At a phone's width the list takes at most half the panel and the diff has the rest; side by side from md. */}
      <div className="flex max-h-[55%] w-full shrink-0 flex-col border-b border-line/60 md:max-h-none md:w-[320px] md:border-r md:border-b-0">
        <div className="flex shrink-0 items-center gap-2 px-3 py-2 text-[12px] text-dim">
          <span>{changed.length} changed</span>
          {git.branch && <Mono className="truncate">{git.branch}</Mono>}
          <span className="ml-auto">{inCommit.length} to commit</span>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">
          {changed.map((c) => (
            <ChangeRow key={c.path} change={c} root={git.root} active={c.path === file} inCommit={inCommit.includes(c.path)}
              canWrite={mine} arming={arming === c.path} busy={busy}
              onPick={() => setPicked(c.path)} onToggle={() => toggle(c.path)}
              onOpen={() => openFile(joinPath(git.root, c.path))} onDiscard={() => void discard(c.path)} />
          ))}
          {git.capped && <p className="px-3 py-2 text-[12px] text-warn">More files have changed than this list holds; the rest are not shown.</p>}
        </div>
        <div className="shrink-0 space-y-1.5 border-t border-line/60 p-2.5">
          <textarea value={message} onChange={(e) => setMessage(e.target.value)} rows={2} disabled={!mine || busy}
            placeholder="What changed, and why" aria-label="Commit message"
            className="w-full resize-none rounded-lg border border-line bg-bg px-2.5 py-1.5 text-[12.5px] text-ink placeholder:text-dim focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-brand/50" />
          <Button size="xs" className="w-full" disabled={!mine || busy || !message.trim() || inCommit.length === 0}
            onClick={() => void commit()}
            title={mine ? 'Commits exactly the files ticked, with this machine’s own git identity' : 'Committing needs the “Use this machine” permission'}>
            {busy ? <Loader2 className="size-3 animate-spin" /> : <GitCommitHorizontal className="size-3" />}
            Commit {inCommit.length === 1 ? '1 file' : `${inCommit.length} files`}
          </Button>
        </div>
      </div>

      {/* what it changed */}
      <div className="min-h-0 min-w-0 flex-1 overflow-y-auto p-3">
        {!file ? null : diff.error ? (
          <p className="text-[13px] text-soft">{diff.error}</p>
        ) : !diff.data ? (
          <p className="flex items-center gap-2 text-[12.5px] text-dim"><Loader2 className="size-3.5 animate-spin" />Reading what changed in {baseName(file)}…</p>
        ) : (
          <>
            <div className="mb-2 flex flex-wrap items-center gap-2 text-[12.5px]">
              <Mono className="min-w-0 truncate">{diff.data.path}</Mono>
              <span className="tnum"><span className="text-ok">+{diff.data.additions}</span> <span className="text-danger">−{diff.data.deletions}</span></span>
              {!diff.data.tracked && <Tag tone="ok">new — git has never seen it</Tag>}
            </div>
            {diff.data.patch ? <DiffLines body={diff.data.patch} /> : (
              <p className="text-[13px] text-soft">Git reports this file as changed but prints no lines for it — a mode or a rename, or the file is binary.</p>
            )}
            {diff.data.truncated && <p className="mt-1.5 text-[12.5px] text-warn">This is the first 200 kB of the file’s diff; the rest was not sent.</p>}
          </>
        )}
      </div>
    </div>
  );
}

function ChangeRow({ change: c, root, active, inCommit, canWrite, arming, busy, onPick, onToggle, onOpen, onDiscard }: {
  change: GitChange; root: string; active: boolean; inCommit: boolean; canWrite: boolean; arming: boolean; busy: boolean;
  onPick: () => void; onToggle: () => void; onOpen: () => void; onDiscard: () => void;
}) {
  const mark = GIT_TONE[c.status];
  const folder = dirName(c.path);
  return (
    <div className={cn('group flex items-center gap-2 px-3 py-1.5 text-[12.5px]', active ? 'bg-brand/8' : 'hover:bg-surface-2/60')}>
      <input type="checkbox" checked={inCommit} onChange={onToggle} disabled={!canWrite || busy}
        aria-label={`Commit ${c.path}`} className="size-3.5 shrink-0 accent-[var(--os-brand)]" />
      <button type="button" onClick={onPick} onDoubleClick={onOpen} className="flex min-w-0 flex-1 items-baseline gap-1.5 text-left">
        <span className={cn('shrink-0 font-mono', mark.cls)} title={mark.word}>{c.status}</span>
        <span className="truncate text-ink">{baseName(c.path)}</span>
        <span className="min-w-0 truncate font-mono text-[11px] text-dim">{folder === '/' ? '' : folder}</span>
      </button>
      {canWrite && (
        <Button size="icon-xs" variant="ghost" disabled={busy} onClick={onDiscard}
          aria-label={`Take ${c.path} back to the last commit`}
          title={arming ? 'Click again: this throws the change away' : 'Take it back to the last commit'}
          className={cn('shrink-0 opacity-100 sm:opacity-0 sm:group-hover:opacity-100 sm:focus-visible:opacity-100', arming && 'text-danger opacity-100!')}>
          <Undo2 className="size-3" />
        </Button>
      )}
      {active && <span className="sr-only">{root}</span>}
    </div>
  );
}
