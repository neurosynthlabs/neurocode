import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import {
  BookOpen, Bot, Brain, ChevronLeft, ChevronRight, Code2, Download, FileInput, FoldVertical, GitFork, ListChecks, Loader2,
  MessageSquarePlus, MoreHorizontal, Paperclip, Pencil, RefreshCw, Wrench,
} from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuGroup, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Ascii, Bar, Dot, Empty, ListRow, Mono, Page, PageBody, PageHeader, Panel, Tag } from '@/components/os';
import { Composer, type Chip } from '@/components/sessions/Composer';
import { PermissionCard } from '@/components/sessions/PermissionCard';
import { api, type ChatAttachment, type ChatMessage, type ChatStream, type SessionDoc } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { useData } from '@/lib/data';
import { fetchModels, type FleetLane } from '@/lib/live/models';
import { download, sessionsApi, workbenchLink, type PermitDecision } from '@/lib/live/sessions';
import { referencesOf } from '@/lib/live/sources';
import { useRemote } from '@/lib/remote';
import { cn } from '@/lib/utils';
import { ago } from '@/pages/code/format';

/* Sessions: a conversation that can read this project's code, and — with a person's say-so — reach the
   web and MCP tools. You ask, it reaches for a tool, the tool runs here, and every turn is written down
   before anything else happens. A call no rule allows waits on a card in the transcript until you
   answer it. An edited question or a regenerated answer is a new turn; the old one stays, behind a
   "1/2 · 2/2" switch, and only the current line is sent to the model. */

const TOOL_LABEL: Record<string, string> = {
  search_code: 'searched the code', read_file: 'read a file', list_files: 'listed files', find: 'searched the index',
  impact: 'traced the blast radius', search_memory: 'searched memory', project_summary: 'looked at the project',
  web_fetch: 'read a web page', web_search: 'searched the web', mcp: 'called an MCP tool', context: 'you attached',
  command: 'ran a command', grounding: 'grounded the question', load_skill: 'loaded a skill',
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
const failed = (e: unknown) => (e instanceof Error ? e.message : 'The API refused.');
/** A turn that ends the wait for an answer: the answer, a note saying why there is none, or a card waiting on you. */
const ends = (m: ChatMessage) => m.role === 'assistant' || (m.role === 'note' && !m.detail) || m.permission?.state === 'pending';

/** One place in the transcript: a turn, and — where an edit or a regeneration replaced it — its versions. */
interface Placed { m: ChatMessage; versions?: { key: number; at: number; of: number; label: string } }

/**
 * The line the person reads. Turns an edit or a regeneration replaced point at the question that replaced
 * them; a question that replaced one is a switch over every version of that point. Choosing an older
 * version shows that version's whole line from there on, as it was — read only.
 */
function arrange(all: ChatMessage[], chosen: Record<number, number>): Placed[] {
  const replaced = new Map<number, ChatMessage[]>();
  for (const m of all) if (m.supersededBy) replaced.set(m.supersededBy, [...(replaced.get(m.supersededBy) ?? []), m]);
  const before = (m: ChatMessage) => {
    const first = replaced.get(m.id)?.[0];
    return first && first.role === 'you' ? first : null;
  };
  let line = all.filter((m) => !m.supersededBy);
  const out: Placed[] = [];
  for (let i = 0; i < line.length; i++) {
    const r = line[i];
    const older = r.role === 'you' ? before(r) : null;
    if (!older) { out.push({ m: r }); continue; }
    const chain: ChatMessage[] = [r];
    for (let c: ChatMessage | null = older; c; c = before(c)) chain.unshift(c);
    const at = Math.min(chosen[r.id] ?? chain.length - 1, chain.length - 1);
    const shown = chain[at];
    if (shown !== r && shown.supersededBy) line = [...line.slice(0, i), ...(replaced.get(shown.supersededBy) ?? [])];
    const same = chain.every((c) => c.text === chain[0].text);
    out.push({ m: line[i], versions: { key: r.id, at, of: chain.length, label: same ? 'Answer' : 'Question' } });
  }
  return out;
}

/** Files each answer rests on: what the person attached and what its tools read, for "Open in Workbench". */
function citedFiles(placed: Placed[]): Map<number, { path: string; line?: number }[]> {
  const out = new Map<number, { path: string; line?: number }[]>();
  let files: { path: string; line?: number }[] = [];
  const add = (path: unknown, line?: unknown) => {
    if (typeof path !== 'string' || !path || files.some((f) => f.path === path)) return;
    files.push({ path, line: typeof line === 'number' ? line : undefined });
  };
  for (const { m } of placed) {
    if (m.role === 'you') {
      files = [];
      for (const a of m.attachments ?? []) {
        if (a.kind === 'file') add(a.ref);
        if (a.kind === 'symbol') add(a.path, a.line);
      }
    } else if (m.role === 'tool' && m.ok && (m.tool === 'read_file' || m.tool === 'impact')) {
      add(m.arguments?.path, m.arguments?.start);
    } else if (m.role === 'assistant') {
      out.set(m.id, files);
    }
  }
  return out;
}

export default function Sessions() {
  const { projects, onChat } = useData();
  const { can } = useAuth();
  const navigate = useNavigate();
  const linked = useSearchParams()[0].get('ref');
  const [picked, setPicked] = useState<string | null>(null);
  const [list, setList] = useState<SessionDoc[] | null>(null);
  const [live, setLive] = useState<Record<string, ChatMessage[]>>({});
  const [pending, setPending] = useState<Record<string, Pending>>({});
  // Which sessions are waiting on an answer. Kept per session, not as one flag, so switching to
  // another session while one is answering neither hides that wait nor locks the other's Ask.
  const [waiting, setWaiting] = useState<ReadonlySet<string>>(() => new Set());
  const [choosing, setChoosing] = useState<'new' | 'import' | null>(null);
  const [compacting, setCompacting] = useState(false);
  const [open, setOpen] = useState<number | null>(null);
  const [chosen, setChosen] = useState<Record<number, number>>({});
  const [editing, setEditing] = useState<{ id: number; text: string } | null>(null);
  const [acting, setActing] = useState<string | null>(null);
  const [lanes, setLanes] = useState<FleetLane[] | null>(null);
  const [importing, setImporting] = useState<unknown>(null);
  const box = useRef<HTMLDivElement>(null);
  const importFile = useRef<HTMLInputElement>(null);

  const remote = useRemote('sessions', () => api.sessions());
  const sessions = list ?? remote.data ?? [];      // what we know locally wins: it is never older
  const ref = picked ?? linked ?? sessions[0]?.ref ?? null;
  const session = sessions.find((s) => s.ref === ref) ?? null;
  // The projects the session's project reads from: their pieces reach the model labelled as references.
  const home = session ? projects.find((p) => p.id === session.projectId) : undefined;
  const readsFrom = home ? referencesOf(home).map((id) => projects.find((p) => p.id === id)?.name ?? id) : [];
  const thinking = ref !== null && waiting.has(ref);
  const wait = (sessionRef: string, on: boolean) => setWaiting((all) => toggled(all, sessionRef, on));
  const refreshList = () => api.sessions().then(setList, (e: unknown) => console.error('[NeuroCode] GET /sessions failed:', e));
  const replace = (doc: SessionDoc) => setList((all) => {
    const now = all ?? sessions;
    return now.some((s) => s.ref === doc.ref) ? now.map((s) => (s.ref === doc.ref ? doc : s)) : [doc, ...now];
  });

  const detail = useRemote(ref, () => api.session(ref ?? ''));
  const reloadDetail = useRef(detail.reload);
  useEffect(() => { reloadDetail.current = detail.reload; }, [detail.reload]);

  // Turns arrive as they are written: your question, each tool call, then the answer. Kept per
  // session, so switching away and back does not lose what arrived while you were elsewhere. A turn
  // that changed in place (an answered permission card) arrives again and replaces the one held.
  useEffect(() => onChat((e) => {
    if (e.stream) {
      const s = e.stream;
      setPending((all) => ({ ...all, [e.sessionRef]: appended(all[e.sessionRef], s) }));
      return;
    }
    const m = e;
    setLive((all) => {
      const mine = all[m.sessionRef] ?? [];
      const at = mine.findIndex((x) => x.id === m.id);
      return { ...all, [m.sessionRef]: at < 0 ? [...mine, m] : mine.map((x, i) => (i === at ? m : x)) };
    });
    if (m.role !== 'you') setPending((all) => {
      if (!(m.sessionRef in all)) return all;
      const next = { ...all };
      delete next[m.sessionRef];
      return next;
    });
    // A summary marks older turns folded, and an edit marks older turns replaced: read them again.
    if (m.role === 'summary' || (m.role === 'you' && (m.edited || m.regenerated))) reloadDetail.current();
    // An answer ends that session's wait whichever session is on screen now.
    if (ends(m)) {
      setWaiting((all) => toggled(all, m.sessionRef, false));
      void refreshList();
    }
  }), [onChat]);

  const messages = useMemo(() => {
    const newer = new Map((live[ref ?? ''] ?? []).map((m) => [m.id, m]));
    const held = (detail.data?.messages ?? []).map((m) => newer.get(m.id) ?? m);
    const seen = new Set(held.map((m) => m.id));
    return [...held, ...[...newer.values()].filter((m) => !seen.has(m.id))].sort((a, b) => a.id - b.id);
  }, [detail.data, live, ref]);
  const placed = useMemo(() => arrange(messages, chosen), [messages, chosen]);
  const cited = useMemo(() => citedFiles(placed), [placed]);
  const card = messages.find((m) => m.permission?.state === 'pending' && !m.supersededBy) ?? null;
  const writing = ref ? pending[ref] : undefined;
  const lastOnLine = [...messages].reverse().find((m) => !m.supersededBy) ?? null;

  useEffect(() => { if (box.current) box.current.scrollTop = box.current.scrollHeight; }, [messages.length, thinking, writing?.answer.length, writing?.reasoning.length]);

  const begin = async (projectId: string, name: string) => {
    setChoosing(null);
    try {
      const made = await api.newSession(projectId);
      setList((all) => [made, ...(all ?? [])]);
      setPicked(made.ref);
      toast.success(`${made.ref} started`, { description: `Reading ${name}.` });
    } catch (e) {
      toast.error('Session not started', { description: failed(e) });
    }
  };

  const send = async (text: string, chips: Chip[]): Promise<boolean> => {
    if (!session) return false;
    wait(session.ref, true);
    try {
      const { message } = await api.askSession(session.ref, text, chips.map(({ kind, ref: r, name }) => ({ kind, ref: r, name })));
      setLive((all) => {
        const mine = all[session.ref] ?? [];
        return mine.some((x) => x.id === message.id) ? all : { ...all, [session.ref]: [...mine, message] };
      });
      return true;
    } catch (e) {
      wait(session.ref, false);
      toast.error('Not asked', { description: failed(e) });
      return false;
    }
  };

  /** A change that makes the session answer again: it waits, like a question does. */
  const answerAgain = async (what: string, run: () => Promise<{ session: SessionDoc } | SessionDoc>) => {
    if (!session) return;
    setActing(what);
    wait(session.ref, true);
    try {
      const out = await run();
      replace('session' in out ? out.session : out);
      detail.reload();
    } catch (e) {
      wait(session.ref, false);
      toast.error(`Not ${what}`, { description: failed(e) });
    } finally {
      setActing(null);
    }
  };

  const permit = (m: ChatMessage) => async (decision: PermitDecision) => {
    if (!session) return;
    await answerAgain(decision === 'refuse' ? 'refused' : 'allowed', () => sessionsApi.permit(session.ref, m.id, decision));
  };

  const saveEdit = async () => {
    if (!session || !editing?.text.trim()) return;
    const { id, text } = editing;
    setEditing(null);
    await answerAgain('edited', () => sessionsApi.edit(session.ref, id, text.trim()));
  };

  const regenerate = (m: ChatMessage, lane: string | null) => {
    if (!session) return;
    void answerAgain('regenerated', () => sessionsApi.regenerate(session.ref, m.id, lane));
  };

  const fork = async (at: number) => {
    if (!session) return;
    setActing('fork');
    try {
      const made = await sessionsApi.fork(session.ref, at);
      setList((all) => [made, ...(all ?? sessions)]);
      setPicked(made.ref);
      toast.success(`${made.ref} forked`, { description: `From ${session.ref}. The original is unchanged.` });
    } catch (e) {
      toast.error('Not forked', { description: failed(e) });
    } finally {
      setActing(null);
    }
  };

  const exportAs = async (format: 'md' | 'json') => {
    if (!session) return;
    try {
      download(await sessionsApi.exportDoc(session.ref, format));
    } catch (e) {
      toast.error('Not exported', { description: failed(e) });
    }
  };

  const toPlan = async () => {
    if (!session) return;
    setActing('plan');
    try {
      const plan = await sessionsApi.toPlan(session.ref);
      toast.success(`${plan.ref} compiled`, { description: 'From the last question and what its answer read.', action: { label: 'Open', onClick: () => navigate(`/plans?ref=${encodeURIComponent(plan.ref)}`) } });
    } catch (e) {
      toast.error('No plan was made', { description: failed(e) });
    } finally {
      setActing(null);
    }
  };

  const pickImport = async (file: File) => {
    try {
      setImporting(JSON.parse(await file.text()) as unknown);
      setChoosing('import');
    } catch {
      toast.error(`${file.name} is not JSON`, { description: 'Import reads a session exported as JSON from NeuroCode.' });
    }
  };

  const doImport = async (projectId: string) => {
    setChoosing(null);
    try {
      const made = await sessionsApi.importDoc(projectId, importing);
      setList((all) => [made, ...(all ?? sessions)]);
      setPicked(made.ref);
      toast.success(`${made.ref} imported`, { description: `${made.turns} answers, on ${made.projectName}.` });
    } catch (e) {
      toast.error('Not imported', { description: failed(e) });
    } finally {
      setImporting(null);
    }
  };

  const compact = async () => {
    if (!session || compacting) return;
    setCompacting(true);
    try {
      const { summary, session: after } = await api.compactSession(session.ref);
      replace(after);
      detail.reload();
      toast.success(`${summary.folded?.turns ?? 0} turns folded`, { description: 'The model is sent the summary instead. Every turn is still here to read.' });
    } catch (e) {
      toast.error('Nothing was folded', { description: failed(e) });
    } finally {
      setCompacting(false);
    }
  };

  const loadLanes = () => {
    if (lanes === null) fetchModels().then((r) => setLanes(r.lanes), (e: unknown) => console.error('[NeuroCode] GET /models failed:', e));
  };

  const mayChat = can('sessions:chat');
  const busy = thinking || acting !== null || card !== null;

  const turn = ({ m, versions }: Placed) => {
    const old = !!m.supersededBy;
    const actions = !old && !m.compacted && mayChat && !busy;
    const switcher = versions && (
      <span className="inline-flex items-center gap-0.5 text-[11.5px] text-dim">
        <button type="button" aria-label="Earlier version" disabled={versions.at === 0}
          onClick={() => setChosen((c) => ({ ...c, [versions.key]: versions.at - 1 }))}
          className="rounded p-0.5 hover:bg-surface-2 hover:text-ink disabled:opacity-40"><ChevronLeft className="size-3.5" /></button>
        <span className="tnum">{versions.label} {versions.at + 1}/{versions.of}</span>
        <button type="button" aria-label="Later version" disabled={versions.at === versions.of - 1}
          onClick={() => setChosen((c) => ({ ...c, [versions.key]: versions.at + 1 }))}
          className="rounded p-0.5 hover:bg-surface-2 hover:text-ink disabled:opacity-40"><ChevronRight className="size-3.5" /></button>
      </span>
    );
    if (m.role === 'you') return (
      <div key={m.id} className="group flex flex-col items-end gap-1">
        {editing?.id === m.id ? (
          <div className="w-full max-w-[85%] rounded-xl border border-brand/40 bg-surface p-2">
            <textarea value={editing.text} onChange={(e) => setEditing({ id: m.id, text: e.target.value })} rows={3} autoFocus
              onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void saveEdit(); } if (e.key === 'Escape') setEditing(null); }}
              className="w-full resize-none bg-transparent px-1.5 py-1 text-[13.5px] text-ink outline-none" />
            <div className="flex justify-end gap-2 pt-1">
              <Button size="xs" variant="ghost" onClick={() => setEditing(null)}>Cancel</Button>
              <Button size="xs" disabled={!editing.text.trim()} onClick={() => void saveEdit()}>Ask this instead</Button>
            </div>
            <p className="px-1.5 pt-1 text-[11.5px] text-dim">The question and what followed it stay, as an earlier version.</p>
          </div>
        ) : (
          <p className={cn('max-w-[85%] rounded-xl rounded-br-sm bg-brand/12 px-3.5 py-2 text-[13.5px] whitespace-pre-wrap text-ink', old && 'opacity-75')}>{m.text}</p>
        )}
        {(m.attachments?.length ?? 0) > 0 && <Chips session={session} items={m.attachments ?? []} />}
        <div className="flex items-center gap-1.5">
          {m.regenerated && m.lane && <span className="text-[11.5px] text-dim">asked again on {m.lane}</span>}
          {switcher}
          {actions && editing?.id !== m.id && (
            <span className="flex items-center gap-0.5 opacity-100 transition-opacity sm:opacity-0 sm:group-hover:opacity-100 sm:focus-within:opacity-100">
              <Button size="icon-xs" variant="ghost" aria-label="Edit this question" title="Edit and ask again" onClick={() => setEditing({ id: m.id, text: m.text })}><Pencil /></Button>
              <Button size="icon-xs" variant="ghost" aria-label="Fork from here" title="Fork from here" onClick={() => void fork(m.id)}><GitFork /></Button>
            </span>
          )}
        </div>
      </div>
    );
    if (m.role === 'tool' && m.permission) return (
      <PermissionCard key={m.id} message={m} canAnswer={mayChat && !old} onAnswer={permit(m)} />
    );
    if (m.role === 'tool') {
      const path = typeof m.arguments?.path === 'string' ? m.arguments.path : null;
      const start = typeof m.arguments?.start === 'number' ? m.arguments.start : null;
      return (
        <div key={m.id}>
          <button onClick={() => setOpen(open === m.id ? null : m.id)}
            className="flex w-full items-center gap-2 rounded-lg border border-line/70 bg-surface-2/50 px-3 py-1.5 text-left transition-colors hover:bg-surface-2">
            {m.tool === 'context' ? <Paperclip className="size-3.5 shrink-0 text-dim" /> : <Wrench className={cn('size-3.5 shrink-0', m.ok === false ? 'text-warn' : 'text-dim')} />}
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
              {session && path && m.ok && (m.tool === 'read_file' || m.tool === 'impact') && (
                <Link to={workbenchLink(session.projectId, path, start)} className="inline-flex items-center gap-1.5 text-[12px] text-brand hover:underline">
                  <Code2 className="size-3.5" />Open {path} in Workbench
                </Link>
              )}
            </div>
          )}
        </div>
      );
    }
    if (m.role === 'note') return (
      <div key={m.id} className="space-y-1.5">
        {m.reasoning && <Reasoning text={m.reasoning} label="What it had reasoned" />}
        {m.detail?.startsWith('image') ? (
          <p className="rounded-lg border border-warn/40 px-3 py-2 text-[12.5px] text-ink-2">{m.text}</p>
        ) : (
          <Panel className="border-warn/40" eyebrow="Nothing was invented" title="It could not answer">
            <p className="text-[13px] text-ink-2">{m.text}</p>
          </Panel>
        )}
        {actions && !m.detail && (
          <Button size="xs" variant="outline" onClick={() => regenerate(m, null)}><RefreshCw className="size-3" />Try again</Button>
        )}
      </div>
    );
    if (m.role === 'summary') return (
      <Panel key={m.id} className="border-brand/30" eyebrow={`Written by ${m.lane ?? 'a model'}${m.model ? ` · ${m.model}` : ''}`}
        title={<span className="flex items-center gap-2"><FoldVertical className="size-4 text-brand" />Summary of {m.folded?.turns ?? 0} earlier turns</span>}>
        <p className="text-[13px] leading-relaxed whitespace-pre-wrap text-ink-2">{m.text}</p>
        <p className="mt-2 text-[12px] text-dim">The model is sent this summary instead of those turns. They are kept above, folded, to read.</p>
      </Panel>
    );
    const files = cited.get(m.id) ?? [];
    return (
      <div key={m.id} className="group max-w-[92%] space-y-1.5">
        {m.reasoning && <Reasoning text={m.reasoning} label={thoughtLabel(m)} />}
        <p className={cn('rounded-xl rounded-bl-sm bg-surface-2/70 px-3.5 py-2.5 text-[13.5px] leading-relaxed whitespace-pre-wrap text-ink', old && 'opacity-75')}>{m.text}</p>
        {session && files.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-[11.5px] text-dim">Read</span>
            {files.slice(0, 8).map((f) => (
              <Link key={f.path} to={workbenchLink(session.projectId, f.path, f.line)} title="Open in Workbench"
                className="inline-flex max-w-[240px] items-center gap-1 rounded-md bg-surface-2 px-1.5 py-0.5 font-mono text-[11.5px] text-ink-2 hover:text-brand">
                <Code2 className="size-3 shrink-0" /><span className="truncate">{f.path}{f.line ? `:${f.line}` : ''}</span>
              </Link>
            ))}
          </div>
        )}
        <div className="flex flex-wrap items-center gap-2 text-[11.5px] text-dim">
          {m.detail === 'stopped' && <Tag tone="warn">stopped</Tag>}
          {m.lane && <Tag tone="neutral">{m.lane}</Tag>}{m.model}{m.ms ? ` · ${seconds(m.ms)}` : ''}
          {actions && (
            <span className="flex items-center gap-0.5 opacity-100 transition-opacity sm:opacity-0 sm:group-hover:opacity-100 sm:focus-within:opacity-100">
              <DropdownMenu onOpenChange={(o) => { if (o) loadLanes(); }}>
                <DropdownMenuTrigger render={<Button size="icon-xs" variant="ghost" aria-label="Regenerate" title="Regenerate" />}>
                  <RefreshCw />
                </DropdownMenuTrigger>
                <DropdownMenuContent align="start" className="w-60">
                  <DropdownMenuItem onClick={() => regenerate(m, null)}>Regenerate</DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <DropdownMenuGroup>
                    <DropdownMenuLabel>On another lane</DropdownMenuLabel>
                    {lanes === null ? (
                      <DropdownMenuItem disabled><Loader2 className="size-3.5 animate-spin" />Reading the lanes…</DropdownMenuItem>
                    ) : lanes.filter((l) => l.ready && l.id !== m.lane).length === 0 ? (
                      <DropdownMenuItem disabled>No other lane can answer now</DropdownMenuItem>
                    ) : lanes.filter((l) => l.ready && l.id !== m.lane).map((l) => (
                      <DropdownMenuItem key={l.id} onClick={() => regenerate(m, l.id)}>
                        <span className="truncate">{l.label}</span><span className="ml-auto truncate font-mono text-[11px] text-dim">{l.model}</span>
                      </DropdownMenuItem>
                    ))}
                  </DropdownMenuGroup>
                </DropdownMenuContent>
              </DropdownMenu>
              <Button size="icon-xs" variant="ghost" aria-label="Fork from here" title="Fork from here" onClick={() => void fork(m.id)}><GitFork /></Button>
            </span>
          )}
          {switcher}
        </div>
      </div>
    );
  };

  const picker = choosing && (
    <>
      {choosing === 'import' && <p className="border-b border-line/60 px-4 py-2 text-[12px] text-dim">Import into which project?</p>}
      {projects.length === 0 && <p className="px-4 py-3 text-[12.5px] text-dim">No projects yet. Onboard one on Projects first.</p>}
      {projects.map((p) => (
        <ListRow key={p.id} onClick={() => void (choosing === 'import' ? doImport(p.id) : begin(p.id, p.name))}>
          <p className="truncate text-[13.5px] text-ink">{p.name}</p>
          <p className="mt-0.5 truncate text-[11.5px] text-dim">{p.stack.length ? p.stack.join(' · ') : p.id}</p>
        </ListRow>
      ))}
    </>
  );

  return (
    <Page>
      <PageHeader
        title="Sessions"
        subtitle="You ask; it reads the code with tools and answers from what it found. The web and MCP tools ask you first. Every turn is written down before anything else happens, so nothing is lost on a reload."
        actions={mayChat && (
          <div className="flex items-center gap-2">
            <input ref={importFile} type="file" accept="application/json,.json" hidden
              onChange={(e) => { const f = e.target.files?.[0]; if (f) void pickImport(f); e.target.value = ''; }} />
            <Button size="sm" variant="outline" onClick={() => importFile.current?.click()} title="Import a session exported as JSON">
              <FileInput className="size-3.5" />Import
            </Button>
            <Button size="sm" onClick={() => setChoosing((v) => (v === 'new' ? null : 'new'))}><MessageSquarePlus className="size-3.5" />New session</Button>
          </div>
        )}
      />
      <PageBody className="flex h-full flex-col gap-3 p-0">
        <div className="flex min-h-0 flex-1 flex-col gap-3 px-4 pt-4 pb-4 sm:px-6 md:flex-row">
          <div className="flex w-full shrink-0 max-h-[32vh] md:max-h-none md:w-[280px] flex-col overflow-y-auto rounded-xl border border-line bg-surface">
            <div className="flex shrink-0 items-center border-b border-line px-4 py-2.5 text-[12px] font-medium text-dim">
              <span className="flex-1">{choosing ? 'Pick a project' : `${sessions.length} sessions`}</span>
              {choosing && <button type="button" className="text-[12px] text-dim hover:text-ink" onClick={() => { setChoosing(null); setImporting(null); }}>Cancel</button>}
            </div>
            <div className="no-scrollbar min-h-0 flex-1 overflow-y-auto">
              {choosing ? picker : sessions.map((s) => (
                <ListRow key={s.ref} active={s.ref === ref} onClick={() => setPicked(s.ref)}>
                  <div className="flex items-center gap-2">
                    <Dot state={s.waitingOn ? 'waiting' : s.status === 'thinking' ? 'running' : 'idle'} pulse={s.status === 'thinking'} />
                    <Mono>{s.ref}</Mono>
                    {s.parentId && <GitFork className="size-3 text-dim" aria-label="Forked" />}
                    <span className="ml-auto text-[11.5px] text-dim">{ago(s.lastAt)}</span>
                  </div>
                  <p className="mt-1 truncate text-[13px] text-ink">{s.title}</p>
                  <p className="mt-1 truncate text-[11.5px] text-dim">
                    {s.waitingOn ? `waiting for you · ${s.waitingOn.tool}` : `${s.projectName} · ${s.toolCalls} tool calls`}
                  </p>
                </ListRow>
              ))}
            </div>
          </div>

          {!session ? (
            <div className="flex min-w-0 flex-1 items-center justify-center rounded-xl border border-line bg-surface">
              <Empty icon={<Bot className="size-6" />} title="No session yet"
                hint="Start one on a project, or import one exported as JSON. It can search the code, read files, trace what depends on what, search memory — and, when you allow it, read the web and call MCP tools." />
            </div>
          ) : (
            <div className="flex min-w-0 flex-1 flex-col gap-3">
              <Context session={session} files={detail.data?.instructions ?? null} busy={compacting || thinking}
                canCompact={mayChat} onCompact={() => void compact()}
                actions={(
                  <DropdownMenu>
                    <DropdownMenuTrigger render={<Button size="xs" variant="outline" aria-label="More for this session" />}>
                      {acting === 'plan' || acting === 'fork' ? <Loader2 className="size-3 animate-spin" /> : <MoreHorizontal className="size-3" />}More
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end" className="w-60">
                      {can('plans:compile') && (
                        <DropdownMenuItem disabled={busy || !messages.some((x) => x.role === 'assistant' && !x.supersededBy)} onClick={() => void toPlan()}>
                          <ListChecks className="size-3.5" />Make this a plan
                        </DropdownMenuItem>
                      )}
                      {mayChat && lastOnLine && (
                        <DropdownMenuItem disabled={busy} onClick={() => void fork(lastOnLine.id)}><GitFork className="size-3.5" />Fork this session</DropdownMenuItem>
                      )}
                      <DropdownMenuSeparator />
                      <DropdownMenuItem onClick={() => void exportAs('md')}><Download className="size-3.5" />Export as Markdown</DropdownMenuItem>
                      <DropdownMenuItem onClick={() => void exportAs('json')}><Download className="size-3.5" />Export as JSON</DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                )} />
              {(detail.data?.parentRef || session.grants.length > 0 || readsFrom.length > 0) && (
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-1 text-[12px] text-dim">
                  {readsFrom.length > 0 && (
                    <span className="inline-flex items-center gap-1 rounded-full border border-line px-2 py-px text-ink-2"
                      title={`Read only, labelled as references: ${readsFrom.join(', ')}. Their files are read as <project id>:<path>.`}>
                      <BookOpen className="size-3" />Reads {readsFrom.length} referenced {readsFrom.length === 1 ? 'project' : 'projects'}
                    </span>
                  )}
                  {detail.data?.parentRef && (
                    <button type="button" onClick={() => setPicked(detail.data?.parentRef ?? null)} className="inline-flex items-center gap-1 hover:text-ink">
                      <GitFork className="size-3" />Forked from <Mono>{detail.data.parentRef}</Mono>
                    </button>
                  )}
                  {session.grants.length > 0 && (
                    <span className="truncate" title={session.grants.map((g) => `${g.tool}: ${g.covers ?? g.subject} (by ${g.by})`).join('\n')}>
                      Allowed for this session: {session.grants.map((g) => g.covers ?? `${g.tool} ${g.subject}`).join(' · ')}
                    </span>
                  )}
                </div>
              )}
              <div ref={box} className="min-h-0 flex-1 space-y-2.5 overflow-y-auto rounded-xl border border-line bg-surface p-4">
                {detail.error && messages.length === 0 && (
                  <div className="flex items-center gap-3 text-[13px] text-ink-2">
                    <span className="flex-1">{detail.error}</span>
                    <Button size="xs" variant="outline" onClick={detail.reload}>Try again</Button>
                  </div>
                )}
                {detail.loading && messages.length === 0 && <p className="flex items-center gap-2 text-[12.5px] text-dim"><Loader2 className="size-3.5 animate-spin" />Reading the session…</p>}
                {!detail.loading && !detail.error && messages.length === 0 && <p className="text-[13px] text-soft">Ask it anything about {session.projectName}. Type @ to attach a file, a symbol, a fact or a plan.</p>}
                {placed.some((p) => p.m.supersededBy) && (
                  <p className="text-[12px] text-dim">You are reading an earlier version. The model is sent only the latest one.</p>
                )}
                {grouped(placed).map((g) => g.folded
                  ? <Folded key={`folded-${g.turns[0].m.id}`} count={g.turns.length}>{g.turns.map(turn)}</Folded>
                  : turn(g.turns[0]))}
                {thinking && !card && (writing && (writing.answer || writing.reasoning) ? (
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

              <Composer session={session} canAsk={mayChat} thinking={thinking} waiting={card !== null}
                onSend={send} onStop={() => void api.cancelSession(session.ref).catch(() => undefined)} />
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

/** What was attached to a question, as it was sent: pictures open, the rest name what the model was handed. */
function Chips({ session, items }: { session: SessionDoc | null; items: ChatAttachment[] }) {
  return (
    <div className="flex max-w-[85%] flex-wrap justify-end gap-1.5">
      {items.map((a) => {
        const label = (
          <>
            {a.kind === 'upload' ? <Paperclip className="size-3 shrink-0" /> : <Code2 className="size-3 shrink-0" />}
            <span className="truncate">{a.name || a.ref}</span>
            {a.cut && <span className="text-warn">· cut</span>}
            {a.missing && <span className="text-dim">· not in the export</span>}
          </>
        );
        const cls = 'inline-flex max-w-[220px] items-center gap-1 rounded-md bg-surface-2 px-1.5 py-0.5 text-[11.5px] text-ink-2';
        const title = a.image ? 'A picture: sent only to a lane that reads images' : a.chars !== undefined ? `${a.chars.toLocaleString()} characters handed to the model` : a.kind;
        if (session && a.kind === 'upload' && !a.missing) {
          return <a key={`${a.kind}:${a.ref}`} href={sessionsApi.fileUrl(session.ref, a.ref)} target="_blank" rel="noreferrer" title={title} className={cn(cls, 'hover:text-brand')}>{label}</a>;
        }
        if (session && (a.kind === 'file' || (a.kind === 'symbol' && a.path))) {
          return <Link key={`${a.kind}:${a.ref}`} to={workbenchLink(session.projectId, a.kind === 'file' ? a.ref : a.path ?? '', a.line)} title="Open in Workbench" className={cn(cls, 'font-mono hover:text-brand')}>{label}</Link>;
        }
        return <span key={`${a.kind}:${a.ref}`} title={title} className={cls}>{label}</span>;
      })}
    </div>
  );
}

/** Runs of folded turns, each run shown as one closed row, in the order they were said. */
function grouped(placed: Placed[]): { folded: boolean; turns: Placed[] }[] {
  const out: { folded: boolean; turns: Placed[] }[] = [];
  for (const p of placed) {
    const last = out[out.length - 1];
    if (p.m.compacted && last?.folded) last.turns.push(p);
    else out.push({ folded: !!p.m.compacted, turns: [p] });
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
function Context({ session, files, busy, canCompact, onCompact, actions }: {
  session: SessionDoc; files: { path: string; bytes: number }[] | null; busy: boolean; canCompact: boolean; onCompact: () => void;
  actions: ReactNode;
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
      <div className="flex items-center gap-1.5">
        {canCompact && (
          <Button size="xs" variant="outline" disabled={busy} onClick={onCompact}
            title="Fold the older turns into one summary a model writes. Every turn stays here to read.">
            {busy ? <Loader2 className="size-3 animate-spin" /> : <FoldVertical className="size-3" />}Compact
          </Button>
        )}
        {actions}
      </div>
    </div>
  );
}
