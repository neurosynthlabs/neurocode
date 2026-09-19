/* Finding, or starting, the local NeuroCode stack the desktop app shows.

   Found: an API already answering on this machine (the one `npm run dev:start` runs, on NC_API_PORT or 8787) is
   used as it is and left running when the app quits — it is not the app's to stop.
   Started: otherwise the app asks `python -m app.data.check` first — no Postgres, no database and no migrations
   look the same in a uvicorn log, and each has its own one-line fix, which the check prints — then runs the API
   the way package.json's `api` script does, on a free port, as a child it watches, restarts if it dies, and
   stops when the app quits. */
import { spawn, execFile, type ChildProcess } from 'node:child_process';
import { createWriteStream, existsSync, readFileSync, renameSync, type WriteStream } from 'node:fs';
import net, { type AddressInfo } from 'node:net';
import os from 'node:os';
import path from 'node:path';

/** What went wrong, in the words a person needs: a title, what was seen, and the fix when there is one. */
export class StackProblem extends Error {
  readonly title: string;
  readonly detail: string;
  constructor(title: string, detail: string) {
    super(`${title}: ${detail}`);
    this.title = title;
    this.detail = detail;
  }
}

export interface Stack {
  /** Where the API answers, e.g. http://127.0.0.1:53121. */
  api: string;
  /** 'found' when it was already running; 'started' when this app runs it. */
  how: 'found' | 'started';
  stop: () => Promise<void>;
}

export interface StackOptions {
  /** The NeuroCode checkout: the folder holding server/. */
  home: string;
  /** The API's log, appended to by the child; the previous launch's is kept beside it. */
  log: string;
  /** Said while starting, for the splash. */
  status: (text: string) => void;
  /** The API died after it was up and could not be brought back. */
  lost: (problem: StackProblem) => void;
  /** The API came back on another port after dying. */
  moved: (api: string) => void;
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** Does a NeuroCode API answer at `base`? Its /health says `ok` (true, or false when its database is down). */
export async function answers(base: string, ms = 1500): Promise<boolean> {
  try {
    const r = await fetch(`${base}/health`, { signal: AbortSignal.timeout(ms) });
    if (!r.ok && r.status !== 503) return false;
    const body = await r.json() as { ok?: unknown };
    return typeof body.ok === 'boolean';
  } catch {
    return false;
  }
}

/** A port nothing listens on right now, on the loopback address only. */
export function freePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const s = net.createServer();
    s.once('error', reject);
    s.listen(0, '127.0.0.1', () => {
      const { port } = s.address() as AddressInfo;
      s.close(() => resolve(port));
    });
  });
}

/* An app opened from the Finder or the Dock inherits launchd's PATH (/usr/bin:/bin:/usr/sbin:/sbin), not the one a
   terminal has, so `uv`, `code` and `git` are not found. The person's login shell is asked for its PATH once;
   if it does not answer quickly, the usual install places are added instead. */
let pathReady: Promise<void> | null = null;
export function adoptShellPath(): Promise<void> {
  pathReady ??= new Promise<void>((resolve) => {
    const usual = [
      path.join(os.homedir(), '.local/bin'), path.join(os.homedir(), '.cargo/bin'), '/opt/homebrew/bin', '/usr/local/bin',
    ];
    const merge = (extra: string[]) => {
      const have = (process.env.PATH ?? '').split(':').filter(Boolean);
      process.env.PATH = [...new Set([...extra, ...have, ...usual])].join(':');
      resolve();
    };
    if (process.platform === 'win32') { resolve(); return; }
    const shell = process.env.SHELL || '/bin/zsh';
    execFile(shell, ['-ilc', 'printf "__NC_PATH__%s__NC_PATH__" "$PATH"'], { timeout: 4000, encoding: 'utf8' }, (err, out) => {
      const found = /__NC_PATH__(.*)__NC_PATH__/.exec(out ?? '');
      if (err || !found) {
        console.warn('[NeuroCode] the login shell did not say its PATH; using the usual install places:', err?.message ?? 'no answer');
        merge([]);
        return;
      }
      merge(found[1].split(':').filter(Boolean));
    });
  });
  return pathReady;
}

/** A program on PATH, or null. */
export function which(program: string): string | null {
  for (const dir of (process.env.PATH ?? '').split(':')) {
    if (!dir) continue;
    const full = path.join(dir, program);
    if (existsSync(full)) return full;
  }
  return null;
}

/** The last lines of the API's log, for a failure that happened inside it. */
function tail(file: string, lines = 15): string {
  try {
    return readFileSync(file, 'utf8').trimEnd().split('\n').slice(-lines).join('\n');
  } catch {
    return '';
  }
}

function run(cmd: string, args: string[], cwd: string, ms: number): Promise<{ code: number | null; out: string }> {
  return new Promise((resolve, reject) => {
    const child = spawn(cmd, args, { cwd, env: process.env, stdio: ['ignore', 'pipe', 'pipe'] });
    let out = '';
    child.stdout.on('data', (d: Buffer) => { out += d.toString(); });
    child.stderr.on('data', (d: Buffer) => { out += d.toString(); });
    const timer = setTimeout(() => child.kill('SIGKILL'), ms);
    child.on('error', (e) => { clearTimeout(timer); reject(e); });
    child.on('close', (code) => { clearTimeout(timer); resolve({ code, out }); });
  });
}

/** The API a person already runs on this machine, if one answers where `npm run dev:start` puts it. */
export async function findRunning(): Promise<string | null> {
  if (process.env.NEUROCODE_DESKTOP_FIND === '0') return null;
  const explicit = process.env.NEUROCODE_API_URL;
  if (explicit) {
    const url = new URL(explicit);
    // The native folder dialog, Reveal in Finder and Open in editor act on this Mac's disk; an API elsewhere
    // would be handed paths from here that mean nothing there.
    if (!['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname)) {
      throw new StackProblem('That API is not on this Mac',
        `NEUROCODE_API_URL is ${explicit}. The desktop app works with an API on this machine; open a remote NeuroCode in the browser instead.`);
    }
    if (await answers(explicit.replace(/\/$/, ''))) return explicit.replace(/\/$/, '');
    throw new StackProblem('The API named by NEUROCODE_API_URL is not answering', `Nothing answered at ${explicit}/health.`);
  }
  const at = `http://127.0.0.1:${process.env.NC_API_PORT ?? 8787}`;
  return (await answers(at)) ? at : null;
}

/** Start the API from the checkout at `home`, and keep it running until `stop`. */
export async function startApi(options: StackOptions): Promise<Stack> {
  const { home, log, status } = options;
  const server = path.join(home, 'server');
  if (!existsSync(path.join(server, 'pyproject.toml'))) {
    throw new StackProblem('NeuroCode\'s server was not found',
      `There is no server/pyproject.toml in ${home}. Set NEUROCODE_HOME to the folder you cloned NeuroCode into, then open the app again.`);
  }
  await adoptShellPath();
  const uv = which('uv');
  if (!uv) {
    throw new StackProblem('uv is not installed',
      'The API runs through uv, which was not found on this Mac. Install it (https://docs.astral.sh/uv/), then try again.');
  }

  status('Checking the database…');
  // The first run also installs the server's Python packages, which can take a minute on a new machine.
  const check = await run(uv, ['run', '--project', server, '--directory', server, 'python', '-m', 'app.data.check'], home, 300_000);
  if (check.code !== 0) {
    const said = check.out.trim().split('\n').filter((l) => !/^(Using|Creating|Installed|Resolved|Prepared|Audited|Uninstalled|Downloading|Building|Built)\b/.test(l.trim()));
    throw new StackProblem('The database is not ready', said.join('\n') || `The check exited with ${check.code ?? 'a signal'}.`);
  }

  let child: ChildProcess | null = null;
  let stopping = false;
  let out: WriteStream | null = null;
  let current = '';
  const deaths: number[] = [];

  const launch = async (): Promise<string> => {
    const port = await freePort();
    out?.end();
    out = createWriteStream(log, { flags: 'a' });
    out.write(`\n── ${new Date().toISOString()} · starting the API on 127.0.0.1:${port}\n`);
    const started = spawn(uv, [
      'run', '--project', server, 'uvicorn', 'app.api.app:create_api', '--factory', '--app-dir', server,
      '--host', '127.0.0.1', '--port', String(port), '--timeout-graceful-shutdown', '5',
    ], {
      cwd: home,
      env: process.env,
      stdio: ['ignore', 'pipe', 'pipe'],
      // Its own process group: uv starts uvicorn as a child of its own, and stopping must reach both.
      detached: true,
    });
    child = started;
    started.stdout?.pipe(out, { end: false });
    started.stderr?.pipe(out, { end: false });
    let exited: string | null = null;
    started.on('error', (e) => { exited = e.message; });
    started.on('exit', (code, signal) => {
      exited = `exited with ${code ?? signal}`;
      if (child !== started || stopping) return;
      child = null;
      void revive();
    });
    const base = `http://127.0.0.1:${port}`;
    const t0 = Date.now();
    while (Date.now() - t0 < 90_000) {
      if (exited) {
        throw new StackProblem('The API stopped while starting', `${exited}.\n${tail(log)}`);
      }
      if (await answers(base, 1000)) return base;
      await sleep(300);
    }
    kill(started);
    throw new StackProblem('The API did not start', `Nothing answered at ${base}/health after 90 seconds.\n${tail(log)}`);
  };

  // Died after it was up: brought back at most three times a minute, a second more patient each time. A fourth
  // death inside the minute is a crash loop, and the person is shown why rather than a page that keeps blinking.
  const revive = async () => {
    const now = Date.now();
    deaths.push(now);
    while (deaths.length && now - deaths[0] > 60_000) deaths.shift();
    if (deaths.length > 3) {
      options.lost(new StackProblem('The API keeps stopping', tail(log)));
      return;
    }
    await sleep(1000 * deaths.length);
    if (stopping) return;
    try {
      current = await launch();
      options.moved(current);
    } catch (e) {
      options.lost(e instanceof StackProblem ? e : new StackProblem('The API could not be restarted', String(e)));
    }
  };

  status('Starting the API…');
  current = await launch();
  return {
    get api() { return current; },
    how: 'started',
    stop: async () => {
      stopping = true;
      const running = child;
      child = null;
      if (running) {
        kill(running);
        const gone = await Promise.race([
          new Promise<boolean>((r) => running.once('exit', () => r(true))),
          sleep(7000).then(() => false),
        ]);
        if (!gone) kill(running, 'SIGKILL');
      }
      out?.end();
    },
  };
}

/** The whole group: uv and the uvicorn it started. */
function kill(child: ChildProcess, signal: NodeJS.Signals = 'SIGTERM'): void {
  if (child.pid === undefined || child.exitCode !== null) return;
  try {
    process.kill(-child.pid, signal);
  } catch {
    child.kill(signal);
  }
}

/** The previous launch's log is kept as api.previous.log; this launch writes a fresh one. */
export function rotateLog(file: string): void {
  try {
    if (existsSync(file)) renameSync(file, file.replace(/\.log$/, '.previous.log'));
  } catch (e) {
    console.warn('[NeuroCode] the last API log was not kept:', e);
  }
}
