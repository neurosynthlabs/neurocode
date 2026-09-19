import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Bot, Brain, ChevronRight, FoldVertical, Loader2, MessageSquarePlus, Send, Square, Wrench } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Ascii, Bar, Dot, Empty, ListRow, Mono, Page, PageBody, PageHeader, Panel, Tag } from '@/components/os';
import { api, type ChatMessage, type ChatStream, type SessionDoc } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { useData } from '@/lib/data';
import { useRemote } from '@/lib/remote';
import { cn } from '@/lib/utils';
import { ago } from '@/pages/code/format';

/* Sessions: a conversation that can read this project's code. You ask, it reaches for a tool, the
   tool runs here, and every turn is written down before anything else happens. The answer is shown as
   it is written, with the model's reasoning folded above it, and written once, whole, when it is done. */

const TOOL_LABEL: Record<string, string> = {
  search_code: 'searched the code', read_file: 'read a file', list_files: 'listed files',
  impact: 'traced the blast radius', search_memory: 'searched memory', project_summary: 'looked at the project',
};

function toggled(all: ReadonlySet<string>, ref: string, on: boolean): ReadonlySet<string> {
  if (all.has(ref) === on) return all;
  const next = new Set(all);
  if (on) next.add(ref); else next.delete(ref);
  return next;
}

/** What has arrived of the answer being written: appended by position, so a missed piece stops the rest. */
interface Pending { step: number; answer: string; reasoning: string; ms: number; broken: boolean }

function appended(before: Pending | undefined, s: ChatStream): Pending {
  const p: Pending = before && before.step === s.step && !s.restart ? before : { step: s.step, answer: '', reasoning: '', ms: 0, broken: false };
  if (s.restart || p.broken) return p;
  let { answer, reasoning } = p;
  let broken = false;
  if (s.answer !== undefined) {
    if (s.answerAt === answer.length) answer += s.answer; else broken = true;
  }
  if (s.reasoning !== undefined) {
    if (s.reasoningAt === reasoning.length) reasoning += s.reasoning; else broken = true;
  }
  return { step: s.step, answer, reasoning, ms: s.ms ?? p.ms, broken };
}

const tokens = (n: number) => (n >= 10_000 ? `${Math.round(n / 1000)}k` : n >= 1000 ? `${(n / 1000).toFixed(1)}k` : `${n}`);
const seconds = (ms: number) => `${(ms / 1000).toFixed(1)} s`;

export default function Sessions() {
  const { projects, onChat } = useData();
  const { can } = useAuth();
  const linked = useSearchParams()[0].get('ref');
  const [picked, setPicked] = useState<string | null>(null);
  const [list, setList] = useState<SessionDoc[] | null>(null);
  const [live, setLive] = useState<Record<string, ChatMessage[]>>({});
  const [pending, setPending] = useState<Record<string, Pending>>({});
  const [draft, setDraft] = useState('');
  // Which sessions are waiting on an answer. Kept per session, not as one flag, so switching to
  // another session while one is answering neither hides that wait nor locks the other's Ask.
  const [waiting, setWaiting] = useState<ReadonlySet<string>>(() => new Set());
  const [choosing, setChoosing] = useState(false);
  const [compacting, setCompacting] = useState(false);
  const [open, setOpen] = useState<number | null>(null);
  const box = useRef<HTMLDivElement>(null);

  const remote = useRemote('sessions', () => api.sessions());
  const sessions = list ?? remote.data ?? [];      // what we know locally wins: it is never older
  const ref = picked ?? linked ?? sessions[0]?.ref ?? null;
  const session = sessions.find((s) => s.ref === ref) ?? null;
  const thinking = ref !== null && waiting.has(ref);
  const wait = (sessionRef: string, on: boolean) => setWaiting((all) => toggled(all, sessionRef, on));
  const refreshList = () => api.sessions().then(setList, (e: unknown) => console.error('[NeuroCode] GET /sessions failed:', e));

  const detail = useRemote(ref, () => api.session(ref ?? ''));
  const reloadDetail = useRef(detail.reload);
  useEffect(() => { reloadDetail.current = detail.reload; }, [detail.reload]);

  // Turns arrive as they are written: your question, each tool call, then the answer. Kept per
  // session, so switching away and back does not lose what arrived while you were elsewhere. The words
  // of an answer still being written arrive as `stream` pieces and are dropped once the turn is in.
  useEffect(() => onChat((e) => {
    if (e.stream) {
      const s = e.stream;
      setPending((all) => ({ ...all, [e.sessionRef]: appended(all[e.sessionRef], s) }));
      return;
    }
    const m = e;
    setLive((all) => {
      const mine = all[m.sessionRef] ?? [];
      return mine.some((x) => x.id === m.id) ? all : { ...all, [m.sessionRef]: [...mine, m] };
    });
    if (m.role !== 'you') setPending((all) => {
      if (!(m.sessionRef in all)) return all;
      const next = { ...all };
      delete next[m.sessionRef];
      return next;
    });
    // A summary marks older turns folded: read them again so they show as such.
    if (m.role === 'summary') reloadDetail.current();
    // An answer ends that session's wait whichever session is on screen now.
    if (m.role === 'assistant' || m.role === 'note') {
      setWaiting((all) => toggled(all, m.sessionRef, false));
      void refreshList();
    }
  }), [onChat]);

  const messages = useMemo(() => {
    const seen = new Set<number>();
    return [...(detail.data?.messages ?? []), ...(live[ref ?? ''] ?? [])]
      .filter((m) => !seen.has(m.id) && seen.add(m.id));
  }, [detail.data, live, ref]);
  const writing = ref ? pending[ref] : undefined;

  useEffect(() => { if (box.current) box.current.scrollTop = box.current.scrollHeight; }, [messages.length, thinking, writing?.answer.length, writing?.reasoning.length]);

  const begin = async (projectId: string, name: string) => {
    setChoosing(false);
    try {
      const made = await api.newSession(projectId);
      setList((all) => [made, ...(all ?? [])]);
      setPicked(made.ref);
      toast.success(`${made.ref} started`, { description: `Reading ${name}.` });
    } catch (e) {
      toast.error('Session not started', { description: e instanceof Error ? e.message : 'The API refused.' });
    }
  };

  const send = async () => {
    const text = draft.trim();
    if (!text || !session || thinking) return;
    setDraft('');
    wait(session.ref, true);
    try {
      const { message } = await api.askSession(session.ref, text);
      setLive((all) => {
        const mine = all[session.ref] ?? [];
        return mine.some((x) => x.id === message.id) ? all : { ...all, [session.ref]: [...mine, message] };
      });
    } catch (e) {
      wait(session.ref, false);
      setDraft(text);
      toast.error('Not asked', { description: e instanceof Error ? e.message : 'The API refused.' });
    }
  };

  const compact = async () => {
    if (!session || compacting) return;
    setCompacting(true);
    try {
      const { summary, session: after } = await api.compactSession(session.ref);
      setList((all) => (all ?? sessions).map((s) => (s.ref === after.ref ? after : s)));
      detail.reload();
      toast.success(`${summary.folded?.turns ?? 0} turns folded`, { description: 'The model is sent the summary instead. Every turn is still here to read.' });
    } catch (e) {
      toast.error('Nothing was folded', { description: e instanceof Error ? e.message : 'The API refused.' });
    } finally {
      setCompacting(false);
    }
  };

  const keys = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void send(); }
  };

  const turn = (m: ChatMessage) => m.role === 'you' ? (
    <div key={m.id} className="flex justify-end">
      <p className="max-w-[85%] rounded-xl rounded-br-sm bg-brand/12 px-3.5 py-2 text-[13.5px] whitespace-pre-wrap text-ink">{m.text}</p>
    </div>
  ) : m.role === 'tool' ? (
    <div key={m.id}>
      <button onClick={() => setOpen(open === m.id ? null : m.id)}
        className="flex w-full items-center gap-2 rounded-lg border border-line/70 bg-surface-2/50 px-3 py-1.5 text-left transition-colors hover:bg-surface-2">
        <Wrench className={cn('size-3.5 shrink-0', m.ok === false ? 'text-warn' : 'text-dim')} />
        <span className="min-w-0 flex-1 truncate text-[12.5px] text-ink-2">
          {TOOL_LABEL[m.tool ?? ''] ?? m.tool} · <span className="text-dim">{m.detail}</span>
        </span>
        {m.reasoning && <Brain className="size-3.5 shrink-0 text-dim" aria-label="It reasoned before choosing this" />}
        {m.why && <span className="hidden min-w-0 truncate text-[12px] text-soft sm:block">{m.why}</span>}
      </button>
      {open === m.id && (
        <div className="mt-1.5 space-y-1.5">
          {m.reasoning && <Reasoning text={m.reasoning} label="Why it chose this tool" />}
          <Ascii className="max-h-[280px] overflow-auto text-[12px]">{m.text}</Ascii>
        </div>
      )}
    </div>
  ) : m.role === 'note' ? (
    <div key={m.id} className="space-y-1.5">
      {m.reasoning && <Reasoning text={m.reasoning} label="What it had reasoned" />}
      <Panel className="border-warn/40" eyebrow="Nothing was invented" title="It could not answer">
        <p className="text-[13px] text-ink-2">{m.text}</p>
      </Panel>
    </div>
  ) : m.role === 'summary' ? (
    <Panel key={m.id} className="border-brand/30" eyebrow={`Written by ${m.lane ?? 'a model'}${m.model ? ` · ${m.model}` : ''}`}
      title={<span className="flex items-center gap-2"><FoldVertical className="size-4 text-brand" />Summary of {m.folded?.turns ?? 0} earlier turns</span>}>
      <p className="text-[13px] leading-relaxed whitespace-pre-wrap text-ink-2">{m.text}</p>
      <p className="mt-2 text-[12px] text-dim">The model is sent this summary instead of those turns. They are kept above, folded, to read.</p>
    </Panel>
  ) : (
    <div key={m.id} className="max-w-[92%] space-y-1.5">
      {m.reasoning && <Reasoning text={m.reasoning} label={thoughtLabel(m)} />}
      <p className="rounded-xl rounded-bl-sm bg-surface-2/70 px-3.5 py-2.5 text-[13.5px] leading-relaxed whitespace-pre-wrap text-ink">{m.text}</p>
      <p className="flex flex-wrap items-center gap-2 text-[11.5px] text-dim">
        {m.detail === 'stopped' && <Tag tone="warn">stopped</Tag>}
        {m.lane && <Tag tone="neutral">{m.lane}</Tag>}{m.model}{m.ms ? ` · ${seconds(m.ms)}` : ''}
      </p>
    </div>
  );

  return (
    <Page>
      <PageHeader
        title="Sessions"
        subtitle="You ask; it reads the code with tools and answers from what it found. Every turn is written down before anything else happens, so nothing is lost on a reload."
        actions={can('sessions:chat') && (
          <Button size="sm" onClick={() => setChoosing((v) => !v)}><MessageSquarePlus className="size-3.5" />New session</Button>
        )}
      />
      <PageBody className="flex h-full flex-col gap-3 p-0">
        <div className="flex min-h-0 flex-1 flex-col gap-3 px-4 pt-4 pb-4 sm:px-6 md:flex-row">
          <div className="flex w-full shrink-0 max-h-[32vh] md:max-h-none md:w-[280px] flex-col overflow-y-auto rounded-xl border border-line bg-surface">
            <div className="shrink-0 border-b border-line px-4 py-2.5 text-[12px] font-medium text-dim">
              {choosing ? 'Pick a project' : `${sessions.length} sessions`}
            </div>
            <div className="no-scrollbar min-h-0 flex-1 overflow-y-auto">
              {choosing
                ? projects.map((p) => (
                  <ListRow key={p.id} onClick={() => void begin(p.id, p.name)}>
                    <p className="truncate text-[13.5px] text-ink">{p.name}</p>
                    <p className="mt-0.5 truncate text-[11.5px] text-dim">{p.stack.length ? p.stack.join(' · ') : p.id}</p>
                  </ListRow>
                ))
                : sessions.map((s) => (
                  <ListRow key={s.ref} active={s.ref === ref} onClick={() => setPicked(s.ref)}>
                    <div className="flex items-center gap-2">
                      <Dot state={s.status === 'thinking' ? 'running' : 'idle'} pulse={s.status === 'thinking'} />
                      <Mono>{s.ref}</Mono>
                      <span className="ml-auto text-[11.5px] text-dim">{ago(s.lastAt)}</span>
                    </div>
                    <p className="mt-1 truncate text-[13px] text-ink">{s.title}</p>
                    <p className="mt-1 truncate text-[11.5px] text-dim">{s.projectName} · {s.toolCalls} tool calls</p>
                  </ListRow>
                ))}
            </div>
          </div>

          {!session ? (
            <div className="flex min-w-0 flex-1 items-center justify-center rounded-xl border border-line bg-surface">
              <Empty icon={<Bot className="size-6" />} title="No session yet"
                hint="Start one on a project whose code is on this machine. It can search the code, read files, trace what depends on what, and search memory." />
            </div>
          ) : (
            <div className="flex min-w-0 flex-1 flex-col gap-3">
              <Context session={session} files={detail.data?.instructions ?? null} busy={compacting || thinking}
                canCompact={can('sessions:chat')} onCompact={() => void compact()} />
              <div ref={box} className="min-h-0 flex-1 space-y-2.5 overflow-y-auto rounded-xl border border-line bg-surface p-4">
                {messages.length === 0 && <p className="text-[13px] text-soft">Ask it anything about {session.projectName}.</p>}
                {grouped(messages).map((g) => g.folded
                  ? <Folded key={`folded-${g.turns[0].id}`} count={g.turns.length}>{g.turns.map(turn)}</Folded>
                  : turn(g.turns[0]))}
                {thinking && (writing && (writing.answer || writing.reasoning) ? (
                  <div className="max-w-[92%] space-y-1.5" aria-live="polite">
                    {writing.reasoning && <Reasoning text={writing.reasoning} label={writing.answer ? `Thought for ${seconds(writing.ms)}` : 'Thinking…'} live={!writing.answer} />}
                    {writing.answer && (
                      <p className="rounded-xl rounded-bl-sm bg-surface-2/70 px-3.5 py-2.5 text-[13.5px] leading-relaxed whitespace-pre-wrap text-ink">
                        {writing.answer}<span className="ml-0.5 inline-block h-3.5 w-1.5 animate-pulse bg-brand/70 align-middle" />
                      </p>
                    )}
                    {writing.broken && <p className="text-[11.5px] text-dim">A piece was missed on the way; the whole answer replaces this when it is in.</p>}
                  </div>
                ) : (
                  <p className="flex items-center gap-2 text-[12.5px] text-dim"><Loader2 className="size-3.5 animate-spin" />reading, then answering…</p>
                ))}
              </div>

              <div className="rounded-xl border border-line bg-surface p-2.5">
                <textarea
                  value={draft} onChange={(e) => setDraft(e.target.value)} onKeyDown={keys} rows={2}
                  disabled={!can('sessions:chat')}
                  placeholder={can('sessions:chat') ? `Ask about ${session.projectName}… (Enter sends, Shift+Enter for a new line)` : 'Your role cannot use sessions.'}
                  className="w-full resize-none bg-transparent px-2 py-1.5 text-[13.5px] text-ink outline-none placeholder:text-dim"
                />
                <div className="flex items-center gap-2 border-t border-line/60 px-1 pt-2">
                  <span className="min-w-0 flex-1 truncate text-[11.5px] text-dim">
                    {session.ref} · {session.projectName} · it reads only; nothing is written or run from here
                  </span>
                  {thinking && (
                    <Button size="sm" variant="outline" onClick={() => void api.cancelSession(session.ref).catch(() => undefined)}>
                      <Square className="size-3.5" />Stop
                    </Button>
                  )}
                  <Button size="sm" onClick={() => void send()} disabled={!draft.trim() || thinking || !can('sessions:chat')}>
                    <Send className="size-3.5" />Ask
                  </Button>
                </div>
              </div>
            </div>
          )}
        </div>
      </PageBody>
    </Page>
  );
}

function thoughtLabel(m: ChatMessage): string {
  const ms = m.thought?.ms;
  const n = m.thought?.tokens;
  if (ms) return `Thought for ${seconds(ms)}${n ? ` · ${tokens(n)} tokens` : ''}`;
  return n ? `Reasoning · ${tokens(n)} tokens` : 'Reasoning';
}

/** Runs of folded turns, each run shown as one closed row, in the order they were said. */
function grouped(messages: ChatMessage[]): { folded: boolean; turns: ChatMessage[] }[] {
  const out: { folded: boolean; turns: ChatMessage[] }[] = [];
  for (const m of messages) {
    const last = out[out.length - 1];
    if (m.compacted && last?.folded) last.turns.push(m);
    else out.push({ folded: !!m.compacted, turns: [m] });
  }
  return out;
}

function Folded({ count, children }: { count: number; children: ReactNode }) {
  const [shown, setShown] = useState(false);
  return (
    <div className="rounded-lg border border-dashed border-line/80">
      <button onClick={() => setShown((v) => !v)} aria-expanded={shown}
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-[12.5px] text-dim transition-colors hover:text-ink-2">
        <ChevronRight className={cn('size-3.5 shrink-0 transition-transform', shown && 'rotate-90')} />
        {count} earlier {count === 1 ? 'turn' : 'turns'}, folded into a summary — no longer sent to the model
      </button>
      {shown && <div className="space-y-2.5 border-t border-dashed border-line/80 p-3 opacity-80">{children}</div>}
    </div>
  );
}

/** The model's reasoning, folded by default; open while it is still being written. */
function Reasoning({ text, label, live = false }: { text: string; label: string; live?: boolean }) {
  // Follows the writing (open while it thinks, closed once it answers) until a person opens or closes it.
  const [chosen, setChosen] = useState<boolean | null>(null);
  const shown = chosen ?? live;
  return (
    <div className="rounded-lg bg-surface-2/40">
      <button onClick={() => setChosen(!shown)} aria-expanded={shown}
        className="flex w-full items-center gap-1.5 px-2.5 py-1 text-left text-[12px] text-dim transition-colors hover:text-ink-2">
        {live ? <Loader2 className="size-3 animate-spin" /> : <Brain className="size-3" />}
        {label}
        <ChevronRight className={cn('ml-auto size-3 transition-transform', shown && 'rotate-90')} />
      </button>
      {shown && <p className="max-h-[240px] overflow-y-auto px-2.5 pb-2 text-[12.5px] leading-relaxed whitespace-pre-wrap text-soft">{text}</p>}
    </div>
  );
}

/** How much of the answering model's window the last call filled, and the one control that frees it. */
function Context({ session, files, busy, canCompact, onCompact }: {
  session: SessionDoc; files: { path: string; bytes: number }[] | null; busy: boolean; canCompact: boolean; onCompact: () => void;
}) {
  const used = session.contextTokens;
  const window = session.contextWindow;
  const share = used !== null && window ? Math.min(100, Math.round((100 * used) / window)) : null;
  const near = share !== null && share >= Math.round(session.autoCompactAt * 100);
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-xl border border-line bg-surface px-3.5 py-2">
      <div className="flex min-w-0 flex-1 items-center gap-2.5">
        <span className="eyebrow shrink-0">Context</span>
        {used === null ? (
          <span className="truncate text-[12.5px] text-dim">No model has answered yet</span>
        ) : share === null ? (
          <span className="truncate text-[12.5px] text-ink-2">
            <span className="tnum">{tokens(used)}</span> tokens on the last call <span className="text-dim">· {session.model ?? 'this model'} publishes no window here</span>
          </span>
        ) : (
          <>
            <Bar className="w-20 shrink-0 sm:w-28" pct={share} tone={near ? 'warn' : 'brand'} />
            <span className="truncate text-[12.5px] text-ink-2">
              <span className="tnum">{tokens(used)} / {tokens(window ?? 0)}</span> <span className="text-dim">· {share}% of {session.lane ?? 'the lane'}{near ? ' · folds before the next answer' : ''}</span>
            </span>
          </>
        )}
      </div>
      {files && files.length > 0 && (
        <span className="hidden truncate text-[12px] text-dim lg:block" title={files.map((f) => f.path).join(', ')}>
          follows {files.map((f) => f.path).join(', ')}
        </span>
      )}
      {canCompact && (
        <Button size="xs" variant="outline" disabled={busy} onClick={onCompact}
          title="Fold the older turns into one summary a model writes. Every turn stays here to read.">
          {busy ? <Loader2 className="size-3 animate-spin" /> : <FoldVertical className="size-3" />}Compact
        </Button>
      )}
    </div>
  );
}
