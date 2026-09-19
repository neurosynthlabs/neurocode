import { useEffect, useImperativeHandle, useLayoutEffect, useRef, type Ref } from 'react';
import { basicSetup } from 'codemirror';
import { indentWithTab } from '@codemirror/commands';
import { HighlightStyle, LanguageDescription, syntaxHighlighting, type LanguageSupport } from '@codemirror/language';
import { languages } from '@codemirror/language-data';
import { Compartment, EditorSelection, EditorState, Prec, RangeSetBuilder, type Extension, type StateEffect, type Text } from '@codemirror/state';
import { Decoration, EditorView, GutterMarker, gutter, keymap } from '@codemirror/view';
import { tags as t } from '@lezer/highlight';
import { baseName } from '@/lib/live/machine';

/* The Workbench's editor: CodeMirror 6, one view whose state is swapped per tab — so each open file keeps
   its own undo history, selection and scroll while another is shown. Highlighting is picked from the file
   name by CodeMirror's language data (every language it knows, loaded on first use), and the colours are
   the app's own CSS variables, so every palette in both modes repaints the editor with the rest of the app. */

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

const mix = (v: string, pct: number) => `color-mix(in oklab, var(${v}) ${pct}%, transparent)`;

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
  }, { dark });
}

const colours = HighlightStyle.define([
  { tag: [t.keyword, t.controlKeyword, t.moduleKeyword, t.operatorKeyword, t.definitionKeyword, t.modifier], color: 'var(--os-violet)' },
  { tag: [t.string, t.special(t.string), t.regexp, t.character, t.inserted], color: 'var(--os-ok)' },
  { tag: [t.number, t.bool, t.null, t.atom, t.unit], color: 'var(--os-warn)' },
  { tag: [t.comment, t.lineComment, t.blockComment, t.docComment], color: 'var(--os-dim)', fontStyle: 'italic' },
  { tag: [t.function(t.variableName), t.function(t.propertyName), t.definition(t.function(t.variableName)), t.macroName], color: 'var(--os-info)' },
  { tag: [t.typeName, t.className, t.namespace, t.definition(t.typeName), t.self], color: 'var(--os-brand)' },
  { tag: [t.tagName, t.deleted, t.invalid], color: 'var(--os-danger)' },
  { tag: [t.attributeName, t.propertyName, t.labelName], color: 'var(--os-ink-2)' },
  { tag: [t.operator, t.punctuation, t.bracket, t.meta, t.processingInstruction], color: 'var(--os-soft)' },
  { tag: t.heading, color: 'var(--os-brand)', fontWeight: '600' },
  { tag: t.strong, fontWeight: '600' },
  { tag: t.emphasis, fontStyle: 'italic' },
  { tag: t.link, color: 'var(--os-info)', textDecoration: 'underline' },
  { tag: t.strikethrough, textDecoration: 'line-through' },
]);

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

export default function Editor(props: EditorProps) {
  const { doc, open, dark, breakpoints, pausedLine, reveal, readOnly = false, ref } = props;
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  const held = useRef(new Map<string, Held>());
  const current = useRef<string | null>(null);
  // The newest callbacks, read by CodeMirror's handlers, which are made once per file.
  const latest = useRef(props);
  useLayoutEffect(() => { latest.current = props; });

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
      keymap.of([indentWithTab]),
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

  useEffect(() => () => { view.current?.destroy(); view.current = null; }, []);

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

  return <div ref={host} className="h-full min-h-0 overflow-hidden [&_.cm-editor]:h-full" />;
}
