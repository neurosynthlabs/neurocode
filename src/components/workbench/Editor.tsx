import { useEffect, useImperativeHandle, useLayoutEffect, useRef, type MouseEvent, type Ref } from 'react';
import { basicSetup } from 'codemirror';
import { indentWithTab } from '@codemirror/commands';
import { LanguageDescription, syntaxHighlighting, type LanguageSupport } from '@codemirror/language';
import { languages } from '@codemirror/language-data';
import { lintGutter, setDiagnostics, type Diagnostic } from '@codemirror/lint';
import { Compartment, EditorSelection, EditorState, Prec, RangeSetBuilder, type Extension, type StateEffect, type Text } from '@codemirror/state';
import { Decoration, EditorView, GutterMarker, gutter, hoverTooltip, keymap } from '@codemirror/view';
import { toast } from 'sonner';
import { ApiError } from '@/lib/api';
import { isDesktop, onPathMenu } from '@/lib/desktop';
import { diagnosticsApi, lspApi, problemsStore, useProblems, type LspStatus, type Problem } from '@/lib/live/diagnostics';
import { baseName } from '@/lib/live/machine';
import { colours, mix } from '@/components/workbench/syntax';

/* The Workbench's editor: CodeMirror 6, one view whose state is swapped per tab — so each open file keeps
   its own undo history, selection and scroll while another is shown. Highlighting is picked from the file
   name by CodeMirror's language data (every language it knows, loaded on first use), and the colours are
   the app's own CSS variables, so every palette in both modes repaints the editor with the rest of the app.

   What the project's own checkers found in the file is underlined, with the words on hover (the Problems tab
   runs them). When a language server for the file is on this machine, hovering a name asks it what the name
   is, and F12 or ⌘-click goes to where it is defined — in this file, or by opening another. */

/** The file shown: `version` changes when the text was replaced from disk, which starts a fresh history. */
export interface EditorDoc { path: string; text: string; version: number }

/** What the Workbench asks of the editor outside React's data flow. */
export interface EditorHandle {
  /** The text of an open file as it is now, line endings as the file had them; null when it is not open. */
  text: (path: string) => string | null;
  /** The file was saved with this text: it is the new baseline for "unsaved". */
  saved: (path: string, text: string) => void;
  focus: () => void;
}

export interface EditorProps {
  doc: EditorDoc;
  /** Every tab still open; what the editor holds for any other file is dropped. */
  open: string[];
  dark: boolean;
  /** Breakpoint lines (1-based) in the file shown. */
  breakpoints: number[];
  onToggleBreakpoint: (line: number) => void;
  /** The line the debugger is paused on in this file, highlighted and scrolled to. */
  pausedLine: number | null;
  /** Go to a line; a new nonce goes again even to the same line. */
  reveal: { line: number; nonce: number } | null;
  onDirty: (path: string, dirty: boolean) => void;
  onSave: (path: string, text: string) => void;
  onCursor: (line: number, col: number) => void;
  onLanguage: (name: string | null) => void;
  /** The file shown is only read: a reference source or a referenced project. Typing and ⌘S do nothing. */
  readOnly?: boolean;
  ref?: Ref<EditorHandle>;
}

interface Held { state: EditorState; version: number; saved: Text; dirty: boolean; scroll: number; language: string | null }

const languageSlot = new Compartment();
const themeSlot = new Compartment();
const breakpointSlot = new Compartment();
const pausedSlot = new Compartment();
const readOnlySlot = new Compartment();

/** Read only: the text cannot change, but it can still be selected, searched and copied. */
const locked = (on: boolean): Extension => (on ? [EditorState.readOnly.of(true)] : []);

function chrome(dark: boolean): Extension {
  return EditorView.theme({
    '&': { height: '100%', color: 'var(--os-ink)', backgroundColor: 'var(--os-bg)', fontSize: '13px' },
    '&.cm-focused': { outline: 'none' },
    '.cm-scroller': { fontFamily: 'var(--font-mono)', lineHeight: '1.65' },
    '.cm-content': { caretColor: 'var(--os-brand)', padding: '8px 0' },
    '.cm-cursor, .cm-dropCursor': { borderLeftColor: 'var(--os-brand)', borderLeftWidth: '2px' },
    '&.cm-focused > .cm-scroller > .cm-selectionLayer .cm-selectionBackground, .cm-selectionBackground, .cm-content ::selection':
      { backgroundColor: mix('--os-brand', 24) },
    '.cm-gutters': { backgroundColor: 'var(--os-bg)', color: 'var(--os-dim)', border: 'none' },
    '.cm-lineNumbers .cm-gutterElement': { padding: '0 10px 0 6px' },
    '.cm-activeLineGutter': { backgroundColor: 'transparent', color: 'var(--os-ink-2)' },
    '.cm-activeLine': { backgroundColor: mix('--os-surface-2', 60) },
    '.cm-matchingBracket, &.cm-focused .cm-matchingBracket': { backgroundColor: mix('--os-brand', 18), outline: `1px solid ${mix('--os-brand', 45)}` },
    '.cm-nonmatchingBracket, &.cm-focused .cm-nonmatchingBracket': { backgroundColor: mix('--os-danger', 20) },
    '.cm-searchMatch': { backgroundColor: mix('--os-warn', 22), outline: `1px solid ${mix('--os-warn', 45)}` },
    '.cm-searchMatch.cm-searchMatch-selected': { backgroundColor: mix('--os-warn', 40) },
    '.cm-selectionMatch': { backgroundColor: mix('--os-info', 16) },
    '.cm-foldPlaceholder': { backgroundColor: 'var(--os-surface-2)', border: '1px solid var(--os-line)', color: 'var(--os-soft)' },
    '.cm-panels': { backgroundColor: 'var(--os-surface)', color: 'var(--os-ink-2)' },
    '.cm-panels.cm-panels-bottom': { borderTop: '1px solid var(--os-line)' },
    '.cm-panels.cm-panels-top': { borderBottom: '1px solid var(--os-line)' },
    '.cm-panel.cm-search': { padding: '6px 10px', fontSize: '12.5px' },
    '.cm-panel input[type=text], .cm-textfield': {
      backgroundColor: 'var(--os-surface-2)', color: 'var(--os-ink)', border: '1px solid var(--os-line)', borderRadius: '6px',
      padding: '2px 6px', fontSize: '12.5px',
    },
    '.cm-panel button, .cm-button': {
      backgroundImage: 'none', backgroundColor: 'var(--os-surface-2)', color: 'var(--os-ink-2)', border: '1px solid var(--os-line)',
      borderRadius: '6px', padding: '2px 8px', fontSize: '12.5px',
    },
    '.cm-panel label': { fontSize: '12.5px', color: 'var(--os-soft)' },
    '.cm-tooltip': { backgroundColor: 'var(--os-surface)', color: 'var(--os-ink-2)', border: '1px solid var(--os-line)', borderRadius: '8px' },
    '.cm-tooltip-autocomplete > ul > li[aria-selected]': { backgroundColor: mix('--os-brand', 18), color: 'var(--os-ink)' },
    '.cm-breakpoints .cm-gutterElement': { width: '16px', display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer' },
    '.cm-breakpoints .cm-gutterElement:hover .cm-bp-spacer': { opacity: '0.35' },
    '.cm-bp, .cm-bp-spacer': { display: 'inline-block', width: '9px', height: '9px', borderRadius: '9999px', backgroundColor: 'var(--os-danger)' },
    '.cm-bp-spacer': { opacity: '0' },
    '.cm-paused-line': { backgroundColor: mix('--os-warn', 20), boxShadow: 'inset 2px 0 0 var(--os-warn)' },
    '.cm-diagnostic': { padding: '4px 8px 4px 10px', fontSize: '12.5px', fontFamily: 'var(--font-sans)', whiteSpace: 'pre-wrap' },
    '.cm-diagnostic-error': { borderLeft: '3px solid var(--os-danger)' },
    '.cm-diagnostic-warning': { borderLeft: '3px solid var(--os-warn)' },
    '.cm-diagnostic-info': { borderLeft: '3px solid var(--os-info)' },
    '.cm-diagnosticSource': { color: 'var(--os-dim)', fontSize: '11.5px' },
    '.cm-gutter-lint': { width: '12px' },
    '.cm-lsp-hover': { maxWidth: 'min(560px, 90vw)', maxHeight: '320px', overflow: 'auto', padding: '6px 10px', fontSize: '12.5px', lineHeight: '1.55' },
    '.cm-lsp-hover p': { margin: '4px 0', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' },
    '.cm-lsp-hover pre': {
      margin: '4px 0', padding: '6px 8px', borderRadius: '6px', backgroundColor: 'var(--os-surface-2)', fontFamily: 'var(--font-mono)',
      fontSize: '12px', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere',
    },
    '.cm-lsp-hover code': { fontFamily: 'var(--font-mono)', fontSize: '12px', color: 'var(--os-ink)' },
  }, { dark });
}


class Dot extends GutterMarker {
  readonly spacer: boolean;
  constructor(spacer: boolean) { super(); this.spacer = spacer; }
  eq(other: Dot) { return other.spacer === this.spacer; }
  toDOM() {
    const dot = document.createElement('span');
    dot.className = this.spacer ? 'cm-bp-spacer' : 'cm-bp';
    return dot;
  }
}
const BREAKPOINT = new Dot(false);
const SPACER = new Dot(true);

function pausedAt(line: number | null): Extension {
  return EditorView.decorations.compute([], (state) => {
    if (line === null || line < 1 || line > state.doc.lines) return Decoration.none;
    return Decoration.set([Decoration.line({ class: 'cm-paused-line' }).range(state.doc.line(line).from)]);
  });
}

/** A language server's markdown as text nodes only — nothing it says becomes HTML: fenced blocks as code,
    `code` spans as code, the rest as paragraphs with emphasis marks and link targets dropped. */
function hoverDom(markdown: string): HTMLElement {
  const dom = document.createElement('div');
  dom.className = 'cm-lsp-hover';
  markdown.split(/```[\w+#.-]*\n?/).forEach((part, i) => {
    const text = part.replace(/\n+$/, '');
    if (!text.trim()) return;
    if (i % 2 === 1) {
      const pre = document.createElement('pre');
      pre.textContent = text;
      dom.append(pre);
      return;
    }
    for (const para of text.split(/\n{2,}/)) {
      if (!para.trim() || /^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(para)) continue;
      const p = document.createElement('p');
      para.split('`').forEach((bit, j) => {
        if (j % 2 === 1) {
          const code = document.createElement('code');
          code.textContent = bit;
          p.append(code);
        } else {
          p.append(bit.replace(/\[([^\]]*)\]\([^)]*\)/g, '$1').replace(/(\*\*|__)(.+?)\1/g, '$2').replace(/\\([\\`*_{}[\]()#+\-.!])/g, '$1'));
        }
      });
      dom.append(p);
    }
  });
  return dom;
}

/** The checkers' problems as the editor's underlines: a missing end is the rest of the word, or one character. */
function underlines(problems: Problem[], doc: Text): Diagnostic[] {
  const at = (line: number, col: number) => {
    const l = doc.line(Math.max(1, Math.min(line, doc.lines)));
    return Math.min(l.from + Math.max(0, col - 1), l.to);
  };
  return problems.map((p) => {
    const from = at(p.line, p.col);
    let to = p.endLine !== null && p.endCol !== null ? at(p.endLine, p.endCol) : from;
    if (to <= from) {
      const line = doc.lineAt(from);
      const word = /^[\w$]+/.exec(line.text.slice(from - line.from));
      to = Math.min(from + (word ? word[0].length : 1), line.to);
    }
    return { from, to: Math.max(from, to), severity: p.severity, message: p.message, source: p.code ? `${p.tool} ${p.code}` : p.tool };
  });
}

/** A language server is worth asking about this file. */
const asks = (s: LspStatus | undefined) => !!s && (s.state === 'available' || s.state === 'starting' || s.state === 'ready');

export default function Editor(props: EditorProps) {
  const { doc, open, dark, breakpoints, pausedLine, reveal, readOnly = false, ref } = props;
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  const held = useRef(new Map<string, Held>());
  const current = useRef<string | null>(null);
  // The newest callbacks, read by CodeMirror's handlers, which are made once per file.
  const latest = useRef(props);
  useLayoutEffect(() => { latest.current = props; });
  // What the language server says about each file held, and what the checkers found.
  const servers = useRef(new Map<string, LspStatus>());
  const { version: checked } = useProblems();

  const lookAgain = (path: string) => {
    lspApi.status(path).then((s) => {
      servers.current.set(path, s);
      if (current.current === path) problemsStore.setLsp(path, s);
    }, (e: unknown) => console.warn(`[NeuroCode] the language server for ${path} was not asked about:`, e));
  };
  /** The editor's text, when it differs from the file on disk; the server reads the file otherwise. */
  const unsaved = (path: string) => (held.current.get(path)?.dirty ? (held.current.get(path)?.state.sliceDoc() ?? undefined) : undefined);
  const starting = (path: string) => {
    const s = servers.current.get(path);
    if (s?.state === 'available') problemsStore.setLsp(path, { ...s, state: 'starting', message: `${s.server ?? 'The language server'} starting…` });
  };

  const hoverAt = (path: string) => hoverTooltip(async (v, pos) => {
    if (!asks(servers.current.get(path))) return null;
    const line = v.state.doc.lineAt(pos);
    const text = line.text;
    let start = pos - line.from;
    let end = start;
    while (start > 0 && /[\w$]/.test(text[start - 1])) start--;
    while (end < text.length && /[\w$]/.test(text[end])) end++;
    if (start === end) return null;
    starting(path);
    try {
      const said = await lspApi.hover(path, line.number, pos - line.from + 1, unsaved(path));
      if (servers.current.get(path)?.state !== 'ready') lookAgain(path);
      if (!said.markdown) return null;
      const words = said.markdown;
      return { pos: line.from + start, end: line.from + end, above: true, create: () => ({ dom: hoverDom(words) }) };
    } catch (e) {
      console.warn(`[NeuroCode] hover in ${path}:`, e);
      lookAgain(path);
      return null;
    }
  }, { hoverTime: 350 });

  const definitionAt = async (v: EditorView, path: string, pos: number) => {
    const status = servers.current.get(path);
    if (!asks(status)) {
      toast('Go to definition needs a language server', { description: status?.message ?? 'Checking this file for one…' });
      return;
    }
    starting(path);
    const line = v.state.doc.lineAt(pos);
    try {
      const found = await lspApi.definition(path, line.number, pos - line.from + 1, unsaved(path));
      const there = found.locations.find((l) => l.openable);
      if (!there) {
        const away = found.locations[0];
        toast(away ? 'Defined outside the folders this server opens' : 'No definition found for this name',
          { description: away ? `${away.path}:${away.line}` : `${found.server} knows of none.` });
      } else if (there.path === path && current.current === path && view.current) {
        const shown = view.current;
        const l = shown.state.doc.line(Math.max(1, Math.min(there.line, shown.state.doc.lines)));
        const to = Math.min(l.from + there.col - 1, l.to);
        shown.dispatch({ selection: EditorSelection.cursor(to), effects: EditorView.scrollIntoView(to, { y: 'center' }) });
        shown.focus();
      } else if (!problemsStore.open(there.path, there.line)) {
        toast(`Defined in ${baseName(there.path)}, line ${there.line}`, { description: there.path });
      }
    } catch (e) {
      toast.error('Go to definition did not answer', { description: e instanceof ApiError ? e.message : 'The local API did not answer.' });
    }
    if (servers.current.get(path)?.state !== 'ready') lookAgain(path);
  };

  const gutterOf = (lines: number[]): Extension => gutter({
    class: 'cm-breakpoints',
    markers: (v) => {
      const b = new RangeSetBuilder<GutterMarker>();
      for (const n of [...new Set(lines)].sort((a, c) => a - c)) {
        if (n >= 1 && n <= v.state.doc.lines) { const at = v.state.doc.line(n).from; b.add(at, at, BREAKPOINT); }
      }
      return b.finish();
    },
    // A faint dot on hover says where a click would put one; a line that has a breakpoint shows only that.
    lineMarker: (_v, _line, others) => (others.length ? null : SPACER),
    initialSpacer: () => SPACER,
    domEventHandlers: {
      mousedown: (v, block) => {
        latest.current.onToggleBreakpoint(v.state.doc.lineAt(block.from).number);
        return true;
      },
    },
  });

  const make = (path: string, text: string): EditorState => EditorState.create({
    doc: text,
    extensions: [
      // Before basicSetup, so the breakpoint column sits left of the line numbers, where debuggers put it.
      breakpointSlot.of(gutterOf(latest.current.breakpoints)),
      basicSetup,
      // A file written with CRLF keeps CRLF: the document reads lines either way and joins them with the file's own.
      text.includes('\r\n') ? EditorState.lineSeparator.of('\r\n') : [],
      Prec.highest(keymap.of([{
        key: 'Mod-s', preventDefault: true,
        run: (v) => { if (!latest.current.readOnly) latest.current.onSave(path, v.state.sliceDoc()); return true; },
      }])),
      keymap.of([indentWithTab, { key: 'F12', run: (v) => { void definitionAt(v, path, v.state.selection.main.head); return true; } }]),
      // ⌘-click (Ctrl-click off a Mac) goes to the definition, as in every editor with a language server.
      EditorView.domEventHandlers({
        mousedown: (e, v) => {
          if (!(e.metaKey || e.ctrlKey) || e.button !== 0) return false;
          const pos = v.posAtCoords({ x: e.clientX, y: e.clientY });
          if (pos === null) return false;
          e.preventDefault();
          void definitionAt(v, path, pos);
          return true;
        },
      }),
      lintGutter(),
      hoverAt(path),
      syntaxHighlighting(colours),
      languageSlot.of([]),
      themeSlot.of(chrome(latest.current.dark)),
      pausedSlot.of(pausedAt(null)),
      readOnlySlot.of(locked(!!latest.current.readOnly)),
      EditorView.updateListener.of((update) => {
        const mine = held.current.get(path);
        if (!mine) return;
        mine.state = update.state;
        if (update.docChanged) {
          const dirty = !update.state.doc.eq(mine.saved);
          if (dirty !== mine.dirty) { mine.dirty = dirty; latest.current.onDirty(path, dirty); }
        }
        if (update.selectionSet || update.docChanged) {
          const head = update.state.selection.main.head;
          const line = update.state.doc.lineAt(head);
          latest.current.onCursor(line.number, head - line.from + 1);
        }
      }),
    ],
  });

  const loadLanguage = (path: string) => {
    const found = LanguageDescription.matchFilename(languages, baseName(path));
    const entry = held.current.get(path);
    if (entry) entry.language = found?.name ?? null;
    if (current.current === path) latest.current.onLanguage(found?.name ?? null);
    if (!found) return;
    found.load().then((support: LanguageSupport) => {
      const mine = held.current.get(path);
      if (!mine) return;
      if (current.current === path && view.current) view.current.dispatch({ effects: languageSlot.reconfigure(support) });
      else mine.state = mine.state.update({ effects: languageSlot.reconfigure(support) }).state;
    }, (e: unknown) => console.warn(`[NeuroCode] no highlighting for ${path}:`, e));
  };

  // Show the active file: its own state when it is already held, a fresh one when it is new or was reloaded.
  useEffect(() => {
    const leaving = current.current ? held.current.get(current.current) : undefined;
    if (leaving && view.current) { leaving.state = view.current.state; leaving.scroll = view.current.scrollDOM.scrollTop; }
    let entry = held.current.get(doc.path);
    const fresh = !entry || entry.version !== doc.version;
    if (!entry || fresh) {
      const state = make(doc.path, doc.text);
      entry = { state, version: doc.version, saved: state.doc, dirty: false, scroll: 0, language: null };
      held.current.set(doc.path, entry);
    }
    current.current = doc.path;
    if (!view.current) {
      view.current = new EditorView({ state: entry.state, parent: host.current ?? undefined });
    } else {
      view.current.setState(entry.state);
    }
    const shown = view.current;
    shown.dispatch({ effects: [themeSlot.reconfigure(chrome(latest.current.dark)),
      breakpointSlot.reconfigure(gutterOf(latest.current.breakpoints))] });
    if (fresh) { loadLanguage(doc.path); latest.current.onDirty(doc.path, false); }
    else latest.current.onLanguage(entry.language);
    const scroll = entry.scroll;
    requestAnimationFrame(() => { shown.scrollDOM.scrollTop = scroll; });
    const head = shown.state.selection.main.head;
    const line = shown.state.doc.lineAt(head);
    latest.current.onCursor(line.number, head - line.from + 1);
    // make/gutterOf/loadLanguage read refs only; the file and its version are what decide.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [doc.path, doc.version]);

  useEffect(() => () => { view.current?.destroy(); view.current = null; problemsStore.setLsp('', null); }, []);

  // The language server for the file shown: asked about (never started) when the file is.
  useEffect(() => {
    const known = servers.current.get(doc.path);
    problemsStore.setLsp(doc.path, known ?? null);
    lookAgain(doc.path);
    // lookAgain reads refs only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [doc.path]);

  // What the newest check found in this file, underlined — read again when a check finishes. A file with
  // changes not yet saved keeps what it has: the check read the file on disk, whose lines may differ.
  useEffect(() => {
    if (!checked) return;
    const path = doc.path;
    let live = true;
    diagnosticsApi.file(path).then(({ problems }) => {
      const entry = held.current.get(path);
      if (!live || !entry || entry.dirty) return;
      const shown = current.current === path && view.current ? view.current : null;
      const state = shown ? shown.state : entry.state;
      const spec = setDiagnostics(state, underlines(problems, state.doc));
      if (shown) shown.dispatch(spec);
      else entry.state = entry.state.update(spec).state;
    }, (e: unknown) => console.warn(`[NeuroCode] the problems in ${path} were not read:`, e));
    return () => { live = false; };
  }, [checked, doc.path, doc.version]);

  useEffect(() => {
    const keep = new Set(open);
    for (const path of [...held.current.keys()]) if (!keep.has(path)) held.current.delete(path);
  }, [open]);

  useEffect(() => { view.current?.dispatch({ effects: themeSlot.reconfigure(chrome(dark)) }); }, [dark]);

  // Each file's state is made with the slot as it was then; the file shown now is locked or not as it is now.
  useEffect(() => { view.current?.dispatch({ effects: readOnlySlot.reconfigure(locked(readOnly)) }); }, [readOnly, doc.path, doc.version]);

  useEffect(() => {
    view.current?.dispatch({ effects: breakpointSlot.reconfigure(gutterOf(breakpoints)) });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [breakpoints]);

  useEffect(() => {
    const v = view.current;
    if (!v) return;
    const effects: StateEffect<unknown>[] = [pausedSlot.reconfigure(pausedAt(pausedLine))];
    if (pausedLine !== null && pausedLine >= 1 && pausedLine <= v.state.doc.lines) {
      effects.push(EditorView.scrollIntoView(v.state.doc.line(pausedLine).from, { y: 'center' }));
    }
    v.dispatch({ effects });
  }, [pausedLine, doc.path]);

  useEffect(() => {
    const v = view.current;
    if (!v || !reveal) return;
    const n = Math.max(1, Math.min(reveal.line, v.state.doc.lines));
    const at = v.state.doc.line(n).from;
    v.dispatch({ selection: EditorSelection.cursor(at), effects: EditorView.scrollIntoView(at, { y: 'center' }) });
    v.focus();
  }, [reveal]);

  useImperativeHandle(ref, () => ({
    text: (path) => {
      if (current.current === path && view.current) return view.current.state.sliceDoc();
      return held.current.get(path)?.state.sliceDoc() ?? null;
    },
    saved: (path, text) => {
      const mine = held.current.get(path);
      if (!mine) return;
      const now = current.current === path && view.current ? view.current.state : mine.state;
      // The baseline is what was written; typing that happened while the save was in flight stays unsaved.
      mine.saved = now.toText(text);
      const dirty = !now.doc.eq(mine.saved);
      mine.dirty = dirty;
      latest.current.onDirty(path, dirty);
    },
    focus: () => view.current?.focus(),
  }), []);

  // In the desktop app a right click gives the Mac's menu for this file — Cut, Copy and Paste, then Open in the
  // person's editor at the line clicked, Reveal in Finder, Copy Path. A browser keeps its own menu.
  const menu = (e: MouseEvent<HTMLDivElement>) => {
    const v = view.current;
    const at = v?.posAtCoords({ x: e.clientX, y: e.clientY });
    const line = v ? v.state.doc.lineAt(at ?? v.state.selection.main.head).number : undefined;
    onPathMenu(e, current.current, { line, edit: !latest.current.readOnly });
  };

  return <div ref={host} onContextMenu={isDesktop ? menu : undefined} className="h-full min-h-0 overflow-hidden [&_.cm-editor]:h-full" />;
}
