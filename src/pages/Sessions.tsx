import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Bot, Loader2, MessageSquarePlus, Send, Square, Wrench } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Ascii, Dot, Empty, ListRow, Mono, Page, PageBody, PageHeader, Panel, Tag } from '@/components/os';
import { api, type ChatMessage, type SessionDoc } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { useData } from '@/lib/data';
import { useRemote } from '@/lib/remote';
import { cn } from '@/lib/utils';
import { ago } from '@/pages/code/format';

/* Sessions: a conversation that can read this project's code. You ask, it reaches for a tool, the
   tool runs here, and every turn is written down before anything else happens. */

const TOOL_LABEL: Record<string, string> = {
  search_code: 'searched the code', read_file: 'read a file', list_files: 'listed files',
  impact: 'traced the blast radius', search_memory: 'searched memory', project_summary: 'looked at the project',
};

export default function Sessions() {
  const { projects, mode, onChat } = useData();
  const { can } = useAuth();
  const linked = useSearchParams()[0].get('ref');
  const [picked, setPicked] = useState<string | null>(null);
  const [list, setList] = useState<SessionDoc[] | null>(null);
  const [live, setLive] = useState<Record<string, ChatMessage[]>>({});
  const [draft, setDraft] = useState('');
  const [thinking, setThinking] = useState(false);
  const [choosing, setChoosing] = useState(false);
  const [open, setOpen] = useState<number | null>(null);
  const box = useRef<HTMLDivElement>(null);

  const remote = useRemote(mode === 'live' ? 'sessions' : null, () => api.sessions());
  const sessions = list ?? remote.data ?? [];      // what we know locally wins: it is never older
  const ref = picked ?? linked ?? sessions[0]?.ref ?? null;
  const session = sessions.find((s) => s.ref === ref) ?? null;

  const detail = useRemote(ref, () => api.session(ref ?? ''));

  // Turns arrive as they are written: your question, each tool call, then the answer. Kept per
  // session, so switching away and back does not lose what arrived while you were elsewhere.
  useEffect(() => onChat((m) => {
    setLive((all) => {
      const mine = all[m.sessionRef] ?? [];
      return mine.some((x) => x.id === m.id) ? all : { ...all, [m.sessionRef]: [...mine, m] };
    });
    if (m.sessionRef !== ref) return;
    if (m.role === 'assistant' || m.role === 'note') {
      setThinking(false);
      void api.sessions().then(setList).catch(() => undefined);
    }
  }), [onChat, ref]);

  const messages = useMemo(() => {
    const seen = new Set<number>();
    return [...(detail.data?.messages ?? []), ...(live[ref ?? ''] ?? [])]
      .filter((m) => !seen.has(m.id) && seen.add(m.id));
  }, [detail.data, live, ref]);

  useEffect(() => { if (box.current) box.current.scrollTop = box.current.scrollHeight; }, [messages.length, thinking]);

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
    setThinking(true);
    try {
      const { message } = await api.askSession(session.ref, text);
      setLive((all) => {
        const mine = all[session.ref] ?? [];
        return mine.some((x) => x.id === message.id) ? all : { ...all, [session.ref]: [...mine, message] };
      });
    } catch (e) {
      setThinking(false);
      setDraft(text);
      toast.error('Not asked', { description: e instanceof Error ? e.message : 'The API refused.' });
    }
  };

  const keys = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void send(); }
  };

  if (mode !== 'live') {
    return (
      <Page>
        <PageHeader title="Sessions" subtitle="A conversation that can read this project's code, and never loses a turn." />
        <PageBody>
          <Empty icon={<Bot className="size-6" />} title="Sessions need the local API"
            hint="Start it with ./scripts/dev.sh start. Every question, every tool call and every answer is then written to the database as it happens." />
        </PageBody>
      </Page>
    );
  }

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
                    <p className="mt-0.5 truncate text-[11.5px] text-dim">{p.stack ?? p.id}</p>
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
              <div ref={box} className="min-h-0 flex-1 space-y-2.5 overflow-y-auto rounded-xl border border-line bg-surface p-4">
                {messages.length === 0 && <p className="text-[13px] text-soft">Ask it anything about {session.projectName}.</p>}
                {messages.map((m) => m.role === 'you' ? (
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
                      {m.why && <span className="hidden min-w-0 truncate text-[12px] text-soft sm:block">{m.why}</span>}
                    </button>
                    {open === m.id && <Ascii className="mt-1.5 max-h-[280px] overflow-auto text-[12px]">{m.text}</Ascii>}
                  </div>
                ) : m.role === 'note' ? (
                  <Panel key={m.id} className="border-warn/40" eyebrow="Nothing was invented" title="It could not answer">
                    <p className="text-[13px] text-ink-2">{m.text}</p>
                  </Panel>
                ) : (
                  <div key={m.id} className="max-w-[92%]">
                    <p className="rounded-xl rounded-bl-sm bg-surface-2/70 px-3.5 py-2.5 text-[13.5px] leading-relaxed whitespace-pre-wrap text-ink">{m.text}</p>
                    <p className="mt-1 flex items-center gap-2 text-[11.5px] text-dim">
                      <Tag tone="neutral">{m.lane ?? 'model'}</Tag>{m.model}{m.ms ? ` · ${(m.ms / 1000).toFixed(1)}s` : ''}
                    </p>
                  </div>
                ))}
                {thinking && (
                  <p className="flex items-center gap-2 text-[12.5px] text-dim"><Loader2 className="size-3.5 animate-spin" />reading, then answering…</p>
                )}
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
