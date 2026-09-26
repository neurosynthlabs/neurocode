import { useEffect, useMemo, useRef, useState, type ChangeEvent, type DragEvent, type KeyboardEvent, type ReactNode } from 'react';
import { AtSign, Box, Brain, FileText, Image as ImageIcon, Loader2, Paperclip, Send, Slash, Square, SquareCode, X } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Mono } from '@/components/os';
import type { ChatAttachment, SessionDoc } from '@/lib/api';
import { extensions, type LiveCommand } from '@/lib/live/extensions';
import { IMAGE_TYPES, UPLOAD_LIMITS, readAsBase64, sessionsApi, type Mention } from '@/lib/live/sessions';
import { cn } from '@/lib/utils';

/* The composer: what a person types, and what they attach to it. `@` opens a picker over the project's
   files and symbols, its memory facts and its plans; a picked item becomes a chip, and its content goes
   into the turn as context — no tool call. `/` at the start opens the project's commands; picking one
   puts its name in the box and the server expands it, visibly, when the question is asked. A paperclip
   (or a drop) uploads text files and pictures; a picture goes only to a lane whose model reads images,
   and the server says so in words when none can. */

export type Chip = Pick<ChatAttachment, 'kind' | 'ref' | 'name' | 'image'> & { detail?: string };

const KIND_ICON: Record<Chip['kind'], ReactNode> = {
  file: <FileText className="size-3.5" />, symbol: <SquareCode className="size-3.5" />, fact: <Brain className="size-3.5" />,
  plan: <Box className="size-3.5" />, upload: <Paperclip className="size-3.5" />,
};

/** What is being picked, from the text before the caret: `@word` anywhere, or `/word` opening the box. */
function trigger(text: string, caret: number): { kind: 'mention' | 'command'; query: string; from: number } | null {
  const before = text.slice(0, caret);
  const at = /(^|\s)@([^\s@]{0,60})$/.exec(before);
  if (at) return { kind: 'mention', query: at[2], from: caret - at[2].length - 1 };
  const slash = /^\/([\w:.-]{0,60})$/.exec(before);
  if (slash) return { kind: 'command', query: slash[1], from: 0 };
  return null;
}

export function Composer({ session, canAsk, thinking, waiting, onSend, onStop }: {
  session: SessionDoc;
  canAsk: boolean;
  thinking: boolean;
  /** The session waits on a permission card: nothing is asked until it is answered. */
  waiting: boolean;
  onSend: (text: string, chips: Chip[]) => Promise<boolean>;
  onStop: () => void;
}) {
  const [draft, setDraft] = useState('');
  const [chips, setChips] = useState<Chip[]>([]);
  const [caret, setCaret] = useState(0);
  const [picked, setPicked] = useState(0);
  const [closedAt, setClosedAt] = useState<string | null>(null);
  const [found, setFound] = useState<{ q: string; items: Mention[] } | null>(null);
  const [commands, setCommands] = useState<{ project: string; items: LiveCommand[] } | null>(null);
  const [uploading, setUploading] = useState(0);
  const [dragging, setDragging] = useState(false);
  const box = useRef<HTMLTextAreaElement>(null);
  const files = useRef<HTMLInputElement>(null);

  const open = trigger(draft, caret);
  const key = open ? `${open.kind}:${open.from}` : null;
  const shown = open && key !== closedAt ? open : null;

  // Mentions are looked up as the person types after @, a moment after they pause.
  const mentionQuery = shown?.kind === 'mention' ? shown.query : null;
  useEffect(() => {
    if (mentionQuery === null) return;
    const project = session.projectId;
    const timer = setTimeout(() => {
      sessionsApi.mentions(project, mentionQuery).then(
        (r) => setFound({ q: `${project}:${mentionQuery}`, items: r.items }),
        (e: unknown) => console.error('[NeuroCode] GET mentions failed:', e));
    }, 140);
    return () => clearTimeout(timer);
  }, [mentionQuery, session.projectId]);

  // The project's commands are read once per project, the first time `/` opens.
  const wantsCommands = shown?.kind === 'command' && commands?.project !== session.projectId;
  useEffect(() => {
    if (!wantsCommands) return;
    const project = session.projectId;
    extensions.commands(project).then(
      (r) => setCommands({ project, items: r.commands }),
      (e: unknown) => console.error('[NeuroCode] GET /extensions/commands failed:', e));
  }, [wantsCommands, session.projectId]);

  const options = useMemo<{ id: string; label: string; detail: string; icon: ReactNode; pick: () => void }[]>(() => {
    if (!shown) return [];
    if (shown.kind === 'mention') {
      if (found?.q !== `${session.projectId}:${shown.query}`) return [];
      return found.items.map((m) => ({
        id: `${m.kind}:${m.ref}`, label: m.name, detail: m.detail, icon: KIND_ICON[m.kind],
        pick: () => {
          setChips((all) => (all.some((c) => c.kind === m.kind && c.ref === m.ref) ? all : [...all, { kind: m.kind, ref: m.ref, name: m.name, detail: m.detail }]));
          const rest = draft.slice(0, shown.from) + draft.slice(caret);
          setDraft(rest);
          setCaret(shown.from);
        },
      }));
    }
    if (commands?.project !== session.projectId) return [];
    const q = shown.query.toLowerCase();
    return commands.items.filter((c) => c.name.toLowerCase().includes(q)).slice(0, 12).map((c) => ({
      id: c.id, label: c.name, detail: c.description || c.body.split('\n').find((l) => l.trim()) || '', icon: <Slash className="size-3.5" />,
      pick: () => { const text = `${c.name} `; setDraft(text); setCaret(text.length); },
    }));
  }, [shown, found, commands, session.projectId, draft, caret]);
  const active = Math.min(picked, Math.max(0, options.length - 1));

  useEffect(() => {
    const el = box.current;
    if (el && document.activeElement === el) el.setSelectionRange(caret, caret);
  }, [caret]);

  const loading = shown !== null && options.length === 0 && (shown.kind === 'mention'
    ? found?.q !== `${session.projectId}:${shown.query}` : commands?.project !== session.projectId);

  const change = (e: ChangeEvent<HTMLTextAreaElement>) => {
    setDraft(e.target.value);
    setCaret(e.target.selectionStart ?? e.target.value.length);
    setPicked(0);
  };

  const send = async () => {
    const text = draft.trim();
    if (!text || thinking || waiting || !canAsk) return;
    const kept = { draft, chips };
    setDraft(''); setChips([]); setCaret(0);
    if (!(await onSend(text, kept.chips))) { setDraft(kept.draft); setChips(kept.chips); }
  };

  const keys = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (shown && options.length) {
      if (e.key === 'ArrowDown') { e.preventDefault(); setPicked((active + 1) % options.length); return; }
      if (e.key === 'ArrowUp') { e.preventDefault(); setPicked((active - 1 + options.length) % options.length); return; }
      if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); options[active].pick(); setPicked(0); return; }
    }
    if (shown && e.key === 'Escape') { e.preventDefault(); setClosedAt(key); return; }
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void send(); }
  };

  const upload = async (list: FileList | File[]) => {
    for (const file of Array.from(list)) {
      const image = IMAGE_TYPES.includes(file.type);
      const cap = image ? UPLOAD_LIMITS.image : UPLOAD_LIMITS.text;
      if (file.size > cap) {
        toast.error(`${file.name} is too large`, { description: `A ${image ? 'picture' : 'text file'} may be at most ${cap / 1024 / 1024} MB.` });
        continue;
      }
      setUploading((n) => n + 1);
      try {
        const made = await sessionsApi.upload(session.ref, file.name, file.type, await readAsBase64(file));
        setChips((all) => [...all, { kind: 'upload', ref: String(made.id), name: made.name, image: made.image, detail: `${Math.max(1, Math.round(made.bytes / 1024))} KB` }]);
      } catch (e) {
        toast.error(`${file.name} was not attached`, { description: e instanceof Error ? e.message : 'The API refused.' });
      } finally {
        setUploading((n) => n - 1);
      }
    }
  };

  const dropped = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragging(false);
    if (canAsk && e.dataTransfer.files.length) void upload(e.dataTransfer.files);
  };

  const hint = !canAsk ? 'Your role cannot use sessions.'
    : waiting ? 'Answer the permission request above first.'
    : `Ask about ${session.projectName}… (@ to attach, / for commands)`;

  return (
    <div className={cn('relative rounded-xl border bg-surface p-2.5 transition-colors', dragging ? 'border-brand/60' : 'border-line')}
      onDragOver={(e) => { if (canAsk) { e.preventDefault(); setDragging(true); } }} onDragLeave={() => setDragging(false)} onDrop={dropped}>
      {shown && (
        <div role="listbox" aria-label={shown.kind === 'mention' ? 'Attach from the project' : 'Commands'}
          className="absolute right-2.5 bottom-full left-2.5 z-20 mb-1.5 max-h-[280px] overflow-y-auto rounded-xl border border-line bg-surface p-1 shadow-lg">
          <p className="px-2.5 pt-1.5 pb-1 text-[11.5px] text-dim">
            {shown.kind === 'mention' ? 'Attach a file, symbol, fact or plan' : "This project's commands"}
          </p>
          {loading ? (
            <p className="flex items-center gap-2 px-2.5 py-2 text-[12.5px] text-dim"><Loader2 className="size-3.5 animate-spin" />Looking…</p>
          ) : options.length === 0 ? (
            <p className="px-2.5 py-2 text-[12.5px] text-dim">
              {shown.kind === 'mention'
                ? `Nothing matches “${shown.query}”. Files and symbols need the code index.`
                : 'No command matches; it is asked as typed. Commands live in .claude/commands.'}
            </p>
          ) : options.map((o, i) => (
            <button key={o.id} role="option" aria-selected={i === active} type="button"
              onMouseDown={(e) => { e.preventDefault(); o.pick(); setPicked(0); }} onMouseEnter={() => setPicked(i)}
              className={cn('flex w-full items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-left', i === active ? 'bg-surface-2' : '')}>
              <span className="shrink-0 text-dim">{o.icon}</span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[13px] text-ink">{o.label}</span>
                {o.detail && <span className="block truncate text-[11.5px] text-dim">{o.detail}</span>}
              </span>
            </button>
          ))}
        </div>
      )}

      {(chips.length > 0 || uploading > 0) && (
        <div className="flex flex-wrap gap-1.5 px-1 pb-2">
          {chips.map((c) => (
            <span key={`${c.kind}:${c.ref}`} title={c.detail}
              className="inline-flex max-w-[260px] items-center gap-1.5 rounded-lg bg-surface-2 py-1 pr-1 pl-2 text-[12px] text-ink-2">
              {c.image ? <ImageIcon className="size-3.5 shrink-0 text-dim" /> : <span className="shrink-0 text-dim">{KIND_ICON[c.kind]}</span>}
              <span className="truncate">{c.name}</span>
              <button type="button" aria-label={`Remove ${c.name}`} onClick={() => setChips((all) => all.filter((x) => x !== c))}
                className="rounded p-0.5 text-dim hover:bg-surface-3 hover:text-ink"><X className="size-3" /></button>
            </span>
          ))}
          {uploading > 0 && <span className="inline-flex items-center gap-1.5 px-1 text-[12px] text-dim"><Loader2 className="size-3 animate-spin" />Uploading…</span>}
        </div>
      )}

      <textarea ref={box}
        value={draft} onChange={change} onKeyDown={keys} rows={2}
        onSelect={(e) => setCaret(e.currentTarget.selectionStart ?? 0)}
        onBlur={() => setClosedAt(key)} onFocus={() => setClosedAt(null)}
        disabled={!canAsk || waiting}
        placeholder={hint}
        className="w-full resize-none bg-transparent px-2 py-1.5 text-[13.5px] text-ink outline-none placeholder:text-dim"
      />
      <input ref={files} type="file" multiple hidden accept="text/*,.md,.json,.yaml,.yml,.toml,.csv,.sql,.py,.ts,.tsx,.js,.go,.rs,.java,.cs,.rb,.php,.sh,image/png,image/jpeg,image/gif,image/webp"
        onChange={(e) => { if (e.target.files) void upload(e.target.files); e.target.value = ''; }} />
      <div className="flex items-center gap-1.5 border-t border-line/60 px-1 pt-2">
        <Button size="icon-sm" variant="ghost" aria-label="Attach a file or picture" title="A text file up to 1 MB, or a picture up to 5 MB"
          disabled={!canAsk || waiting} onClick={() => files.current?.click()}><Paperclip className="size-3.5" /></Button>
        <Button size="icon-sm" variant="ghost" aria-label="Mention a file, symbol, fact or plan" title="Attach from the project (@)"
          disabled={!canAsk || waiting} onClick={() => {
            const text = draft && !/\s$/.test(draft) ? `${draft} @` : `${draft}@`;
            setDraft(text); setCaret(text.length); setClosedAt(null); box.current?.focus();
          }}><AtSign className="size-3.5" /></Button>
        <span className="min-w-0 flex-1 truncate text-[11.5px] text-dim">
          <Mono>{session.ref}</Mono> · web and MCP tools ask first
        </span>
        {thinking && (
          <Button size="sm" variant="outline" onClick={onStop}><Square className="size-3.5" />Stop</Button>
        )}
        <Button size="sm" onClick={() => void send()} disabled={!draft.trim() || thinking || waiting || !canAsk || uploading > 0}>
          <Send className="size-3.5" />Ask
        </Button>
      </div>
    </div>
  );
}
