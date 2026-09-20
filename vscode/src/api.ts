/* The one way this extension talks to a NeuroCode server.
 *
 * There is no second brain here. Every command below is a request to the same HTTP API the web app
 * and `nc` use, signed in with a personal access token, which means every permission, every tool
 * rule and every gate is the server's — the extension cannot do anything an account cannot do, and
 * it never decides anything on its own.
 *
 * Refusals are carried, not swallowed. The API writes its refusals as sentences meant for a person
 * ("Your role does not include…", "No model can answer…"), and `Refused.detail` is that sentence,
 * shown as it was written. A client that replaced it with "Request failed" would be throwing away
 * the only part of the answer that tells anybody what to do next.
 */
import { candidates, isHealth, readEvents, type Address, type StreamEvent } from './protocol';

export class Refused extends Error {
  /** The HTTP status, or 0 when the server could not be reached at all. */
  readonly status: number;
  readonly detail: string;
  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
    this.detail = detail;
  }
}

async function said(answer: Response): Promise<string> {
  let body: unknown;
  try {
    body = await answer.json();
  } catch {
    return `The server answered ${answer.status} and nothing that could be read.`;
  }
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((one) => {
        const it = one as { loc?: unknown[]; msg?: unknown };
        const where = Array.isArray(it.loc) ? it.loc.slice(1).join('.') : '';
        return where ? `${where}: ${String(it.msg)}` : String(it.msg);
      })
      .join('; ');
  }
  return `The server answered ${answer.status}.`;
}

export class Api {
  constructor(
    readonly base: string,
    private readonly token: string,
  ) {}

  private headers(): Record<string, string> {
    return {
      Authorization: `Bearer ${this.token}`,
      // The API asks this of a change carried by the session cookie. A bearer token does not need
      // it, and sending it anyway costs nothing and says plainly which client this is.
      'X-NC-Client': 'vscode',
      Accept: 'application/json',
    };
  }

  async call<T>(method: string, path: string, body?: unknown): Promise<T> {
    let answer: Response;
    try {
      answer = await fetch(`${this.base}${path}`, {
        method,
        headers: body === undefined ? this.headers() : { ...this.headers(), 'Content-Type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch (e) {
      throw new Refused(0, `${this.base} could not be reached: ${e instanceof Error ? e.message : String(e)}`);
    }
    if (!answer.ok) throw new Refused(answer.status, await said(answer));
    if (answer.status === 204) return undefined as T;
    return (await answer.json()) as T;
  }

  me = () => this.call<{ user: { name: string; email: string }; workspace: { name: string } | null }>('GET', '/auth/me');
  projects = () => this.call<{ id: string; name: string }[]>('GET', '/projects');
  runs = (limit = 50) => this.call<unknown[]>('GET', `/runs?limit=${limit}`);
  startSession = (projectId: string, title: string) =>
    this.call<{ ref: string }>('POST', '/sessions', { projectId, title });
  ask = (ref: string, text: string, attachments: { kind: string; ref: string; name: string }[]) =>
    this.call<unknown>('POST', `/sessions/${encodeURIComponent(ref)}/messages`, { text, attachments });
  compile = (projectId: string, requirement: string) =>
    this.call<{ ref: string; openQuestions?: unknown[] }>('POST', '/plans/compile', { projectId, requirement });

  /**
   * The live stream, read until the caller aborts it. The same `/activity/stream` every open tab
   * reads; this one only ever listens.
   *
   * Reconnecting is the caller's job, so that a view can say it is reconnecting rather than a client
   * quietly retrying for ever behind a list that has stopped being true.
   */
  async stream(onEvent: (event: StreamEvent) => void, signal: AbortSignal): Promise<void> {
    const answer = await fetch(`${this.base}/activity/stream`, {
      headers: { ...this.headers(), Accept: 'text/event-stream' },
      signal,
    }).catch((e) => {
      throw new Refused(0, `The live stream could not be opened: ${e instanceof Error ? e.message : String(e)}`);
    });
    if (!answer.ok) throw new Refused(answer.status, await said(answer));
    if (!answer.body) throw new Refused(0, 'The live stream opened with no body to read.');
    const lines = answer.body.pipeThrough(new TextDecoderStream());
    let carry = '';
    for await (const chunk of lines as unknown as AsyncIterable<string>) {
      const { events, rest } = readEvents(carry + chunk);
      carry = rest;
      for (const event of events) onEvent(event);
    }
  }
}

/**
 * Which of the two addresses a person's server really is, asked rather than assumed — the same
 * probe `nc login` makes, so both clients read one address the same way.
 */
export async function locate(address: string): Promise<Address> {
  const tried: string[] = [];
  for (const where of candidates(address)) {
    tried.push(`${where.api}/health`);
    try {
      const answer = await fetch(`${where.api}/health`, { headers: { Accept: 'application/json' } });
      if (answer.ok && isHealth(await answer.json())) return where;
    } catch {
      continue;
    }
  }
  throw new Refused(
    0,
    `No NeuroCode API answers at ${address} (tried ${tried.join(' and ')}). ` +
      'Check “neurocode.server” in the settings, and that the server is running.',
  );
}
