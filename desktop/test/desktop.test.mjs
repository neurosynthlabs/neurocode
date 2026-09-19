// The desktop app's own server and helpers, against real sockets on 127.0.0.1: the built web app served with
// /api proxied beside it (plain requests, the change stream, WebSockets), the host check, the path guard, the
// port kept between launches, the health probe, neurocode:// links and the remembered window.
//   node desktop/scripts/prepare.mjs && node --test desktop/test/
import assert from 'node:assert/strict';
import fs from 'node:fs';
import http from 'node:http';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { after, before, test } from 'node:test';
import { startAppServer } from '../app/out/server.js';
import { answers, freePort } from '../app/out/stack.js';
import { routeOf } from '../app/out/links.js';
import { keep, onScreen, recall } from '../app/out/state.js';

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'nc-desktop-test-'));
const webRoot = path.join(tmp, 'web');
fs.mkdirSync(path.join(webRoot, 'assets'), { recursive: true });
fs.writeFileSync(path.join(webRoot, 'index.html'), '<!doctype html><title>NeuroCode</title><div id="root"></div>');
fs.writeFileSync(path.join(webRoot, 'assets', 'index-abc123.js'), 'console.log(1)');
fs.writeFileSync(path.join(tmp, 'secret.txt'), 'outside the web root');

/** A stand-in for the API: says what it was asked, streams events on /changes/stream, echoes a WebSocket. */
const seen = [];
const api = http.createServer((req, res) => {
  let body = '';
  req.on('data', (d) => { body += d; });
  req.on('end', () => {
    seen.push({ method: req.method, url: req.url, body, host: req.headers.host, client: req.headers['x-nc-client'] });
    if (req.url === '/changes/stream') {
      res.writeHead(200, { 'content-type': 'text/event-stream' });
      res.write('event: hello\ndata: {"n":1}\n\n');
      return; // held open, as the real stream is
    }
    res.writeHead(201, { 'content-type': 'application/json', 'set-cookie': 'nc_session=abc; HttpOnly; Path=/' });
    res.end(JSON.stringify({ path: req.url, method: req.method }));
  });
});
api.on('upgrade', (req, socket) => {
  seen.push({ upgrade: req.url, origin: req.headers.origin });
  socket.write('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n');
  socket.on('data', (d) => socket.write(Buffer.concat([Buffer.from('echo:'), d])));
});

let apiUrl;
let server;
before(async () => {
  await new Promise((r) => api.listen(0, '127.0.0.1', r));
  apiUrl = `http://127.0.0.1:${api.address().port}`;
  server = await startAppServer({ root: webRoot, api: apiUrl });
});
after(async () => {
  await server.close();
  api.closeAllConnections();
  await new Promise((r) => api.close(r));
  fs.rmSync(tmp, { recursive: true, force: true });
});

/** A raw request, so the Host header and odd paths go exactly as written. */
function raw(pathname, { method = 'GET', host, headers = {}, body } = {}) {
  return new Promise((resolve, reject) => {
    const req = http.request({ host: '127.0.0.1', port: server.port, path: pathname, method,
      headers: { host: host ?? `127.0.0.1:${server.port}`, ...headers } }, (res) => {
      let text = '';
      res.on('data', (d) => { text += d; });
      res.on('end', () => resolve({ status: res.statusCode, headers: res.headers, text }));
    });
    req.on('error', reject);
    if (body) req.write(body);
    req.end();
  });
}

test('serves the app, a screen of it, and its assets', async () => {
  const home = await raw('/');
  assert.equal(home.status, 200);
  assert.match(home.text, /<title>NeuroCode<\/title>/);
  assert.equal(home.headers['cache-control'], 'no-cache');
  assert.equal(home.headers['content-type'], 'text/html; charset=utf-8');

  const screen = await raw('/projects/erp?tab=sources');
  assert.equal(screen.status, 200);
  assert.match(screen.text, /id="root"/);

  const asset = await raw('/assets/index-abc123.js');
  assert.equal(asset.status, 200);
  assert.equal(asset.text, 'console.log(1)');
  assert.equal(asset.headers['content-type'], 'text/javascript; charset=utf-8');
  assert.match(asset.headers['cache-control'], /immutable/);

  // A missing asset is a 404, never the app's HTML under a script's name.
  const missing = await raw('/assets/index-gone.js');
  assert.equal(missing.status, 404);
  assert.equal((await raw('/', { method: 'POST' })).status, 405);
});

test('never serves a file outside the web root', async () => {
  for (const p of ['/../secret.txt', '/%2e%2e/secret.txt', '/assets/..%2f..%2fsecret.txt', '/..%5csecret.txt']) {
    const r = await raw(p);
    assert.doesNotMatch(r.text, /outside the web root/, p);
  }
  assert.equal((await raw('/%00')).status, 400);
});

test('answers only this machine by its own names', async () => {
  assert.equal((await raw('/', { host: 'evil.example:80' })).status, 403);
  assert.equal((await raw('/api/health', { host: `rebound.example:${server.port}` })).status, 403);
  assert.equal((await raw('/', { host: `localhost:${server.port}` })).status, 200);
});

test('proxies /api to the API with the path, body, headers and cookie intact', async () => {
  seen.length = 0;
  const r = await raw('/api/plans/compile?project=erp', {
    method: 'POST', body: '{"text":"hi"}', headers: { 'content-type': 'application/json', 'x-nc-client': 'web' },
  });
  assert.equal(r.status, 201);
  assert.deepEqual(JSON.parse(r.text), { path: '/plans/compile?project=erp', method: 'POST' });
  assert.equal(r.headers['set-cookie'][0], 'nc_session=abc; HttpOnly; Path=/');
  assert.equal(seen[0].body, '{"text":"hi"}');
  assert.equal(seen[0].client, 'web');

  const bare = await raw('/api');
  assert.equal(JSON.parse(bare.text).path, '/');
  // /apiary is a screen name, not the API.
  const notApi = await raw('/apiary');
  assert.match(notApi.text, /id="root"/);
});

test('streams the change stream as it is written', async () => {
  const first = await new Promise((resolve, reject) => {
    const req = http.get({ host: '127.0.0.1', port: server.port, path: '/api/changes/stream' }, (res) => {
      res.once('data', (d) => { resolve({ type: res.headers['content-type'], chunk: String(d) }); req.destroy(); });
    });
    req.on('error', (e) => { if (e.code !== 'ECONNRESET') reject(e); });
  });
  assert.equal(first.type, 'text/event-stream');
  assert.match(first.chunk, /event: hello/);
});

test('tunnels a WebSocket under /api with its path rewritten', async () => {
  seen.length = 0;
  const reply = await new Promise((resolve, reject) => {
    const s = net.connect(server.port, '127.0.0.1', () => {
      s.write(`GET /api/terminal/t1/ws HTTP/1.1\r\nHost: 127.0.0.1:${server.port}\r\nOrigin: http://127.0.0.1:${server.port}\r\n`
        + 'Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n\r\n');
    });
    let got = '';
    s.on('data', (d) => {
      got += d;
      if (got.includes('\r\n\r\n') && !got.includes('echo:')) s.write('ping');
      if (got.includes('echo:ping')) { s.destroy(); resolve(got); }
    });
    s.on('error', reject);
  });
  assert.match(reply, /^HTTP\/1\.1 101/);
  assert.equal(seen[0].upgrade, '/terminal/t1/ws');
  assert.equal(seen[0].origin, `http://127.0.0.1:${server.port}`);

  // A socket that is not the API's, or from another host, is refused.
  const refused = await new Promise((resolve) => {
    const s = net.connect(server.port, '127.0.0.1', () => {
      s.write(`GET /somewhere HTTP/1.1\r\nHost: 127.0.0.1:${server.port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n`);
    });
    let got = '';
    s.on('data', (d) => { got += d; });
    s.on('close', () => resolve(got));
  });
  assert.match(refused, /^HTTP\/1\.1 403/);
});

test('says so in JSON when the API is not answering, and follows it to a new port', async () => {
  const dead = await freePort();
  server.retarget(`http://127.0.0.1:${dead}`);
  const r = await raw('/api/health');
  assert.equal(r.status, 502);
  assert.match(JSON.parse(r.text).detail, /not answering/);
  server.retarget(apiUrl);
  assert.equal((await raw('/api/health')).status, 201);
});

test('keeps the port it was given, and takes another when that one is busy', async () => {
  const wanted = await freePort();
  const one = await startAppServer({ root: webRoot, api: apiUrl, port: wanted });
  assert.equal(one.port, wanted);
  const two = await startAppServer({ root: webRoot, api: apiUrl, port: wanted });
  assert.notEqual(two.port, wanted);
  await one.close();
  await two.close();
});

test('recognises a NeuroCode API by its /health', async () => {
  const health = http.createServer((req, res) => {
    if (req.url === '/health') { res.writeHead(200, { 'content-type': 'application/json' }); res.end('{"ok":true,"db":"x"}'); return; }
    res.writeHead(404).end();
  });
  await new Promise((r) => health.listen(0, '127.0.0.1', r));
  assert.equal(await answers(`http://127.0.0.1:${health.address().port}`), true);
  // Something else on the port (here, the stand-in that answers every path with another shape) is not it.
  assert.equal(await answers(apiUrl), false);
  assert.equal(await answers(`http://127.0.0.1:${await freePort()}`, 500), false);
  await new Promise((r) => health.close(r));
});

test('reads neurocode:// links as routes of the app, and nothing else', () => {
  assert.equal(routeOf('neurocode:///workbench?project=erp'), '/workbench?project=erp');
  assert.equal(routeOf('neurocode://workbench?folder=%2FUsers%2Fa'), '/workbench?folder=%2FUsers%2Fa');
  assert.equal(routeOf('neurocode://'), '/');
  assert.equal(routeOf('https://example.com/x'), null);
  assert.equal(routeOf('not a url'), null);
});

test('remembers the window, and forgets a place no display shows any more', () => {
  const dir = fs.mkdtempSync(path.join(tmp, 'state-'));
  assert.deepEqual(recall(dir), {});
  keep(dir, { bounds: { x: 10, y: 20, width: 1200, height: 800 }, maximized: false, port: 5188 });
  assert.deepEqual(recall(dir), { bounds: { x: 10, y: 20, width: 1200, height: 800 }, maximized: false, port: 5188 });
  fs.writeFileSync(path.join(dir, 'window.json'), '{"port": 80, "bounds": {"x": "a"}}');
  assert.deepEqual(recall(dir), { bounds: undefined, maximized: false, port: undefined });
  const display = [{ x: 0, y: 0, width: 1440, height: 900 }];
  assert.ok(onScreen({ x: 100, y: 100, width: 800, height: 600 }, display));
  assert.equal(onScreen({ x: 3000, y: 100, width: 800, height: 600 }, display), undefined);
});
