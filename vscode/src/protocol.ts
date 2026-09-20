/* What the extension knows about NeuroCode, with no editor and no network in it.
 *
 * Everything in this file is a plain function of its arguments, for one reason: it is the part that
 * is wrong quietly. An address resolved one way instead of another signs you in to nothing; a
 * relative path computed from the wrong root attaches a file the server has never heard of; a
 * server-sent event split across two TCP reads and parsed as two events loses a run's state and
 * nobody notices until the list is stale. None of that can be seen by opening the editor and
 * looking. So it is here, and `test/protocol.test.mjs` reads it back.
 *
 * The rest of the extension — extension.ts, runs.ts — is the editor's side of the same ideas, and
 * holds no rules of its own.
 */

/** A NeuroCode server: where its API answers, and where its web app is (when that is known). */
export interface Address {
  api: string;
  /** Null when the person gave the API's own address: an API says nothing about where a web app is. */
  web: string | null;
}

export function withoutTrailingSlash(address: string): string {
  return address.replace(/\/+$/, '');
}

/**
 * The places to look for an API, in order, given one address a person knows — the same two `nc
 * login` tries, and in the same order, so the two clients cannot disagree about what an address
 * means. The web app serves its API under `/api`; an API reached directly answers at its root.
 */
export function candidates(address: string): Address[] {
  let base = withoutTrailingSlash(address.trim());
  if (!base) return [];
  if (!base.includes('://')) base = `http://${base}`;
  return [
    { api: `${base}/api`, web: base },
    { api: base, web: null },
  ];
}

/** Whether a body is really NeuroCode's `/health` and not some other server's 200. */
export function isHealth(body: unknown): boolean {
  return typeof body === 'object' && body !== null && 'ok' in body && 'counts' in body;
}

/** A personal access token, as Settings → Access tokens makes them. Checked before it is stored, so
 *  a pasted password fails here with a sentence rather than as a 401 on every command afterwards. */
export function looksLikeToken(token: string): boolean {
  return /^nc_pat_[A-Za-z0-9_-]{8,}$/.test(token.trim());
}

/* ── server-sent events ────────────────────────────────────────── */

export interface StreamEvent {
  /** `activity`, `change`, `run`, `chat`, `routine`, `review`, `resync` or `reset`. */
  kind: string;
  data: unknown;
}

/**
 * The events in what has arrived so far, and what is left over.
 *
 * A stream is bytes, not events: one read can carry three events and half of a fourth, and the next
 * can carry the rest of it. So this is given everything not yet parsed and hands back what it could
 * not finish, to be prepended to the next read. A `data:` line that is not JSON is passed on as
 * text rather than ending the stream, which is what the CLI's reader does too.
 */
export function readEvents(buffer: string): { events: StreamEvent[]; rest: string } {
  const normalised = buffer.replace(/\r\n/g, '\n');
  const cut = normalised.lastIndexOf('\n\n');
  if (cut === -1) return { events: [], rest: normalised };
  const events: StreamEvent[] = [];
  for (const block of normalised.slice(0, cut).split('\n\n')) {
    let kind = 'message';
    const data: string[] = [];
    for (const line of block.split('\n')) {
      if (line === '' || line.startsWith(':')) continue;      // blank, or a keep-alive comment
      const at = line.indexOf(':');
      const field = at === -1 ? line : line.slice(0, at);
      let value = at === -1 ? '' : line.slice(at + 1);
      if (value.startsWith(' ')) value = value.slice(1);
      if (field === 'event') kind = value;
      else if (field === 'data') data.push(value);
    }
    if (!data.length) continue;
    const raw = data.join('\n');
    try {
      events.push({ kind, data: JSON.parse(raw) });
    } catch {
      events.push({ kind, data: raw });
    }
  }
  return { events, rest: normalised.slice(cut + 2) };
}

/* ── runs ──────────────────────────────────────────────────────── */

/** The fields of a run this view uses. The API sends a great deal more; none of it is needed here. */
export interface Run {
  ref: string;
  status: string;
  projectId?: string;
  projectName?: string;
  requirement?: string;
  branch?: string;
  startedAt?: string;
}

/** What a run's state means, in the words the web app uses for it. */
export const RUN_STATES: Record<string, string> = {
  queued: 'queued',
  running: 'working',
  waiting: 'waiting on you',
  review: 'being reviewed',
  done: 'done',
  merged: 'merged',
  failed: 'failed',
  cancelled: 'stopped',
  discarded: 'discarded',
  interrupted: 'interrupted',
};

/** Whether this run is stopped at a person — the only state the editor can do nothing about. */
export const needsAPerson = (run: Run): boolean => run.status === 'waiting';

export function runDescription(run: Run): string {
  const state = RUN_STATES[run.status] ?? run.status;
  return run.projectName ? `${state} · ${run.projectName}` : state;
}

/** One line for a run: its reference and what it was asked to do, cut to something a tree can show. */
export function runLabel(run: Run, width = 72): string {
  const said = (run.requirement ?? '').replace(/\s+/g, ' ').trim();
  if (!said) return run.ref;
  const room = Math.max(8, width - run.ref.length - 3);
  return `${run.ref} · ${said.length > room ? `${said.slice(0, room - 1)}…` : said}`;
}

/** Newest first, but anything waiting on a person first of all: that is the list's whole purpose. */
export function order(runs: Run[]): Run[] {
  return [...runs].sort((a, b) => {
    if (needsAPerson(a) !== needsAPerson(b)) return needsAPerson(a) ? -1 : 1;
    return (b.startedAt ?? '').localeCompare(a.startedAt ?? '');
  });
}

/**
 * The list after one `change` event from the stream, in the shape `GET /runs` returns.
 * A `put` replaces the run it names or adds it; a `drop` removes it. Anything else is not ours.
 */
export function applyChange(runs: Run[], change: unknown): Run[] {
  if (typeof change !== 'object' || change === null) return runs;
  const it = change as { op?: string; collection?: string; doc?: Run; id?: string };
  if (it.collection !== 'runs') return runs;
  if (it.op === 'drop') return runs.filter((run) => run.ref !== it.id && String(run.ref) !== String(it.id));
  if (it.op === 'put' && it.doc && it.doc.ref) {
    const without = runs.filter((run) => run.ref !== it.doc!.ref);
    return order([it.doc, ...without]);
  }
  return runs;
}

/* ── attaching what you are looking at ─────────────────────────── */

/**
 * The path to give the server for a file, or null when it is outside the folder open here.
 *
 * The server reads an attached file inside the project's own checkout, so the path has to be
 * relative to the root of that checkout — which is the folder open in the editor. A file from
 * somewhere else on the disk has no such path, and saying so is the only honest answer: inventing
 * one would attach a different file, or none, without saying that is what happened.
 */
export function relativeTo(root: string, file: string): string | null {
  const from = withoutTrailingSlash(root.replace(/\\/g, '/'));
  const to = file.replace(/\\/g, '/');
  if (!from || to === from) return null;
  if (!to.startsWith(`${from}/`)) return null;
  return to.slice(from.length + 1);
}

/** The folder a file is in, which is what the Workbench opens. */
export function folderOf(file: string): string {
  const at = file.replace(/\\/g, '/').lastIndexOf('/');
  return at <= 0 ? '/' : file.replace(/\\/g, '/').slice(0, at);
}

export interface Selected {
  /** The file, relative to the project, as the server will read it. */
  path: string;
  /** One-based and inclusive, as an editor counts and as a person says them. */
  from: number;
  to: number;
  text: string;
  /** The editor's language id, for the fence. Empty is fine. */
  language: string;
}

/**
 * The question that is actually asked: the person's words, then what they had selected, quoted.
 *
 * The selection is quoted into the question rather than only attached, because the attachment is the
 * whole file — the server reads an attached file whole — and "this selection" has to point at
 * something. The line numbers are in the text for the same reason: a model that is asked about lines
 * 42 to 58 can say so back, and a person reading the session afterwards can see what was meant.
 */
export function selectionQuestion(question: string, what: Selected): string {
  const where = what.from === what.to ? `line ${what.from}` : `lines ${what.from}–${what.to}`;
  const fence = '```';
  return [
    question.trim(),
    '',
    `From \`${what.path}\`, ${where}:`,
    '',
    `${fence}${what.language}`,
    what.text.replace(/\s+$/, ''),
    fence,
  ].join('\n');
}

/** A short title for the session this starts, so the Sessions list is readable. */
export function sessionTitle(what: Selected): string {
  const name = what.path.split('/').pop() ?? what.path;
  const where = what.from === what.to ? `:${what.from}` : `:${what.from}-${what.to}`;
  return `${name}${where}`.slice(0, 80);
}

/* ── where things are in the web app ───────────────────────────── */

const at = (web: string, route: string) => `${withoutTrailingSlash(web)}${route}`;
const q = (value: string) => encodeURIComponent(value);

export function workbenchUrl(web: string, projectId: string, folder?: string): string {
  const route = `/workbench?project=${q(projectId)}${folder ? `&folder=${q(folder)}` : ''}`;
  return at(web, route);
}
export const sessionUrl = (web: string, ref: string) => at(web, `/sessions?ref=${q(ref)}`);
export const planUrl = (web: string, ref: string) => at(web, `/plans?ref=${q(ref)}`);
export const runUrl = (web: string, ref: string) => at(web, `/runs?ref=${q(ref)}`);
