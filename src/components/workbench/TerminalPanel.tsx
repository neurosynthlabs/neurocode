import { useCallback, useEffect, useRef, useState } from 'react';
import { Terminal as Xterm, type ITheme } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import { WebLinksAddon } from '@xterm/addon-web-links';
import '@xterm/xterm/css/xterm.css';
import { Loader2, Plus, RotateCcw, SquareTerminal, X } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Empty, cx } from '@/components/os';
import { ApiError } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { FINAL_CLOSE, terminals, type TerminalDoc, type TerminalMessage } from '@/lib/live/terminal';
import { useTheme } from '@/lib/theme';

/* The Workbench's terminals: real shells on the machine the API runs on, drawn by xterm.js. A terminal
   lives on the server, not in this tab — closing the panel, switching tabs or reloading only closes the
   socket, and opening it again repaints the screen from what the server kept. "Kill" ends the shell. */

const reason = (e: unknown) => (e instanceof ApiError ? e.message : 'The local API did not answer.');
const MONO = 'ui-monospace, "SF Mono", SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace';
const RETRY_MS = [1000, 2000, 4000, 8000, 10_000];

/* ── a theme from the app's own colours ──────────────────────────── */

type Rgb = [number, number, number];

/** Reads CSS colour tokens as the browser resolves them, back as pixels, so every notation (hsl, color-mix) works. */
function colourReader(): { read: (token: string) => Rgb; done: () => void } | null {
  const canvas = document.createElement('canvas');
  canvas.width = 1;
  canvas.height = 1;
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  if (!ctx) return null;
  const probe = document.createElement('span');
  probe.style.display = 'none';
  document.body.appendChild(probe);
  const read = (token: string): Rgb => {
    probe.style.color = `var(${token})`;
    ctx.clearRect(0, 0, 1, 1);
    ctx.fillStyle = '#000';
    ctx.fillStyle = getComputedStyle(probe).color;
    ctx.fillRect(0, 0, 1, 1);
    const [r, g, b] = ctx.getImageData(0, 0, 1, 1).data;
    return [r, g, b];
  };
  return { read, done: () => probe.remove() };
}

const hex = ([r, g, b]: Rgb) => `#${[r, g, b].map((n) => n.toString(16).padStart(2, '0')).join('')}`;
const mix = (a: Rgb, b: Rgb, t: number): Rgb => [0, 1, 2].map((i) => Math.round(a[i] * (1 - t) + b[i] * t)) as Rgb;
const light = ([r, g, b]: Rgb) => (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255 > 0.5;

/** xterm's colours from the theme tokens: the ground is the panel's, the sixteen ANSI colours the status ones. */
function xtermTheme(): ITheme {
  const colours = colourReader();
  if (!colours) return {};
  const { read, done } = colours;
  const surface = read('--os-surface');
  const ink = read('--os-ink');
  const [brand, ok, warn, danger, info, violet, soft, dim, s3, ink2] =
    ['--os-brand', '--os-ok', '--os-warn', '--os-danger', '--os-info', '--os-violet', '--os-soft', '--os-dim',
      '--os-surface-3', '--os-ink-2'].map(read);
  done();
  const dark = !light(surface);
  const bright = (c: Rgb) => hex(mix(c, ink, dark ? 0.25 : 0.15));
  const cyan = mix(info, ok, 0.5);
  return {
    background: hex(surface), foreground: hex(ink), cursor: hex(brand), cursorAccent: hex(surface),
    selectionBackground: `${hex(brand)}55`, selectionInactiveBackground: `${hex(brand)}33`,
    black: hex(dark ? s3 : ink), red: hex(danger), green: hex(ok), yellow: hex(warn), blue: hex(info),
    magenta: hex(violet), cyan: hex(cyan), white: hex(dark ? ink2 : soft),
    brightBlack: hex(dim), brightRed: bright(danger), brightGreen: bright(ok), brightYellow: bright(warn),
    brightBlue: bright(info), brightMagenta: bright(violet), brightCyan: bright(cyan), brightWhite: hex(dark ? ink : ink2),
  };
}

/** Changes whenever the palette does, so an open terminal repaints with the app. */
function useThemeKey() {
  const t = useTheme();
  return `${t.theme}|${t.baseMode}|${t.themeColor}|${t.surfaceTone}`;
}

/* ── one terminal on screen ──────────────────────────────────────── */

type Link = { state: 'connecting' | 'open' } | { state: 'retrying'; inMs: number } | { state: 'gone'; why: string };

/**
 * One terminal, drawn and connected: keystrokes go to the server as binary frames, output comes back as
 * binary frames, and the size follows the element. A dropped socket reconnects by itself and repaints;
 * one the server closed for good (signed out, not allowed, terminal gone) says why and stays closed.
 */
export function TerminalView({ terminalId, onMessage, className }: {
  terminalId: string;
  onMessage?: (message: TerminalMessage) => void;
  className?: string;
}) {
  const host = useRef<HTMLDivElement>(null);
  const term = useRef<Xterm | null>(null);
  const said = useRef(onMessage);
  const [link, setLink] = useState<Link>({ state: 'connecting' });
  const themeKey = useThemeKey();
  useEffect(() => { said.current = onMessage; });

  useEffect(() => {
    const el = host.current;
    if (!el) return;
    const xterm = new Xterm({
      fontFamily: MONO, fontSize: 12.5, lineHeight: 1.25, cursorBlink: true, scrollback: 5000,
      allowTransparency: false, theme: xtermTheme(), macOptionIsMeta: true,
    });
    const fit = new FitAddon();
    xterm.loadAddon(fit);
    xterm.loadAddon(new WebLinksAddon((_event, uri) => { window.open(uri, '_blank', 'noopener,noreferrer'); }));
    xterm.open(el);
    term.current = xterm;
    const encoder = new TextEncoder();
    let socket: WebSocket | null = null;
    let attempt = 0;
    let timer: number | undefined;
    let over = false;

    const send = (data: string | Uint8Array<ArrayBuffer>) => {
      if (socket?.readyState === WebSocket.OPEN) socket.send(data);
    };
    const sendSize = () => send(JSON.stringify({ type: 'resize', cols: xterm.cols, rows: xterm.rows }));
    const refit = () => {
      if (!el.offsetWidth || !el.offsetHeight) return;
      try { fit.fit(); } catch { /* not laid out yet */ }
    };

    const connect = () => {
      setLink({ state: 'connecting' });
      const ws = new WebSocket(terminals.socket(terminalId));
      ws.binaryType = 'arraybuffer';
      socket = ws;
      ws.onopen = () => { attempt = 0; setLink({ state: 'open' }); refit(); sendSize(); };
      ws.onmessage = (event: MessageEvent<ArrayBuffer | string>) => {
        if (typeof event.data !== 'string') {
          xterm.write(new Uint8Array(event.data));
          return;
        }
        let message: TerminalMessage;
        try { message = JSON.parse(event.data) as TerminalMessage; } catch { return; }
        // A hello is followed by everything the terminal kept; a repaint by the same. Start clean for it.
        if (message.type === 'hello' || message.type === 'repaint') xterm.reset();
        if (message.type === 'exit') {
          xterm.write(`\r\n\x1b[2mProcess exited${message.code === null ? '' : ` with code ${message.code}`}.\x1b[0m\r\n`);
        }
        if (message.type === 'error') toast.error('Terminal', { description: message.message });
        said.current?.(message);
      };
      ws.onclose = (event) => {
        if (socket !== ws || over) return;
        socket = null;
        const final = FINAL_CLOSE[event.code] ?? (event.code === 1000 ? 'This terminal was closed.' : null);
        if (final) {
          setLink({ state: 'gone', why: event.reason || final });
          return;
        }
        const wait = RETRY_MS[Math.min(attempt, RETRY_MS.length - 1)];
        attempt += 1;
        setLink({ state: 'retrying', inMs: wait });
        timer = window.setTimeout(connect, wait);
      };
    };

    const typed = xterm.onData((data) => send(encoder.encode(data)));
    const binary = xterm.onBinary((data) => send(Uint8Array.from(data, (c) => c.charCodeAt(0))));
    const resized = xterm.onResize(() => sendSize());
    const observer = new ResizeObserver(() => refit());
    observer.observe(el);
    refit();
    connect();
    return () => {
      over = true;
      window.clearTimeout(timer);
      observer.disconnect();
      typed.dispose();
      binary.dispose();
      resized.dispose();
      socket?.close();
      xterm.dispose();
      term.current = null;
    };
  }, [terminalId]);

  // The palette moves the CSS variables in its own effect, which runs after this one: read them a frame later.
  useEffect(() => {
    const frame = requestAnimationFrame(() => { if (term.current) term.current.options.theme = xtermTheme(); });
    return () => cancelAnimationFrame(frame);
  }, [themeKey]);

  return (
    <div className={cx('relative min-h-0 flex-1 overflow-hidden bg-surface', className)}>
      <div ref={host} className="absolute inset-0 px-3 py-2" onClick={() => term.current?.focus()} />
      {link.state !== 'open' && (
        <div className="pointer-events-none absolute top-2 right-3 flex max-w-[80%] items-center gap-1.5 rounded-full border border-line/70 bg-surface-2/95 px-2.5 py-1 text-[12px] text-soft shadow-sm">
          {link.state === 'gone' ? link.why
            : <><Loader2 className="size-3 animate-spin" />{link.state === 'connecting' ? 'Connecting…' : 'Connection lost — reconnecting…'}</>}
        </div>
      )}
    </div>
  );
}

/* ── the Terminal tab ────────────────────────────────────────────── */

/**
 * The Terminal tab of the Workbench's bottom panel: this person's shells as tabs, a new one in the folder
 * the Workbench shows (`cwd`, else the project's checkout), and Kill. Run configurations' terminals are
 * the Run tab's, so they are not listed here.
 */
export function TerminalPanel({ projectId, cwd = null }: { projectId: string | null; cwd?: string | null }) {
  const { machine } = useAuth();
  const allowed = machine;
  const [list, setList] = useState<TerminalDoc[] | null>(null);
  const [error, setError] = useState<{ status: number; message: string } | null>(null);
  const [active, setActive] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [version, setVersion] = useState(0);
  const load = () => setVersion((v) => v + 1);

  useEffect(() => {
    if (!allowed) return;
    let current = true;
    terminals.list().then(
      (all) => {
        if (!current) return;
        const shells = all.filter((t) => t.kind === 'shell');
        setList(shells);
        setError(null);
        setActive((now) => (now && shells.some((t) => t.id === now) ? now : shells.at(-1)?.id ?? null));
      },
      (e: unknown) => { if (current) setError({ status: e instanceof ApiError ? e.status : 0, message: reason(e) }); },
    );
    return () => { current = false; };
  }, [allowed, version]);

  const open = async () => {
    setBusy(true);
    try {
      const made = await terminals.open({ cwd: cwd ?? null, projectId: cwd ? null : projectId, cols: 100, rows: 30 });
      setList((now) => [...(now ?? []), made]);
      setActive(made.id);
    } catch (e) {
      toast.error('No terminal opened', { description: reason(e) });
    } finally {
      setBusy(false);
    }
  };

  const kill = async (id: string) => {
    try {
      await terminals.close(id);
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 404)) {
        toast.error('The terminal did not close', { description: reason(e) });
        return;
      }
    }
    setList((now) => {
      const left = (now ?? []).filter((t) => t.id !== id);
      setActive((current) => (current === id ? left.at(-1)?.id ?? null : current));
      return left;
    });
  };

  const restart = async (id: string) => {
    try {
      const again = await terminals.restart(id);
      setList((now) => (now ?? []).map((t) => (t.id === id ? again : t)));
    } catch (e) {
      toast.error('The terminal did not restart', { description: reason(e) });
    }
  };

  const onMessage = useCallback((message: TerminalMessage) => {
    if (message.type === 'hello' || message.type === 'state') {
      setList((now) => (now ?? []).map((t) => (t.id === message.terminal.id ? message.terminal : t)));
    } else if (message.type === 'exit' && active) {
      setList((now) => (now ?? []).map((t) => (t.id === active ? { ...t, status: 'exited', exitCode: message.code } : t)));
    } else if (message.type === 'closed' && active) {
      setList((now) => (now ?? []).filter((t) => t.id !== active));
    }
  }, [active]);

  if (!allowed) {
    return <Empty icon={<SquareTerminal className="size-6" />} title="Terminals need the machine:access permission"
      hint="A terminal is a shell on the machine the API runs on, so only an Owner holds it unless an Owner grants it." />;
  }
  if (error && !list) {
    return (
      <Empty icon={<SquareTerminal className="size-6" />}
        title={error.status === 404 ? 'Machine access is off on this server' : 'The terminals did not load'}
        hint={error.status === 404 ? 'The API was started with NEUROCODE_MACHINE_ACCESS=false, so it opens no shells. Start it without that to use terminals here.' : error.message}
        action={error.status === 404 ? undefined : <Button size="sm" variant="outline" onClick={load}>Try again</Button>} />
    );
  }
  if (!list) return <Empty icon={<Loader2 className="size-5 animate-spin" />} title="Reading your terminals…" />;

  const current = list.find((t) => t.id === active) ?? null;
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 items-center gap-2 border-b border-line/60 px-3 py-1.5">
        <div className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto" role="tablist" aria-label="Terminals">
          {list.map((t) => (
            <div key={t.id} role="tab" aria-selected={t.id === active} tabIndex={0}
              onClick={() => setActive(t.id)} onKeyDown={(e) => { if (e.key === 'Enter') setActive(t.id); }}
              className={cx('group flex h-7 shrink-0 cursor-pointer items-center gap-1.5 rounded-md pr-1 pl-2.5 text-[12.5px] transition-colors',
                t.id === active ? 'bg-surface-3 text-ink' : 'text-soft hover:bg-surface-2 hover:text-ink-2')}>
              <span className={cx('size-1.5 rounded-full', t.status === 'running' ? 'bg-ok' : 'bg-line-strong')} />
              <span className="max-w-[160px] truncate" title={t.cwd}>{t.title}</span>
              <button type="button" aria-label={`Kill ${t.title}`} title="Kill"
                onClick={(e) => { e.stopPropagation(); void kill(t.id); }}
                className="grid size-5 place-items-center rounded text-dim opacity-60 hover:bg-surface-2 hover:text-ink group-hover:opacity-100">
                <X className="size-3" />
              </button>
            </div>
          ))}
          <Button size="icon-xs" variant="ghost" aria-label="New terminal" title="New terminal" disabled={busy} onClick={() => void open()}>
            {busy ? <Loader2 className="animate-spin" /> : <Plus />}
          </Button>
        </div>
        {current && (
          <div className="flex shrink-0 items-center gap-1.5">
            <span className="hidden max-w-[280px] truncate font-mono text-[11.5px] text-dim md:inline" title={current.cwd}>{current.cwd}</span>
            {current.status === 'exited' && (
              <Button size="xs" variant="ghost" onClick={() => void restart(current.id)}><RotateCcw />Restart</Button>
            )}
            <Button size="xs" variant="ghost" onClick={() => void kill(current.id)}><X />Kill</Button>
          </div>
        )}
      </div>
      {current ? (
        <TerminalView key={current.id} terminalId={current.id} onMessage={onMessage} />
      ) : (
        <Empty icon={<SquareTerminal className="size-6" />} title="No terminal open"
          hint={cwd ? `A shell opens in ${cwd}.` : projectId ? "A shell opens in this project's checkout." : 'A shell opens in the first folder this server may open.'}
          action={<Button size="sm" variant="outline" disabled={busy} onClick={() => void open()}><Plus className="size-3.5" />New terminal</Button>} />
      )}
    </div>
  );
}
