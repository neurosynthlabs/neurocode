import { api, type ChatMessage, type SessionDoc, type SessionDetail } from '@/lib/api';
import type { SearchHit } from '@/types';

/* Reading a whole session back.

   `GET /sessions/{ref}` answers with the turns after an id, oldest first, up to five hundred of them —
   it is written for catching up on a stream. The screen asked it once, from zero, and so a session
   with more than five hundred rows came back as its FIRST five hundred: the conversation ended in the
   middle of some Tuesday and today's answers were simply missing, with nothing on screen saying so.
   One question with grounding and four tool calls is six rows, so that is about seventy exchanges.

   So the screen keeps asking, from the last id it holds, until a page comes back short. Almost every
   session is one request, exactly as before. Both ceilings below are real and are said out loud when
   they are reached, because a transcript that quietly stops is the bug this replaces. */

/** The page size the detail route uses. A full page means there may be another. */
export const TURN_PAGE = 500;
/** The most turns one screen holds. Past this the oldest are dropped — and the thread says they were. */
export const TURN_CAP = 4000;
/** The most pages one read asks for. Two million characters of conversation is not a screen's problem. */
export const TURN_REQUESTS = 20;

export interface Turns<M> {
  messages: M[];
  /** Older turns exist that are not in `messages`: the cap dropped them. */
  earlier: boolean;
  /** False when the walk stopped at its own ceiling, so even the newest turns may not be here. */
  whole: boolean;
}

/**
 * Every turn of a session, newest ones included, by walking the pages the API gives.
 *
 * `page(after)` is one request: the turns with an id above `after`, oldest first. The walk stops at
 * the first short page — that is the end of the conversation — and keeps only the newest `cap`.
 */
export async function readTurns<M extends { id: number }>(
  page: (after: number) => Promise<M[]>,
  { size = TURN_PAGE, cap = TURN_CAP, requests = TURN_REQUESTS }: { size?: number; cap?: number; requests?: number } = {},
): Promise<Turns<M>> {
  let held: M[] = [];
  let earlier = false;
  let whole = false;
  let after = 0;
  for (let n = 0; n < requests; n++) {
    const got = await page(after);
    held = held.concat(got);
    if (held.length > cap) {
      held = held.slice(held.length - cap);
      earlier = true;
    }
    if (got.length < size) { whole = true; break; }
    after = got[got.length - 1].id;
  }
  return { messages: held, earlier, whole };
}

/** The session and its turns as the screen reads them: the document from the first page, every turn from all of them. */
export async function readSession(ref: string): Promise<SessionDetail & Omit<Turns<ChatMessage>, 'messages'>> {
  let first: SessionDetail | undefined;
  const { messages, earlier, whole } = await readTurns<ChatMessage>(async (after) => {
    const got = await api.session(ref, after);
    first ??= got;
    return got.messages;
  });
  // The first page either answered or raised, so there is always one; this is for the type, not the case.
  if (!first) throw new Error(`${ref} answered with no session.`);
  return { ...first, messages, earlier, whole };
}


/* ── finding one again ────────────────────────────────────────────
   Two searches, each over what is already here. The sessions themselves are matched by the three
   things a person half-remembers about one — its reference, what it was called, and which project it
   was about. What was *said* inside one is matched in the session that holds it, because its turns
   are in this browser only while it is open: the API has no search over `chat_messages` (there is no
   text index on that table), and pretending otherwise by searching only the open session from ⌘K
   would answer "nowhere" for every session but one. */

/** The sessions whose reference, title or project match. Empty search: all of them, in their own order. */
export function matchSessions<S extends { ref: string; title: string; projectName: string }>(
  all: readonly S[], search: string,
): S[] {
  const q = search.trim().toLowerCase();
  const list = [...all];
  if (!q) return list;
  return list.filter((s) => `${s.ref} ${s.title} ${s.projectName}`.toLowerCase().includes(q));
}

/** ⌘K's hits for the sessions: each opens the session it names. */
export function sessionHits(sessions: readonly SessionDoc[]): SearchHit[] {
  return sessions.map((s) => ({
    id: `session-${s.id}`,
    group: 'Sessions',
    title: `${s.ref} · ${s.title}`,
    subtitle: `${s.projectName} · ${s.turns} ${s.turns === 1 ? 'answer' : 'answers'} · ${s.toolCalls} tool calls`,
    to: `/sessions?ref=${encodeURIComponent(s.ref)}`,
    icon: 'History',
    meta: s.waitingOn ? 'WAITING' : undefined,
  }));
}

/**
 * The turns that say something — the words themselves, a tool call's line, or the tool's own name,
 * since what a person remembers is as often a path a tool read as a sentence somebody wrote.
 */
export function turnsSaying<T extends { m: Pick<ChatMessage, 'text' | 'detail' | 'tool'> }>(
  placed: readonly T[], search: string,
): T[] {
  const q = search.trim().toLowerCase();
  const list = [...placed];
  if (!q) return list;
  return list.filter(({ m }) => `${m.text} ${m.detail ?? ''} ${m.tool ?? ''}`.toLowerCase().includes(q));
}
