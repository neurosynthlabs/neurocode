import { useMemo, useState } from 'react';
import { ChevronRight } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Mono, Tag } from '@/components/os';
import { splitPatch, type PatchFile } from '@/lib/live/runtime';
import { cn } from '@/lib/utils';

/* A patch, read as a person reads one: the files it touches, what happened to each, and the lines
   themselves — coloured, folded away until asked for. The same component draws a run's whole diff
   (the screen where a signature is given) and one file's changes in the Workbench, because they are
   the same thing at two sizes and a reviewer should not have to learn it twice. */

const CHANGE_TONE = { A: 'ok', M: 'warn', D: 'danger', R: 'info' } as const;
const CHANGE_WORD = { A: 'added', M: 'changed', D: 'deleted', R: 'renamed' } as const;
/** Lines of one file put in the DOM at once. A generated file can be tens of thousands; the rest is
    still in the patch, and the row says how many are not drawn rather than quietly stopping. */
const MAX_LINES = 800;
/** Files whose lines are open when the diff first appears. Beyond this, a diff opens as a list. */
const OPEN_AT_FIRST = 3;

const tone = (line: string) =>
  line.startsWith('+') ? 'text-ok' : line.startsWith('-') ? 'text-danger'
    : line.startsWith('@@') ? 'text-brand' : line.startsWith('\\') ? 'text-dim' : '';

/** One file's hunks as git printed them, each line coloured by what it does. */
export function DiffLines({ body, className }: { body: string; className?: string }) {
  const lines = useMemo(() => body.split('\n'), [body]);
  const shown = lines.length > MAX_LINES ? lines.slice(0, MAX_LINES) : lines;
  return (
    <>
      <pre className={cn('ascii overflow-x-auto rounded-lg bg-base p-2.5 text-[12px] ring-1 ring-line/60 ring-inset', className)}>
        {shown.map((line, i) => (
          <div key={i} className={tone(line)}>{line || ' '}</div>
        ))}
      </pre>
      {shown.length < lines.length && (
        <p className="mt-1 text-[12px] text-dim">
          {(lines.length - shown.length).toLocaleString()} more lines of this file are in the patch but not drawn here.
        </p>
      )}
    </>
  );
}

/** One file in the patch: its row, and its lines once the row is opened. */
function FileRow({ file, open, onToggle }: { file: PatchFile; open: boolean; onToggle: () => void }) {
  return (
    <div className="px-5 py-2.5">
      <button type="button" onClick={onToggle} aria-expanded={open}
        className="flex w-full items-center gap-2 text-left">
        <ChevronRight className={cn('size-3.5 shrink-0 text-dim transition-transform', open && 'rotate-90')} />
        <span title={CHANGE_WORD[file.change]}><Tag tone={CHANGE_TONE[file.change]}>{file.change}</Tag></span>
        <Mono className="min-w-0 truncate">{file.path}</Mono>
        {file.from && <span className="shrink-0 text-[11.5px] text-dim">from <Mono>{file.from}</Mono></span>}
        <span className="ml-auto shrink-0 tnum text-[12px]">
          {file.binary ? <span className="text-dim">binary</span>
            : <><span className="text-ok">+{file.additions}</span> <span className="text-danger">−{file.deletions}</span></>}
        </span>
      </button>
      {open && (
        <div className="mt-1.5">
          {file.binary ? <p className="text-[12.5px] text-dim">Git records that this file differs and prints no lines for it.</p>
            : file.body ? <DiffLines body={file.body} />
              : <p className="text-[12.5px] text-dim">Only the file itself moved: the patch carries no changed lines for it.</p>}
        </div>
      )}
    </div>
  );
}

/**
 * A whole patch. `truncated` is the server's own flag: it says the patch was cut at its ceiling, so
 * what is drawn is the beginning of it and not all of it.
 */
export function PatchFiles({ patch, truncated = false, ceiling }: { patch: string; truncated?: boolean; ceiling?: string }) {
  const files = useMemo(() => splitPatch(patch), [patch]);
  const [open, setOpen] = useState<readonly string[] | null>(null);
  const shown = open ?? files.slice(0, OPEN_AT_FIRST).map((f) => f.path);
  const toggle = (path: string) =>
    setOpen(shown.includes(path) ? shown.filter((p) => p !== path) : [...shown, path]);
  const additions = files.reduce((n, f) => n + f.additions, 0);
  const deletions = files.reduce((n, f) => n + f.deletions, 0);

  if (files.length === 0) {
    return <p className="px-5 py-3 text-[13px] text-soft">The patch holds no files.</p>;
  }
  return (
    <div className="divide-y divide-line/60">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-5 py-2.5 text-[12.5px] text-dim">
        <span>{files.length === 1 ? '1 file' : `${files.length} files`}</span>
        <span className="tnum"><span className="text-ok">+{additions.toLocaleString()}</span> <span className="text-danger">−{deletions.toLocaleString()}</span></span>
        <div className="ml-auto flex items-center gap-1.5">
          <Button size="xs" variant="ghost" onClick={() => setOpen(files.map((f) => f.path))}>Open all</Button>
          <Button size="xs" variant="ghost" onClick={() => setOpen([])}>Close all</Button>
        </div>
      </div>
      {files.map((f) => (
        <FileRow key={`${f.change}:${f.path}`} file={f} open={shown.includes(f.path)} onToggle={() => toggle(f.path)} />
      ))}
      {truncated && (
        <p className="px-5 py-2.5 text-[12.5px] text-warn">
          This is the first {ceiling ?? '200 kB'} of the patch — the rest was not sent, so files after the last one here are
          not shown at all. Read the whole diff with git in the worktree.
        </p>
      )}
    </div>
  );
}
