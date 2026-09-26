import { useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode, type Ref } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  AlertTriangle, ArrowDown, ArrowUp, BookOpen, ChevronsUpDown, Code2, Eraser, FileWarning, Loader2, MessageCircleQuestion, Play,
  PlayCircle, Plus, RotateCcw, Save, Square, Trash2, Type, Wrench,
} from 'lucide-react';
import { toast } from 'sonner';
import { minimalSetup } from 'codemirror';
import { closeBrackets, closeBracketsKeymap } from '@codemirror/autocomplete';
import { indentWithTab } from '@codemirror/commands';
import { LanguageDescription, bracketMatching, indentOnInput, indentUnit, syntaxHighlighting, type LanguageSupport } from '@codemirror/language';
import { languages } from '@codemirror/language-data';
import { Compartment, EditorState, Prec, type Extension } from '@codemirror/state';
import { EditorView, keymap, placeholder } from '@codemirror/view';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuGroup, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { Dot, Empty, More } from '@/components/os';
import { ApiError, api } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { baseName } from '@/lib/live/machine';
import {
  KERNEL_CLOSE, newCell, notebooks, type CellType, type KernelChoice, type KernelDoc, type KernelEvent, type KernelOptions, type KernelRun,
  type NbCell, type NbDocument, type NbOutput, type NotebookFile, type RunStatus,
} from '@/lib/live/notebooks';
import { cn } from '@/lib/utils';
import { colours, mix } from '@/components/workbench/syntax';

/* A Jupyter notebook opened in the Workbench: its cells edited in place (CodeMirror, in the notebook's
   language), markdown rendered as text and never as HTML, outputs drawn as Jupyter draws them — HTML only in a
   sandboxed frame that runs no script and fetches nothing — and cells run on a real kernel on this machine,
   their output streaming in over the kernel's socket. The file is saved as nbformat 4 over the version this
   page opened, as the editor saves any file. Nothing here is invented: no kernel, no run button that pretends. */

/** What the Workbench asks of an open notebook outside React's data flow. */
export interface NotebookHandle {
  /** Save to the file; false when it was not saved (the reason is on screen). */
  save: () => Promise<boolean>;
  /** The tab is closing: shut its kernel down. */
  close: () => Promise<void>;
}

export interface NotebookViewProps {
  path: string;
  projectId?: string | null;
  dark: boolean;
  /** A notebook in a reference: cells may run, but nothing is edited or saved. */
  readOnly?: boolean;
  onDirty: (path: string, dirty: boolean) => void;
  ref?: Ref<NotebookHandle>;
}

type LiveOutput = NbOutput & { displayId?: string | null };
type LiveCell = Omit<NbCell, 'outputs'> & { outputs?: LiveOutput[] };

const reason = (e: unknown) => (e instanceof ApiError ? e.message : 'The local API did not answer. Is it still running?');
const FINAL: ReadonlySet<RunStatus> = new Set(['ok', 'error', 'aborted']);
const RETRY_MS = [1000, 2000, 4000, 8000];
/** The server's own ceiling on a session question (services/chat.py MAX_QUESTION). */
const MAX_QUESTION = 4000;
const IMAGE_MIMES = ['image/png', 'image/jpeg', 'image/gif', 'image/webp'];
// Terminal escape sequences are exactly what these match: the ESC and BEL characters are the point.
// oxlint-disable-next-line no-control-regex
const ANSI = /\x1b\[([\d;]*)m/g;
// oxlint-disable-next-line no-control-regex
const OTHER_ESCAPES = /\x1b\[[\d;?]*[A-Za-ln-z]|\x1b\][^\x07]*\x07/g;

function isLive(k: KernelDoc | null): k is KernelDoc {
  return !!k && k.status !== 'dead' && k.status !== 'closed';
}

/* ── ANSI colour, as tracebacks carry it ─────────────────────────── */

const ANSI_TONES: Record<number, string> = {
  30: 'text-dim', 31: 'text-danger', 32: 'text-ok', 33: 'text-warn', 34: 'text-info', 35: 'text-violet', 36: 'text-info', 37: 'text-ink',
  90: 'text-dim', 91: 'text-danger', 92: 'text-ok', 93: 'text-warn', 94: 'text-info', 95: 'text-violet', 96: 'text-info', 97: 'text-ink',
};

/** Text with ANSI colour codes as coloured spans; every other escape sequence is taken out. */
function Ansi({ text }: { text: string }) {
  const clean = text.replace(OTHER_ESCAPES, '');
  const parts: ReactNode[] = [];
  let tone = '';
  let bold = false;
  let last = 0;
  let key = 0;
  for (const m of clean.matchAll(ANSI)) {
    if (m.index > last) parts.push(<span key={key++} className={cn(tone, bold && 'font-semibold')}>{clean.slice(last, m.index)}</span>);
    const codes = (m[1] || '0').split(';').map(Number);
    for (let i = 0; i < codes.length; i++) {
      const c = codes[i];
      if (c === 0) { tone = ''; bold = false; }
      else if (c === 1) bold = true;
      else if (c === 22) bold = false;
      else if (c === 39) tone = '';
      else if (c === 38 || c === 48) i += codes[i + 1] === 5 ? 2 : 4;
      else if (ANSI_TONES[c]) tone = ANSI_TONES[c];
    }
    last = m.index + m[0].length;
  }
  if (last < clean.length) parts.push(<span key={key++} className={cn(tone, bold && 'font-semibold')}>{clean.slice(last)}</span>);
  return <>{parts}</>;
}

const stripAnsi = (text: string) => text.replace(ANSI, '').replace(OTHER_ESCAPES, '');

/* ── Markdown, rendered as React elements: raw HTML is shown as the text it is ── */

const SAFE_LINK = /^(https?:|mailto:)/i;

function inline(text: string, key: string, attachments?: NbCell['attachments']): ReactNode[] {
  const out: ReactNode[] = [];
  // Code first, then images, links, bold, italic and inline math; whatever is left is plain text.
  const pattern = /(`+)([\s\S]*?)\1|!\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)|\[([^\]]+)\]\(([^)\s]+)(?:\s+"[^"]*")?\)|\*\*([^*]+)\*\*|__([^_]+)__|\*([^*\s][^*]*)\*|(?<![\w])_([^_\s][^_]*)_(?![\w])|\$([^$\n]+)\$/g;
  let last = 0;
  let i = 0;
  for (const m of text.matchAll(pattern)) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const k = `${key}-${i++}`;
    if (m[1]) out.push(<code key={k} className="rounded bg-surface-2 px-1 py-px font-mono text-[0.92em] text-ink">{m[2]}</code>);
    else if (m[4] !== undefined) {
      const src = m[4];
      const attached = src.startsWith('attachment:') ? attachments?.[src.slice(11)] : undefined;
      const mime = attached ? IMAGE_MIMES.find((x) => attached[x]) : undefined;
      if (attached && mime) out.push(<img key={k} alt={m[3]} src={`data:${mime};base64,${attached[mime]}`} className="my-1 inline-block max-w-full" />);
      else if (/^data:image\/(png|jpeg|gif|webp);base64,/.test(src)) out.push(<img key={k} alt={m[3]} src={src} className="my-1 inline-block max-w-full" />);
      // A picture on the internet is linked, not fetched: opening a notebook sends nothing anywhere.
      else if (SAFE_LINK.test(src)) out.push(<a key={k} href={src} target="_blank" rel="noreferrer noopener" className="text-info underline">{m[3] || src} ↗</a>);
      else out.push(<span key={k} className="text-dim">[{m[3] || 'image'}]</span>);
    } else if (m[6] !== undefined) {
      out.push(SAFE_LINK.test(m[6])
        ? <a key={k} href={m[6]} target="_blank" rel="noreferrer noopener" className="text-info underline">{inline(m[5], k, attachments)}</a>
        : <span key={k}>{inline(m[5], k, attachments)}</span>);
    } else if (m[7] !== undefined || m[8] !== undefined) out.push(<strong key={k} className="font-semibold text-ink">{inline(m[7] ?? m[8], k, attachments)}</strong>);
    else if (m[9] !== undefined || m[10] !== undefined) out.push(<em key={k}>{inline(m[9] ?? m[10], k, attachments)}</em>);
    else if (m[11] !== undefined) out.push(<span key={k} className="font-mono text-[0.92em] text-violet">{m[11]}</span>);
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

const HEADING = ['text-[1.5em]', 'text-[1.3em]', 'text-[1.15em]', 'text-[1.05em]', 'text-[1em]', 'text-[0.95em]'];

function Markdown({ text, attachments }: { text: string; attachments?: NbCell['attachments'] }) {
  const lines = text.replace(/\r\n/g, '\n').split('\n');
  const blocks: ReactNode[] = [];
  let i = 0;
  let n = 0;
  const isList = (l: string) => /^\s*([-*+]|\d+[.)])\s+/.test(l);
  const isTableRule = (l: string | undefined) => !!l && /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(l);
  const cells = (l: string) => l.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim());
  while (i < lines.length) {
    const line = lines[i];
    const key = `b${n++}`;
    if (!line.trim()) { i++; continue; }
    const fence = /^\s*(```|~~~)\s*(\S*)/.exec(line);
    if (fence) {
      const body: string[] = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith(fence[1])) body.push(lines[i++]);
      i++;
      blocks.push(<pre key={key} className="my-2 overflow-x-auto rounded-lg bg-surface-2/70 px-3 py-2 font-mono text-[12.5px] leading-relaxed text-ink-2">{body.join('\n')}</pre>);
      continue;
    }
    if (line.trim() === '$$') {
      const body: string[] = [];
      i++;
      while (i < lines.length && lines[i].trim() !== '$$') body.push(lines[i++]);
      i++;
      blocks.push(<pre key={key} className="my-2 overflow-x-auto text-center font-mono text-[12.5px] text-violet">{body.join('\n')}</pre>);
      continue;
    }
    const heading = /^(#{1,6})\s+(.*?)\s*#*\s*$/.exec(line);
    if (heading) {
      const level = heading[1].length;
      blocks.push(<p key={key} role="heading" aria-level={level} className={cn('mt-3 mb-1.5 font-semibold text-ink first:mt-0', HEADING[level - 1])}>{inline(heading[2], key, attachments)}</p>);
      i++;
      continue;
    }
    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) { blocks.push(<hr key={key} className="my-3 border-line" />); i++; continue; }
    if (/^\s*>/.test(line)) {
      const body: string[] = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) body.push(lines[i++].replace(/^\s*>\s?/, ''));
      blocks.push(<blockquote key={key} className="my-2 border-l-2 border-line-strong pl-3 text-soft"><Markdown text={body.join('\n')} attachments={attachments} /></blockquote>);
      continue;
    }
    if (line.includes('|') && isTableRule(lines[i + 1])) {
      const head = cells(line);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && lines[i].includes('|') && lines[i].trim()) rows.push(cells(lines[i++]));
      blocks.push(
        <div key={key} className="my-2 overflow-x-auto">
          <table className="border-collapse text-[0.95em]">
            <thead><tr>{head.map((h, j) => <th key={j} className="border-b border-line px-2.5 py-1 text-left font-semibold text-ink">{inline(h, `${key}h${j}`, attachments)}</th>)}</tr></thead>
            <tbody>{rows.map((r, ri) => <tr key={ri}>{r.map((c, j) => <td key={j} className="border-b border-line/50 px-2.5 py-1">{inline(c, `${key}r${ri}c${j}`, attachments)}</td>)}</tr>)}</tbody>
          </table>
        </div>,
      );
      continue;
    }
    if (isList(line)) {
      const ordered = /^\s*\d+[.)]/.test(line);
      const items: string[] = [];
      while (i < lines.length && (isList(lines[i]) || (lines[i].trim() && /^\s{2,}/.test(lines[i]) && items.length))) {
        if (isList(lines[i])) items.push(lines[i].replace(/^\s*([-*+]|\d+[.)])\s+/, ''));
        else items[items.length - 1] += ` ${lines[i].trim()}`;
        i++;
      }
      const List = ordered ? 'ol' : 'ul';
      blocks.push(
        <List key={key} className={cn('my-1.5 space-y-0.5 pl-5', ordered ? 'list-decimal' : 'list-disc')}>
          {items.map((item, j) => {
            const task = /^\[([ xX])\]\s+/.exec(item);
            return <li key={j}>{task ? <><input type="checkbox" checked={task[1] !== ' '} readOnly disabled className="mr-1.5 align-middle" />{inline(item.slice(task[0].length), `${key}i${j}`, attachments)}</> : inline(item, `${key}i${j}`, attachments)}</li>;
          })}
        </List>,
      );
      continue;
    }
    const body: string[] = [];
    while (i < lines.length && lines[i].trim() && !/^(#{1,6}\s|\s*(```|~~~)|\s*>)/.test(lines[i]) && !(body.length && isList(lines[i]))) body.push(lines[i++]);
    // Line breaks inside a paragraph are kept, as Jupyter's renderer keeps them.
    const parts: ReactNode[] = inline(body.join('\n'), key, attachments).flatMap((part, j): ReactNode[] =>
      typeof part === 'string' ? part.split('\n').flatMap((s, k): ReactNode[] => (k ? [<br key={`${key}br${j}-${k}`} />, s] : [s])) : [part]);
    blocks.push(<p key={key} className="my-1.5 leading-relaxed">{parts}</p>);
  }
  return <>{blocks}</>;
}

/* ── Outputs ─────────────────────────────────────────────────────── */

/** HTML a cell produced (a dataframe, an SVG), in a frame that runs no script and loads nothing from anywhere. */
function SafeHtml({ html }: { html: string }) {
  const frame = useRef<HTMLIFrameElement>(null);
  const [height, setHeight] = useState(60);
  const doc = useMemo(() => {
    const css = getComputedStyle(document.documentElement);
    const v = (name: string, fallback: string) => css.getPropertyValue(name).trim() || fallback;
    const style = `html,body{margin:0;padding:0;background:transparent;color:${v('--os-ink-2', '#333')};font:12.5px/1.5 ui-sans-serif,system-ui,sans-serif}`
      + `table{border-collapse:collapse;font-variant-numeric:tabular-nums}th,td{padding:4px 10px;border-bottom:1px solid ${v('--os-line', '#ddd')};text-align:right;white-space:nowrap}`
      + `th{font-weight:600;color:${v('--os-ink', '#111')}}tbody tr:nth-child(odd){background:${v('--os-surface-2', '#f5f5f5')}}`
      + `a{color:${v('--os-info', '#06c')}}svg{max-width:100%;height:auto}pre{font:12px/1.5 ui-monospace,monospace}`;
    // default-src 'none': no script, no fetch, no remote font or image; pictures inside the output still show.
    // Scripts are also taken out, so the browser does not report each one it refused to run.
    const body = html.replace(/<script\b[\s\S]*?<\/script\s*>/gi, '');
    return `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'; font-src data:"><style>${style}</style></head><body>${body}</body></html>`;
  }, [html]);
  const measure = () => {
    const body = frame.current?.contentDocument?.documentElement;
    if (body) setHeight(Math.min(Math.max(body.scrollHeight + 4, 24), 640));
  };
  return (
    <iframe ref={frame} title="Cell output" srcDoc={doc} sandbox="allow-same-origin" onLoad={measure} style={{ height }}
      className="block w-full border-0 bg-transparent" />
  );
}

const PREFERRED = ['application/vnd.jupyter.widget-view+json', 'image/png', 'image/jpeg', 'image/gif', 'image/webp', 'image/svg+xml', 'text/html',
  'text/markdown', 'application/json', 'text/latex', 'text/plain'];

function RichOutput({ data, metadata }: { data: Record<string, unknown>; metadata: Record<string, unknown> }) {
  const [asText, setAsText] = useState(false);
  const mime = PREFERRED.find((m) => m in data) ?? Object.keys(data)[0];
  const plain = typeof data['text/plain'] === 'string' ? (data['text/plain'] as string) : null;
  if (!mime) return null;
  const value = data[mime];
  const toggle = plain !== null && (mime === 'text/html' || mime === 'image/svg+xml') ? (
    <button type="button" onClick={() => setAsText((x) => !x)} className="mt-1 text-[11.5px] text-dim hover:text-ink">
      {asText ? 'Show as rendered' : 'Show as text'}
    </button>
  ) : null;
  if (asText && plain !== null) return <><pre className="whitespace-pre-wrap font-mono text-[12.5px] text-ink-2"><Ansi text={plain} /></pre>{toggle}</>;
  if (mime === 'application/vnd.jupyter.widget-view+json') {
    return (
      <div>
        <p className="text-[12px] text-dim">An interactive widget. It runs only in Jupyter's own front end.</p>
        {plain !== null && <pre className="mt-1 whitespace-pre-wrap font-mono text-[12.5px] text-ink-2">{plain}</pre>}
      </div>
    );
  }
  if (mime.startsWith('image/') && mime !== 'image/svg+xml' && typeof value === 'string') {
    const meta = (metadata[mime] ?? {}) as { width?: number; height?: number };
    return <img alt={plain ?? 'Cell output'} src={`data:${mime};base64,${value}`} width={meta.width} height={meta.height} className="max-w-full rounded-sm bg-white/0" />;
  }
  if ((mime === 'text/html' || mime === 'image/svg+xml') && typeof value === 'string') return <>{<SafeHtml html={value} />}{toggle}</>;
  if (mime === 'text/markdown' && typeof value === 'string') return <div className="text-[13px] text-ink-2"><Markdown text={value} /></div>;
  if (mime === 'application/json') return <pre className="overflow-x-auto font-mono text-[12.5px] text-ink-2">{JSON.stringify(value, null, 2)}</pre>;
  return <pre className="whitespace-pre-wrap break-words font-mono text-[12.5px] text-ink-2"><Ansi text={typeof value === 'string' ? value : JSON.stringify(value)} /></pre>;
}

function Outputs({ outputs, onFix }: { outputs: LiveOutput[]; onFix?: (error: Extract<NbOutput, { output_type: 'error' }>) => void }) {
  const [open, setOpen] = useState(false);
  const long = outputs.reduce((n, o) => n + (o.output_type === 'stream' ? o.text.split('\n').length : o.output_type === 'error' ? o.traceback.length * 3 : 10), 0) > 40;
  if (!outputs.length) return null;
  return (
    <div className="relative">
      <div className={cn('space-y-1.5 overflow-x-auto px-3 py-2', long && !open && 'max-h-[420px] overflow-y-auto')}>
        {outputs.map((o, i) => {
          if (o.output_type === 'stream') {
            return <pre key={i} className={cn('whitespace-pre-wrap break-words font-mono text-[12.5px] leading-relaxed', o.name === 'stderr' ? 'rounded-md bg-danger/6 px-2 py-1 text-ink-2' : 'text-ink-2')}><Ansi text={o.text} /></pre>;
          }
          if (o.output_type === 'error') {
            return (
              <div key={i} className="rounded-md bg-danger/6 px-2 py-1.5">
                <pre className="whitespace-pre-wrap break-words font-mono text-[12.5px] leading-relaxed text-ink-2">
                  {o.traceback.length ? <Ansi text={o.traceback.join('\n')} /> : <span className="text-danger">{o.ename}: {o.evalue}</span>}
                </pre>
                {onFix && (
                  <Button size="xs" variant="outline" className="mt-1.5" onClick={() => onFix(o)}><Wrench className="size-3" />Fix this error</Button>
                )}
              </div>
            );
          }
          return <div key={i} className="min-w-0"><RichOutput data={o.data} metadata={o.metadata} /></div>;
        })}
      </div>
      {long && (
        <button type="button" onClick={() => setOpen((x) => !x)} className="flex w-full items-center justify-center gap-1 border-t border-line/50 py-1 text-[11.5px] text-dim hover:text-ink">
          <ChevronsUpDown className="size-3" />{open ? 'Scroll the output' : 'Show all output'}
        </button>
      )}
    </div>
  );
}

/* ── A cell's editor ─────────────────────────────────────────────── */

function cellTheme(dark: boolean): Extension {
  return EditorView.theme({
    '&': { color: 'var(--os-ink)', backgroundColor: 'transparent', fontSize: '13px' },
    '&.cm-focused': { outline: 'none' },
    '.cm-scroller': { fontFamily: 'var(--font-mono)', lineHeight: '1.6' },
    '.cm-content': { caretColor: 'var(--os-brand)', padding: '8px 0' },
    '.cm-line': { padding: '0 12px' },
    '.cm-cursor, .cm-dropCursor': { borderLeftColor: 'var(--os-brand)', borderLeftWidth: '2px' },
    '&.cm-focused > .cm-scroller > .cm-selectionLayer .cm-selectionBackground, .cm-selectionBackground, .cm-content ::selection':
      { backgroundColor: mix('--os-brand', 24) },
    '.cm-matchingBracket, &.cm-focused .cm-matchingBracket': { backgroundColor: mix('--os-brand', 18) },
    '.cm-placeholder': { color: 'var(--os-dim)' },
    '.cm-tooltip': { backgroundColor: 'var(--os-surface)', color: 'var(--os-ink-2)', border: '1px solid var(--os-line)', borderRadius: '8px' },
  }, { dark });
}


type RunMode = 'next' | 'stay' | 'insert';

function CellEditor({ initial, language, dark, readOnly, placeholderText, focus, onChange, onRun, onLeave, onFocus }: {
  initial: string;
  language: LanguageSupport | null;
  dark: boolean;
  readOnly: boolean;
  placeholderText: string;
  focus: number;
  onChange: (text: string) => void;
  onRun: (mode: RunMode) => void;
  onLeave: () => void;
  onFocus: () => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  const slots = useRef({ language: new Compartment(), theme: new Compartment(), readOnly: new Compartment() });
  const calls = useRef({ onChange, onRun, onLeave, onFocus });
  useEffect(() => { calls.current = { onChange, onRun, onLeave, onFocus }; });

  useEffect(() => {
    if (!host.current) return;
    const run = (mode: RunMode) => () => { calls.current.onRun(mode); return true; };
    const made = new EditorView({
      parent: host.current,
      state: EditorState.create({
        doc: initial,
        extensions: [
          minimalSetup,
          Prec.highest(keymap.of([
            { key: 'Shift-Enter', run: run('next') },
            { key: 'Mod-Enter', run: run('stay') },
            { key: 'Alt-Enter', run: run('insert') },
            { key: 'Escape', run: () => { calls.current.onLeave(); return true; } },
          ])),
          keymap.of([indentWithTab, ...closeBracketsKeymap]),
          bracketMatching(),
          closeBrackets(),
          indentOnInput(),
          indentUnit.of('    '),
          syntaxHighlighting(colours),
          placeholder(placeholderText),
          slots.current.language.of(language ?? []),
          slots.current.theme.of(cellTheme(dark)),
          slots.current.readOnly.of(readOnly ? EditorState.readOnly.of(true) : []),
          EditorView.updateListener.of((u) => {
            if (u.docChanged) calls.current.onChange(u.state.doc.toString());
            if (u.focusChanged && u.view.hasFocus) calls.current.onFocus();
          }),
        ],
      }),
    });
    view.current = made;
    return () => { made.destroy(); view.current = null; };
    // The text is the editor's own from here on; a new document is a new cell (the key changes).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => { view.current?.dispatch({ effects: slots.current.language.reconfigure(language ?? []) }); }, [language]);
  useEffect(() => { view.current?.dispatch({ effects: slots.current.theme.reconfigure(cellTheme(dark)) }); }, [dark]);
  useEffect(() => {
    view.current?.dispatch({ effects: slots.current.readOnly.reconfigure(readOnly ? EditorState.readOnly.of(true) : []) });
  }, [readOnly]);
  useEffect(() => { if (focus) view.current?.focus(); }, [focus]);

  return <div ref={host} className="min-w-0" />;
}

/* ── The notebook ────────────────────────────────────────────────── */

const toLive = (cells: NbCell[]): LiveCell[] => cells.map((c) => ({ ...c }));
const when = (at: string | null | undefined) => (at ? Date.parse(at) : 0);

export default function NotebookView({ path, projectId, dark, readOnly = false, onDirty, ref }: NotebookViewProps) {
  const navigate = useNavigate();
  const { can } = useAuth();
  const [file, setFile] = useState<NotebookFile | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loadNonce, setLoadNonce] = useState(0);
  const [cells, setCells] = useState<LiveCell[]>([]);
  const [sha1, setSha1] = useState('');
  const [dirty, setDirty] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [editing, setEditing] = useState<Set<string>>(() => new Set());
  const [focusAt, setFocusAt] = useState<{ id: string; nonce: number } | null>(null);
  const [runState, setRunState] = useState<Record<string, RunStatus>>({});
  const [kernel, setKernel] = useState<KernelDoc | null>(null);
  const [options, setOptions] = useState<KernelOptions | null>(null);
  const [optionsError, setOptionsError] = useState<string | null>(null);
  const [kernelError, setKernelError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [linkLost, setLinkLost] = useState(false);
  const [saving, setSaving] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [confirmRestart, setConfirmRestart] = useState<null | 'restart' | 'run-all'>(null);
  const [language, setLanguage] = useState<LanguageSupport | null>(null);
  const [markdownLanguage, setMarkdownLanguage] = useState<LanguageSupport | null>(null);

  const cellsRef = useRef(cells);
  const fileRef = useRef(file);
  const kernelRef = useRef(kernel);
  useEffect(() => { cellsRef.current = cells; fileRef.current = file; kernelRef.current = kernel; });
  /** Which cell each execution belongs to, for the executions this page started or picked up. */
  const requestCell = useRef(new Map<string, string>());
  /** Resolved when the kernel's socket has said hello, so a cell is sent only once its output can be heard. */
  const ready = useRef<{ id: string; promise: Promise<void>; resolve: () => void } | null>(null);

  useEffect(() => { onDirty(path, dirty); }, [path, dirty, onDirty]);

  // ── the file ──────────────────────────────────────────────────
  useEffect(() => {
    let current = true;
    notebooks.open(path).then(
      (opened) => {
        if (!current) return;
        setFile(opened);
        setCells(toLive(opened.notebook.cells));
        setSha1(opened.sha1);
        setDirty(false);
        setLoadError(null);
        setConflict(false);
        setSelected((was) => was ?? opened.notebook.cells[0]?.id ?? null);
      },
      (e: unknown) => { if (current) setLoadError(reason(e)); },
    );
    return () => { current = false; };
  }, [path, loadNonce]);

  // The notebook's language, highlighted by CodeMirror's own language data, loaded on first use.
  const languageName = file?.language ?? null;
  useEffect(() => {
    if (!languageName) return;
    let current = true;
    const found = LanguageDescription.matchLanguageName(languages, languageName, true);
    found?.load().then((support) => { if (current) setLanguage(support); }, () => undefined);
    LanguageDescription.matchLanguageName(languages, 'markdown', true)?.load().then((support) => { if (current) setMarkdownLanguage(support); }, () => undefined);
    return () => { current = false; };
  }, [languageName]);

  // A kernel this person already runs for this notebook (the page was reloaded, or the Workbench left and
  // came back) is picked up; otherwise what would run it is read, so the toolbar says so before anything starts.
  const realPath = file?.path ?? null;
  useEffect(() => {
    if (!realPath) return;
    let current = true;
    notebooks.kernels().then(
      (mine) => {
        if (!current) return;
        const found = mine.find((k) => k.path === realPath && isLive(k));
        if (found) setKernel(found);
      },
      () => undefined,
    );
    notebooks.kernelOptions(realPath).then(
      (found) => { if (current) { setOptions(found); setOptionsError(null); } },
      (e: unknown) => { if (current) setOptionsError(reason(e)); },
    );
    return () => { current = false; };
  }, [realPath]);

  // ── outputs arriving ──────────────────────────────────────────
  const editCell = useCallback((id: string, change: (c: LiveCell) => LiveCell) => {
    setCells((was) => was.map((c) => (c.id === id ? change(c) : c)));
  }, []);

  /** Take in what the kernel remembers: every execution this page started, and newer runs of its cells. */
  const absorb = useCallback((runs: KernelRun[]) => {
    const opened = when(fileRef.current?.modified);
    const latest = new Map<string, KernelRun>();
    for (const run of runs) {
      const known = requestCell.current.has(run.requestId);
      const newer = !FINAL.has(run.status) || when(run.endedAt) > opened;
      if (!known && !newer) continue;
      const was = latest.get(run.cellId);
      if (!was || when(run.startedAt) >= when(was.startedAt)) latest.set(run.cellId, run);
    }
    if (!latest.size) return;
    let changed = false;
    setCells((was) => was.map((c) => {
      const run = latest.get(c.id);
      if (!run || c.cell_type !== 'code') return c;
      requestCell.current.set(run.requestId, c.id);
      changed = true;
      return { ...c, outputs: run.outputs, execution_count: run.executionCount ?? c.execution_count ?? null };
    }));
    setRunState((was) => {
      const next = { ...was };
      for (const [cellId, run] of latest) next[cellId] = run.status;
      return next;
    });
    if (changed) setDirty(true);
  }, []);

  const onEvent = useCallback((e: KernelEvent, kernelId: string) => {
    if (e.type === 'hello') {
      setKernel(e.kernel);
      absorb(e.runs);
      if (ready.current?.id === kernelId) ready.current.resolve();
      return;
    }
    if (e.type === 'state') { setKernel(e.kernel); return; }
    if (e.type === 'closed') { setKernel((k) => (k && k.id === kernelId ? { ...k, status: 'closed' } : k)); return; }
    if (e.type === 'resync') {
      notebooks.kernel(kernelId).then((k) => { setKernel(k); absorb(k.runs); }, () => undefined);
      return;
    }
    if (e.type === 'update') {
      setCells((was) => was.map((c) => (c.outputs?.some((o) => o.displayId === e.displayId)
        ? { ...c, outputs: c.outputs.map((o) => (o.displayId === e.displayId && o.output_type !== 'stream' && o.output_type !== 'error' && e.output.output_type !== 'stream' && e.output.output_type !== 'error'
          ? { ...o, data: e.output.data, metadata: e.output.metadata } : o)) }
        : c)));
      return;
    }
    // Every run event names its cell, so output that arrives before the run's own HTTP answer is not lost.
    const cellId = requestCell.current.get(e.requestId) ?? (cellsRef.current.some((c) => c.id === e.cellId) ? e.cellId : null);
    if (!cellId) return;
    requestCell.current.set(e.requestId, cellId);
    if (e.type === 'queued') setRunState((s) => ({ ...s, [cellId]: 'queued' }));
    else if (e.type === 'started') {
      setRunState((s) => ({ ...s, [cellId]: 'running' }));
    } else if (e.type === 'stream') {
      editCell(cellId, (c) => {
        const outputs = [...(c.outputs ?? [])];
        const last = outputs[outputs.length - 1];
        if (last && last.output_type === 'stream' && last.name === e.name) outputs[outputs.length - 1] = { ...last, text: last.text + e.text };
        else outputs.push({ output_type: 'stream', name: e.name, text: e.text });
        return { ...c, outputs };
      });
      setDirty(true);
    } else if (e.type === 'output') {
      editCell(cellId, (c) => ({ ...c, outputs: [...(c.outputs ?? []), { ...e.output, displayId: e.displayId }] }));
      setDirty(true);
    } else if (e.type === 'clear') {
      editCell(cellId, (c) => ({ ...c, outputs: [] }));
    } else if (e.type === 'done') {
      setRunState((s) => ({ ...s, [cellId]: e.status }));
      editCell(cellId, (c) => ({ ...c, execution_count: e.executionCount ?? c.execution_count ?? null }));
      setDirty(true);
    }
  }, [absorb, editCell]);

  // ── the kernel's socket ───────────────────────────────────────
  const kernelId = isLive(kernel) ? kernel.id : null;
  useEffect(() => {
    if (!kernelId) return;
    let ws: WebSocket | null = null;
    let timer: number | undefined;
    let attempt = 0;
    let over = false;
    const connect = () => {
      const socket = new WebSocket(notebooks.socket(kernelId));
      ws = socket;
      socket.onopen = () => { attempt = 0; setLinkLost(false); };
      socket.onmessage = (event: MessageEvent<string>) => {
        let e: KernelEvent;
        try { e = JSON.parse(event.data) as KernelEvent; } catch { return; }
        onEvent(e, kernelId);
      };
      socket.onclose = (event) => {
        if (over || ws !== socket) return;
        if (event.code in KERNEL_CLOSE || event.code === 1000) {
          if (event.code !== 1000) setKernelError(KERNEL_CLOSE[event.code]);
          setKernel((k) => (k && k.id === kernelId ? { ...k, status: 'closed' } : k));
          return;
        }
        setLinkLost(true);
        timer = window.setTimeout(connect, RETRY_MS[Math.min(attempt++, RETRY_MS.length - 1)]);
      };
    };
    connect();
    return () => { over = true; window.clearTimeout(timer); ws?.close(); };
  }, [kernelId, onEvent]);

  // ── the kernel's life ─────────────────────────────────────────
  const [pick, setPick] = useState<string | null>(null);
  const choice: KernelChoice | null = (pick ? options?.available.find((c) => c.name === pick) : null) ?? options?.choice ?? null;

  const ensureKernel = useCallback(async (): Promise<string | null> => {
    const now = kernelRef.current;
    if (isLive(now) && now.status !== 'starting') return now.id;
    if (!realPath) return null;
    setStarting(true);
    setKernelError(null);
    try {
      const started = await notebooks.start(realPath, pick);
      let resolve = () => {};
      const promise = new Promise<void>((r) => { resolve = r; });
      ready.current = { id: started.id, promise, resolve };
      setKernel(started);
      // The first cell is sent once the socket can hear its output; a socket that cannot open does not hold it up for ever.
      await Promise.race([promise, new Promise((r) => window.setTimeout(r, 8000))]);
      return started.id;
    } catch (e) {
      setKernelError(reason(e));
      return null;
    } finally {
      setStarting(false);
    }
  }, [realPath, pick]);

  const run = useCallback(async (ids: string[]) => {
    const code = ids.map((id) => cellsRef.current.find((c) => c.id === id)).filter((c): c is LiveCell => !!c && c.cell_type === 'code');
    if (!code.length) return;
    const id = await ensureKernel();
    if (!id) return;
    for (const cell of code) {
      editCell(cell.id, (c) => ({ ...c, outputs: [] }));
      setRunState((s) => ({ ...s, [cell.id]: 'queued' }));
      try {
        const queued = await notebooks.execute(id, cell.id, cellsRef.current.find((c) => c.id === cell.id)?.source ?? cell.source);
        requestCell.current.set(queued.requestId, cell.id);
      } catch (e) {
        setRunState((s) => ({ ...s, [cell.id]: 'error' }));
        toast.error('The cell was not run', { description: reason(e) });
        return;
      }
    }
  }, [ensureKernel, editCell]);

  const interrupt = async () => {
    if (!kernelId) return;
    try { await notebooks.interrupt(kernelId); } catch (e) { toast.error('The kernel was not interrupted', { description: reason(e) }); }
  };

  const restart = async (thenRunAll: boolean) => {
    setConfirmRestart(null);
    if (!kernelId) { if (thenRunAll) void run(cellsRef.current.map((c) => c.id)); return; }
    try {
      const k = await notebooks.restart(kernelId);
      setKernel(k);
      setRunState({});
      if (thenRunAll) await run(cellsRef.current.map((c) => c.id));
    } catch (e) {
      toast.error('The kernel did not restart', { description: reason(e) });
    }
  };

  const shutDown = useCallback(async () => {
    const now = kernelRef.current;
    if (!isLive(now)) return;
    try {
      await notebooks.shutDown(now.id);
      setKernel({ ...now, status: 'closed', note: 'Shut down.' });
    } catch (e) {
      toast.error('The kernel was not shut down', { description: reason(e) });
    }
  }, []);

  const switchKernel = async (name: string) => {
    setPick(name);
    if (isLive(kernelRef.current)) await shutDown();
  };

  // ── editing the notebook ──────────────────────────────────────
  const change = useCallback((next: (was: LiveCell[]) => LiveCell[]) => {
    setCells(next);
    setDirty(true);
  }, []);

  const insert = useCallback((at: number, type: CellType) => {
    const cell = newCell(type);
    change((was) => [...was.slice(0, at), cell, ...was.slice(at)]);
    setSelected(cell.id);
    if (type === 'markdown') setEditing((s) => new Set(s).add(cell.id));
    setFocusAt({ id: cell.id, nonce: Date.now() });
  }, [change]);

  const remove = (id: string) => {
    const at = cellsRef.current.findIndex((c) => c.id === id);
    const cell = cellsRef.current[at];
    if (!cell) return;
    change((was) => was.filter((c) => c.id !== id));
    setSelected(cellsRef.current[at + 1]?.id ?? cellsRef.current[at - 1]?.id ?? null);
    toast('Cell deleted', { action: { label: 'Undo', onClick: () => change((was) => [...was.slice(0, at), cell, ...was.slice(at)]) } });
  };

  const move = (id: string, by: -1 | 1) => change((was) => {
    const i = was.findIndex((c) => c.id === id);
    const j = i + by;
    if (i < 0 || j < 0 || j >= was.length) return was;
    const next = [...was];
    [next[i], next[j]] = [next[j], next[i]];
    return next;
  });

  const retype = (id: string, type: CellType) => change((was) => was.map((c) => {
    if (c.id !== id || c.cell_type === type) return c;
    return type === 'code'
      ? { id: c.id, cell_type: 'code', source: c.source, metadata: c.metadata, execution_count: null, outputs: [] }
      : { id: c.id, cell_type: type, source: c.source, metadata: c.metadata };
  }));

  const clearOutputs = () => change((was) => was.map((c) => (c.cell_type === 'code' ? { ...c, outputs: [], execution_count: null } : c)));

  const runCell = useCallback((id: string, mode: RunMode) => {
    const list = cellsRef.current;
    const at = list.findIndex((c) => c.id === id);
    const cell = list[at];
    if (!cell) return;
    if (cell.cell_type !== 'code') setEditing((s) => { const n = new Set(s); n.delete(id); return n; });
    else void run([id]);
    if (mode === 'insert' || (mode === 'next' && at === list.length - 1)) {
      if (!readOnly) insert(at + 1, 'code');
    } else if (mode === 'next') {
      const next = list[at + 1];
      setSelected(next.id);
      setFocusAt({ id: next.id, nonce: Date.now() });
    }
  }, [run, insert, readOnly]);

  // ── saving ────────────────────────────────────────────────────
  const document_ = useCallback((): NbDocument | null => {
    const opened = fileRef.current;
    if (!opened) return null;
    return { ...opened.notebook, cells: cellsRef.current as NbCell[] };
  }, []);

  const save = useCallback(async (expect?: string): Promise<boolean> => {
    const doc = document_();
    if (!doc || !fileRef.current) return false;
    if (readOnly) { toast(`${baseName(path)} is read only`, { description: 'A reference is read, never written.' }); return false; }
    setSaving(true);
    try {
      const saved = await notebooks.save(fileRef.current.path, doc, expect ?? sha1);
      setSha1(saved.sha1);
      setFile((f) => (f ? { ...f, sha1: saved.sha1, size: saved.size, modified: saved.modified, new: false } : f));
      setDirty(false);
      setConflict(false);
      return true;
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 && e.message.includes('changed on disk')) setConflict(true);
      else toast.error(`${baseName(path)} was not saved`, { description: reason(e) });
      return false;
    } finally {
      setSaving(false);
    }
  }, [document_, path, readOnly, sha1]);

  const overwrite = async () => {
    try {
      const now = await notebooks.open(path);
      await save(now.sha1);
    } catch (e) {
      toast.error(`${baseName(path)} was not saved`, { description: reason(e) });
    }
  };

  useImperativeHandle(ref, () => ({ save: () => save(), close: shutDown }), [save, shutDown]);

  // ── asking a session about a cell ─────────────────────────────
  const canAsk = can('sessions:chat');
  const ask = async (cell: LiveCell, error?: Extract<NbOutput, { output_type: 'error' }>) => {
    if (!projectId) return;
    const name = baseName(path);
    const lang = file?.language ?? 'python';
    const trace = error ? stripAnsi(error.traceback.join('\n')).trim().split('\n').slice(-30).join('\n') : '';
    const head = error
      ? `This cell in the notebook ${name} fails with ${error.ename}: ${stripAnsi(error.evalue)}. Find the cause and say how to fix it.`
      : `Explain what this cell in the notebook ${name} does, step by step, and anything in it that looks wrong.`;
    const room = MAX_QUESTION - head.length - 60 - (error ? Math.min(trace.length, 1400) + 40 : 0);
    const codeText = cell.source.length > room ? `${cell.source.slice(0, Math.max(0, room))}\n…` : cell.source;
    const text = `${head}\n\n\`\`\`${lang}\n${codeText}\n\`\`\`${error ? `\n\nTraceback (last lines):\n\`\`\`\n${trace.slice(-1400)}\n\`\`\`` : ''}`;
    try {
      const made = await api.newSession(projectId, error ? `Fix ${error.ename} in ${name}` : `Explain a cell in ${name}`);
      await api.askSession(made.ref, text.slice(0, MAX_QUESTION));
      navigate(`/sessions?ref=${encodeURIComponent(made.ref)}`);
    } catch (e) {
      toast.error('The session was not started', { description: reason(e) });
    }
  };

  // ── keys: ⌘S saves; with no editor focused, the arrows move and a/b/m/y/Enter act as in Jupyter ─
  const onKeyDown = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    if ((e.metaKey || e.ctrlKey) && !e.altKey && e.key.toLowerCase() === 's') {
      e.preventDefault();
      void save();
      return;
    }
    const inEditor = (e.target as HTMLElement).closest('.cm-editor, input, textarea, select, button, [role="menu"]');
    if (inEditor || e.metaKey || e.ctrlKey || e.altKey || !selected) return;
    const at = cells.findIndex((c) => c.id === selected);
    if (at < 0) return;
    if (e.key === 'ArrowDown' && at < cells.length - 1) { e.preventDefault(); setSelected(cells[at + 1].id); }
    else if (e.key === 'ArrowUp' && at > 0) { e.preventDefault(); setSelected(cells[at - 1].id); }
    else if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      if (cells[at].cell_type !== 'code') setEditing((s) => new Set(s).add(selected));
      setFocusAt({ id: selected, nonce: Date.now() });
    } else if (e.key === 'Enter' && e.shiftKey) { e.preventDefault(); runCell(selected, 'next'); }
    else if (readOnly) return;
    else if (e.key === 'a') { e.preventDefault(); insert(at, 'code'); }
    else if (e.key === 'b') { e.preventDefault(); insert(at + 1, 'code'); }
    else if (e.key === 'm') { e.preventDefault(); retype(selected, 'markdown'); }
    else if (e.key === 'y') { e.preventDefault(); retype(selected, 'code'); }
  };

  // ── drawing ───────────────────────────────────────────────────
  if (loadError) {
    return (
      <Empty icon={<FileWarning className="size-6" />} title={`${baseName(path)} did not open`} hint={loadError}
        action={<Button size="sm" variant="outline" onClick={() => { setLoadError(null); setLoadNonce((n) => n + 1); }}>Try again</Button>} />
    );
  }
  if (!file) {
    return <div className="flex h-full items-center justify-center gap-2 text-[13px] text-dim"><Loader2 className="size-4 animate-spin" />Opening {baseName(path)}…</div>;
  }

  const live = isLive(kernel) ? kernel : null;
  const busy = live?.status === 'busy' || Object.values(runState).some((s) => s === 'running' || s === 'queued');
  const kernelLabel = live ? live.displayName : choice?.displayName ?? null;
  const statusWord = starting ? 'starting' : live ? live.status : kernel?.status === 'dead' ? 'stopped' : 'not started';
  const tone = starting || live?.status === 'starting' || live?.status === 'restarting' ? 'warn' : live?.status === 'busy' ? 'info' : live ? 'ok' : kernel?.status === 'dead' ? 'danger' : 'neutral';

  return (
    <div className="flex h-full min-h-0 flex-col bg-bg" onKeyDown={onKeyDown}>
      {/* The toolbar: the kernel, what runs, and the file. */}
      <div className="flex shrink-0 flex-wrap items-center gap-1.5 border-b border-line/70 px-3 py-1.5">
        <DropdownMenu>
          <DropdownMenuTrigger render={<button type="button" className="flex min-w-0 max-w-[260px] items-center gap-2 rounded-md px-2 py-1 text-left text-[12.5px] hover:bg-surface-2" aria-label="Kernel" />}>
            <Dot state={tone === 'ok' ? 'ok' : tone === 'info' ? 'info' : tone === 'warn' ? 'warn' : tone === 'danger' ? 'danger' : 'idle'} pulse={tone === 'info' || tone === 'warn'} />
            <span className="min-w-0 truncate text-ink-2">{kernelLabel ?? (options ? 'No kernel' : 'Looking for a kernel…')}</span>
            <span className="shrink-0 text-dim">{statusWord}</span>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="w-80">
            <DropdownMenuGroup>
              <DropdownMenuLabel>Kernels on this machine</DropdownMenuLabel>
              {!options ? (
                <DropdownMenuItem disabled><Loader2 className="size-3.5 animate-spin" />{optionsError ?? 'Looking…'}</DropdownMenuItem>
              ) : options.available.length === 0 ? (
                <DropdownMenuItem disabled>None can run {options.language}</DropdownMenuItem>
              ) : options.available.map((c) => (
                <DropdownMenuItem key={c.name} onClick={() => void switchKernel(c.name)}>
                  <span className="min-w-0 truncate">{c.displayName}</span>
                  <span className="ml-auto shrink-0 text-[11px] text-dim">{c.name === (live?.name ?? choice?.name) ? 'in use' : c.source === 'project' ? 'project' : c.source === 'machine' ? 'machine' : c.language}</span>
                </DropdownMenuItem>
              ))}
            </DropdownMenuGroup>
            {live && (
              <>
                <DropdownMenuSeparator />
                <DropdownMenuItem onClick={() => void shutDown()}><Square className="size-3.5" />Shut the kernel down</DropdownMenuItem>
              </>
            )}
            {(live?.interpreter ?? choice?.interpreter) && (
              <p className="px-2 pt-1 pb-1.5 font-mono text-[11px] break-all text-dim">{live?.interpreter ?? choice?.interpreter}</p>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
        {linkLost && <span className="text-[11.5px] text-warn">Reconnecting…</span>}

        <div className="ml-auto flex flex-wrap items-center gap-1">
          <Button size="sm" variant="ghost" disabled={starting || !cells.some((c) => c.cell_type === 'code')} onClick={() => void run(cells.map((c) => c.id))}>
            <PlayCircle className="size-3.5" />Run all
          </Button>
          <Tooltip><TooltipTrigger render={<Button size="icon-sm" variant="ghost" aria-label="Interrupt" disabled={!live || !busy} onClick={() => void interrupt()} />}>
            <Square className="size-3.5" />
          </TooltipTrigger><TooltipContent>Interrupt the running cell</TooltipContent></Tooltip>
          <DropdownMenu>
            <DropdownMenuTrigger render={<Button size="icon-sm" variant="ghost" aria-label="Restart" disabled={!live} />}>
              <RotateCcw className="size-3.5" />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-56">
              <DropdownMenuItem onClick={() => setConfirmRestart('restart')}>Restart the kernel</DropdownMenuItem>
              <DropdownMenuItem onClick={() => setConfirmRestart('run-all')}>Restart and run all</DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
          <Tooltip><TooltipTrigger render={<Button size="icon-sm" variant="ghost" aria-label="Clear all outputs" disabled={readOnly} onClick={clearOutputs} />}>
            <Eraser className="size-3.5" />
          </TooltipTrigger><TooltipContent>Clear all outputs</TooltipContent></Tooltip>
          <span className="mx-1 h-4 w-px bg-line" aria-hidden />
          {!readOnly && (
            <Button size="sm" variant={dirty ? 'secondary' : 'ghost'} disabled={saving || !dirty} onClick={() => void save()}>
              {saving ? <Loader2 className="size-3.5 animate-spin" /> : <Save className="size-3.5" />}{dirty ? 'Save' : 'Saved'}
            </Button>
          )}
        </div>
      </div>

      {/* What stops cells from running, in the server's words. */}
      {(kernelError || (!live && !starting && options?.missing) || (kernel?.status === 'dead' && kernel.note)) && (
        <div className="flex shrink-0 items-start gap-2 border-b border-line/70 bg-warn/8 px-4 py-2 text-[12.5px] leading-relaxed text-ink-2">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-warn" />
          <p className="min-w-0 flex-1 break-words">{kernelError ?? (kernel?.status === 'dead' ? kernel.note : options?.missing)}</p>
          {kernel?.status === 'dead' && !starting && <Button size="xs" variant="outline" onClick={() => void ensureKernel()}>Start a new kernel</Button>}
        </div>
      )}
      {conflict && (
        <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-line/70 bg-danger/6 px-4 py-2 text-[12.5px] text-ink-2">
          <span className="min-w-0 flex-1">{baseName(path)} changed on disk after you opened it. Nothing was saved.</span>
          <Button size="xs" variant="ghost" onClick={() => setConflict(false)}>Keep editing</Button>
          <Button size="xs" variant="outline" onClick={() => { setConflict(false); setLoadNonce((n) => n + 1); }}>Reload from disk</Button>
          <Button size="xs" variant="destructive" onClick={() => void overwrite()}>Overwrite with mine</Button>
        </div>
      )}

      {/* The cells. */}
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-[980px] px-2 py-4 sm:px-6">
          {cells.length === 0 && (
            <Empty icon={<BookOpen className="size-6" />} title="This notebook has no cells"
              hint="Add a code cell to run something, or a markdown cell to write."
              action={readOnly ? undefined : <div className="flex gap-2"><Button size="sm" onClick={() => insert(0, 'code')}><Code2 className="size-3.5" />Code</Button><Button size="sm" variant="outline" onClick={() => insert(0, 'markdown')}><Type className="size-3.5" />Markdown</Button></div>} />
          )}
          {cells.map((cell, index) => {
            const on = cell.id === selected;
            const state = runState[cell.id];
            const prompt = cell.cell_type !== 'code' ? '' : state === 'running' ? '[*]' : state === 'queued' ? '[ ]' : `[${cell.execution_count ?? ' '}]`;
            const isEditing = cell.cell_type === 'code' || editing.has(cell.id) || (cell.cell_type === 'markdown' && !cell.source.trim() && on);
            const focus = focusAt && focusAt.id === cell.id ? focusAt.nonce : 0;
            return (
              <div key={cell.id}>
                {index === 0 && !readOnly && <Inserter onAdd={(type) => insert(0, type)} />}
                <div onMouseDown={() => setSelected(cell.id)} tabIndex={-1}
                  className={cn('group relative rounded-xl border transition-colors focus:outline-none',
                    on ? 'border-brand/40 bg-surface/60 shadow-[inset_2px_0_0_var(--os-brand)]' : 'border-transparent hover:border-line/70')}>
                  <div className="flex">
                    <div className={cn('w-12 shrink-0 pt-2.5 text-right font-mono text-[11.5px] tnum select-none sm:w-14',
                      state === 'running' ? 'text-info' : state === 'error' ? 'text-danger' : 'text-dim')}>
                      {cell.cell_type === 'code' && (
                        <button type="button" aria-label="Run this cell" onClick={() => runCell(cell.id, 'stay')}
                          className="group/run inline-flex items-center justify-end gap-0.5 pr-1 hover:text-brand">
                          <Play className="hidden size-3 group-hover/run:inline" />
                          <span className="group-hover/run:hidden">{prompt}</span>
                        </button>
                      )}
                    </div>
                    <div className="min-w-0 flex-1 py-1 pr-1">
                      {cell.cell_type === 'code' || cell.cell_type === 'raw' || isEditing ? (
                        <div className={cn('rounded-lg', cell.cell_type === 'code' ? 'bg-surface-2/55' : 'bg-surface-2/35')}>
                          <CellEditor
                            initial={cell.source}
                            language={cell.cell_type === 'code' ? language : cell.cell_type === 'markdown' ? markdownLanguage : null}
                            dark={dark}
                            readOnly={readOnly}
                            placeholderText={cell.cell_type === 'code' ? 'Code — Shift+Enter runs it' : cell.cell_type === 'markdown' ? 'Markdown — Shift+Enter shows it' : 'Raw text, kept as it is'}
                            focus={focus}
                            onChange={(text) => change((was) => was.map((c) => (c.id === cell.id ? { ...c, source: text } : c)))}
                            onRun={(mode) => runCell(cell.id, mode)}
                            onLeave={() => setEditing((s) => { const n = new Set(s); n.delete(cell.id); return n; })}
                            onFocus={() => setSelected(cell.id)}
                          />
                        </div>
                      ) : (
                        <div className="cursor-text px-3 py-1.5 text-[13.5px] text-ink-2" onDoubleClick={() => { if (!readOnly) { setEditing((s) => new Set(s).add(cell.id)); setFocusAt({ id: cell.id, nonce: Date.now() }); } }}>
                          {cell.source.trim() ? <Markdown text={cell.source} attachments={cell.attachments} />
                            : <p className="text-dim italic">Empty markdown cell. Double-click to write.</p>}
                        </div>
                      )}
                      {cell.cell_type === 'code' && cell.outputs && cell.outputs.length > 0 && (
                        <Outputs outputs={cell.outputs} onFix={canAsk && projectId ? (error) => void ask(cell, error) : undefined} />
                      )}
                    </div>
                  </div>

                  {/* What can be done to a cell, shown on hover and on the selected one. */}
                  <div className={cn('absolute -top-3 right-2 flex items-center gap-0.5 rounded-lg border border-line/70 bg-surface px-0.5 shadow-sm transition-opacity',
                    on ? 'opacity-100' : 'opacity-0 group-hover:opacity-100 focus-within:opacity-100')}>
                    {cell.cell_type === 'code' && (
                      <Button size="icon-xs" variant="ghost" aria-label="Run this cell" title="Run (⌘Enter)" onClick={() => runCell(cell.id, 'stay')}><Play /></Button>
                    )}
                    {cell.cell_type === 'code' && canAsk && (
                      <Button size="icon-xs" variant="ghost" aria-label="Explain this cell" disabled={!projectId}
                        title={projectId ? 'Explain this cell in a new session' : 'Open this notebook from a project to ask about it'}
                        onClick={() => void ask(cell)}><MessageCircleQuestion /></Button>
                    )}
                    {!readOnly && (
                      <>
                        <Button size="icon-xs" variant="ghost" aria-label="Move up" title="Move up" disabled={index === 0} onClick={() => move(cell.id, -1)}><ArrowUp /></Button>
                        <Button size="icon-xs" variant="ghost" aria-label="Move down" title="Move down" disabled={index === cells.length - 1} onClick={() => move(cell.id, 1)}><ArrowDown /></Button>
                        <DropdownMenu>
                          <DropdownMenuTrigger render={<Button size="xs" variant="ghost" aria-label="Cell type" className="px-1.5 text-[11px] text-soft" />}>
                            {cell.cell_type === 'code' ? 'Code' : cell.cell_type === 'markdown' ? 'Markdown' : 'Raw'}
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end" className="w-40">
                            <DropdownMenuItem onClick={() => retype(cell.id, 'code')}>Code</DropdownMenuItem>
                            <DropdownMenuItem onClick={() => retype(cell.id, 'markdown')}>Markdown</DropdownMenuItem>
                            <DropdownMenuItem onClick={() => retype(cell.id, 'raw')}>Raw</DropdownMenuItem>
                          </DropdownMenuContent>
                        </DropdownMenu>
                        <Button size="icon-xs" variant="ghost" aria-label="Delete this cell" title="Delete" onClick={() => remove(cell.id)}><Trash2 /></Button>
                      </>
                    )}
                  </div>
                </div>
                {!readOnly && <Inserter onAdd={(type) => insert(index + 1, type)} />}
              </div>
            );
          })}
          <More label="Shortcuts" className="px-14 pt-4 pb-8">
            <ul className="space-y-0.5 text-[11.5px] text-dim">
              <li>Shift+Enter runs a cell and moves on</li>
              <li>⌘Enter runs it in place</li>
              <li>Esc, then A or B adds a cell above or below</li>
              <li>⌘S saves</li>
            </ul>
          </More>
        </div>
      </div>

      <Dialog open={!!confirmRestart} onOpenChange={(o) => { if (!o) setConfirmRestart(null); }}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{confirmRestart === 'run-all' ? 'Restart the kernel and run every cell?' : 'Restart the kernel?'}</DialogTitle>
            <DialogDescription>Its variables are lost and a running cell stops. The notebook is unchanged.</DialogDescription>
          </DialogHeader>
          <DialogFooter className="flex-col gap-2 sm:flex-row">
            <Button variant="ghost" onClick={() => setConfirmRestart(null)}>Cancel</Button>
            <Button onClick={() => void restart(confirmRestart === 'run-all')}>{confirmRestart === 'run-all' ? 'Restart and run all' : 'Restart'}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

/** The thin line between cells that adds one: invisible until the pointer is near. */
function Inserter({ onAdd }: { onAdd: (type: CellType) => void }) {
  return (
    <div className="group/ins relative flex h-4 items-center justify-center">
      <div className="absolute inset-x-14 top-1/2 h-px bg-transparent group-hover/ins:bg-line" />
      <div className="relative flex gap-1 opacity-0 transition-opacity group-hover/ins:opacity-100 focus-within:opacity-100">
        <Button size="xs" variant="outline" className="h-5 bg-bg px-1.5 text-[11px]" onClick={() => onAdd('code')}><Plus className="size-3" />Code</Button>
        <Button size="xs" variant="outline" className="h-5 bg-bg px-1.5 text-[11px]" onClick={() => onAdd('markdown')}><Plus className="size-3" />Markdown</Button>
      </div>
    </div>
  );
}
