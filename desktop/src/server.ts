/* The desktop app's own web server: the built web app (dist/) and, under /api, the local API — on one origin,
   127.0.0.1 and a port of its own. One origin is the point: the session cookie the API sets is then the page's
   own, the X-NC-Client rule holds as it does in a browser, and the Workbench's terminal and debugger sockets open
   against the same host they would in development, where Vite's proxy does this job. */
import { createReadStream } from 'node:fs';
import { stat } from 'node:fs/promises';
import http, { type IncomingMessage, type ServerResponse } from 'node:http';
import net, { type AddressInfo } from 'node:net';
import path from 'node:path';

const TYPES: Record<string, string> = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.webp': 'image/webp',
  '.ico': 'image/x-icon',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
  '.ttf': 'font/ttf',
  '.wasm': 'application/wasm',
  '.txt': 'text/plain; charset=utf-8',
  '.map': 'application/json; charset=utf-8',
};

export interface AppServerOptions {
  /** The built web app: the folder holding index.html and assets/. */
  root: string;
  /** Where the API answers, e.g. http://127.0.0.1:8787. /api/x is sent there as /x. */
  api: string;
  /** Tried first, so the page keeps one origin (and so its own browser storage) from one launch to the next. */
  port?: number;
}

export interface AppServer {
  origin: string;
  port: number;
  /** Point /api somewhere else — the API was restarted on another port. */
  retarget: (api: string) => void;
  close: () => Promise<void>;
}

/** Only this machine's own names: a page elsewhere that points a DNS name at 127.0.0.1 is refused, not served. */
function hostAllowed(host: string | undefined, port: number): boolean {
  if (!host) return false;
  return host === `127.0.0.1:${port}` || host === `localhost:${port}`;
}

/** The file a request names inside `root`, or null when it would climb out of it. */
export function inside(root: string, urlPath: string): string | null {
  let decoded: string;
  try {
    decoded = decodeURIComponent(urlPath.split('?')[0]);
  } catch {
    return null;
  }
  if (decoded.includes('\0')) return null;
  const full = path.resolve(root, `.${path.posix.normalize(`/${decoded}`)}`);
  return full === root || full.startsWith(root + path.sep) ? full : null;
}

async function serveStatic(root: string, req: IncomingMessage, res: ServerResponse): Promise<void> {
  if (req.method !== 'GET' && req.method !== 'HEAD') {
    res.writeHead(405, { allow: 'GET, HEAD' }).end();
    return;
  }
  const url = req.url ?? '/';
  const wanted = inside(root, url);
  if (!wanted) {
    res.writeHead(400, { 'content-type': 'text/plain; charset=utf-8' }).end('Bad path');
    return;
  }
  let file = wanted;
  let info = await stat(file).catch(() => null);
  if (!info || !info.isFile()) {
    // A path the router owns (/projects/erp, /workbench?folder=…) is the app itself; a missing asset stays a 404,
    // so a stale chunk name fails loudly instead of being answered with HTML.
    if (path.extname(url.split('?')[0])) {
      res.writeHead(404, { 'content-type': 'text/plain; charset=utf-8' }).end('Not found');
      return;
    }
    file = path.join(root, 'index.html');
    info = await stat(file).catch(() => null);
    if (!info) {
      res.writeHead(500, { 'content-type': 'text/plain; charset=utf-8' }).end(`The web app is not built: ${file} is missing.`);
      return;
    }
  }
  const ext = path.extname(file).toLowerCase();
  const hashed = file.startsWith(path.join(root, 'assets') + path.sep);
  res.writeHead(200, {
    'content-type': TYPES[ext] ?? 'application/octet-stream',
    'content-length': info.size,
    // Vite names every asset by its content, so it never changes; the page that names them must always be asked again.
    'cache-control': hashed ? 'public, max-age=31536000, immutable' : 'no-cache',
    'x-content-type-options': 'nosniff',
  });
  if (req.method === 'HEAD') {
    res.end();
    return;
  }
  createReadStream(file).on('error', () => res.destroy()).pipe(res);
}

/** /api/x → the API's /x, streamed both ways: uploads are not held in memory, and the change stream (SSE) flows as it is written. */
function proxy(api: URL, req: IncomingMessage, res: ServerResponse): void {
  const target = (req.url ?? '/').replace(/^\/api(?=\/|$|\?)/, '') || '/';
  const upstream = http.request({
    host: api.hostname,
    port: api.port,
    method: req.method,
    path: target.startsWith('/') ? target : `/${target}`,
    headers: req.headers,
  }, (answer) => {
    res.writeHead(answer.statusCode ?? 502, answer.statusMessage, answer.headers);
    // Headers go out at once: an event stream says nothing until something changes, and the page must not wait for it.
    res.flushHeaders();
    answer.pipe(res);
  });
  upstream.on('error', () => {
    if (res.headersSent) {
      res.destroy();
      return;
    }
    res.writeHead(502, { 'content-type': 'application/json' })
      .end(JSON.stringify({ detail: 'The local NeuroCode API is not answering. It may be restarting; try again in a moment.' }));
  });
  req.on('aborted', () => upstream.destroy());
  res.on('close', () => upstream.destroy());
  req.pipe(upstream);
}

/** A WebSocket under /api (the terminal, the debugger) is handed to the API as raw bytes after its first line is rewritten. */
function tunnel(api: URL, req: IncomingMessage, socket: net.Socket, head: Buffer, open: Set<net.Socket>): void {
  const target = (req.url ?? '/').replace(/^\/api(?=\/|$|\?)/, '') || '/';
  const upstream = net.connect(Number(api.port), api.hostname, () => {
    const lines = [`${req.method} ${target} HTTP/${req.httpVersion}`];
    for (let i = 0; i < req.rawHeaders.length; i += 2) lines.push(`${req.rawHeaders[i]}: ${req.rawHeaders[i + 1]}`);
    upstream.write(`${lines.join('\r\n')}\r\n\r\n`);
    if (head.length) upstream.write(head);
    upstream.pipe(socket);
    socket.pipe(upstream);
  });
  // An upgraded socket is no longer the HTTP server's to close; they are kept here so closing the server ends them.
  open.add(socket);
  open.add(upstream);
  const end = () => { upstream.destroy(); socket.destroy(); };
  socket.once('close', () => open.delete(socket));
  upstream.once('close', () => open.delete(upstream));
  upstream.on('error', end);
  socket.on('error', end);
  upstream.on('close', () => socket.destroy());
  socket.on('close', () => upstream.destroy());
}

function listen(server: http.Server, port: number): Promise<number> {
  return new Promise((resolve, reject) => {
    const fail = (e: Error) => { server.off('listening', ok); reject(e); };
    const ok = () => { server.off('error', fail); resolve((server.address() as AddressInfo).port); };
    server.once('error', fail);
    server.once('listening', ok);
    server.listen(port, '127.0.0.1');
  });
}

export async function startAppServer(options: AppServerOptions): Promise<AppServer> {
  const root = path.resolve(options.root);
  let api = new URL(options.api);
  let port = 0;
  const tunnels = new Set<net.Socket>();
  const server = http.createServer((req, res) => {
    if (!hostAllowed(req.headers.host, port)) {
      res.writeHead(403, { 'content-type': 'text/plain; charset=utf-8' }).end('This server answers only the NeuroCode app on this machine.');
      return;
    }
    if (/^\/api(\/|$|\?)/.test(req.url ?? '')) proxy(api, req, res);
    else void serveStatic(root, req, res).catch(() => { if (!res.headersSent) res.writeHead(500).end(); else res.destroy(); });
  });
  server.on('upgrade', (req: IncomingMessage, socket: net.Socket, head: Buffer) => {
    if (!hostAllowed(req.headers.host, port) || !/^\/api\//.test(req.url ?? '')) {
      socket.end('HTTP/1.1 403 Forbidden\r\n\r\n');
      return;
    }
    tunnel(api, req, socket, head, tunnels);
  });
  // A long-lived event stream is not idle: Node's default request timeout would cut it.
  server.requestTimeout = 0;
  server.headersTimeout = 30_000;
  try {
    port = await listen(server, options.port ?? 0);
  } catch (e) {
    // The port kept from last time is taken by something else: any free one will do, and the caller keeps it next time.
    if ((e as NodeJS.ErrnoException).code !== 'EADDRINUSE' || !options.port) throw e;
    port = await listen(server, 0);
  }
  return {
    origin: `http://127.0.0.1:${port}`,
    port,
    retarget: (next) => { api = new URL(next); },
    close: () => new Promise((resolve) => {
      server.close(() => resolve());
      server.closeAllConnections();
      for (const s of tunnels) s.destroy();
    }),
  };
}
