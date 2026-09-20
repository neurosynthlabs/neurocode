/* NeuroCode's service worker: the shell, cached, and never a word about the data.
 *
 * The web app holds no data of its own — every figure on every screen came from the API a moment
 * ago. So the one thing this must never do is answer an API call from a cache: a cached run status
 * or a cached inbox is a lie told confidently, which is the single thing this product does not do.
 * `/api/*` is therefore passed straight through, and when the network is gone the request fails and
 * the app says "Not connected", exactly as it does in a browser tab with the API stopped.
 *
 * What is cached is the shell: the HTML, the fingerprinted chunks under /assets, and the icons. That
 * is what makes it open from the home screen on a train — the app appears, and then tells the truth
 * about not being able to reach its server.
 *
 * Navigations go to the network first and only fall back to the cached page, because a cached
 * index.html names chunk hashes that a deploy has replaced; asking the network first means a
 * connected phone is always on the current build.
 *
 * It is an ES module on purpose: the routing rule below is the whole of the interesting part, and as
 * a module it can be imported and tested by itself (src/lib/__tests__/pwa.test.mjs) rather than only
 * being watched in a browser.
 */

//: Bump this when what is precached changes; the old caches are deleted on activation.
export const VERSION = 'v1';
export const SHELL_CACHE = `neurocode-shell-${VERSION}`;
export const ASSET_CACHE = `neurocode-assets-${VERSION}`;

//: The page every navigation falls back to. One entry, because the app is one page.
export const SHELL = '/';

//: Fetched on install, so the very first flight already has something to open.
export const PRECACHE = [SHELL, '/manifest.webmanifest', '/neurocode.svg', '/icon-192.png', '/icon-512.png'];

/* Files that keep their name across deploys, so their cached copy may be stale and has to be
   refreshed in the background. Everything under /assets carries a content hash instead and can be
   answered from the cache outright. */
export const UNHASHED = new Set([
  '/manifest.webmanifest', '/neurocode.svg', '/apple-touch-icon.png', '/og.png',
  '/icon-192.png', '/icon-512.png', '/icon-maskable-512.png',
]);

/**
 * What to do with one request. `pass` means this worker does not answer it at all — the browser
 * makes the request it would have made without a service worker in the way.
 *
 * Takes the three fields it needs rather than a Request, so the rule can be read back in a test
 * without a browser to make Requests with.
 */
export function plan({ method, url, mode }, origin) {
  if (method !== 'GET') return 'pass';
  let where;
  try {
    where = new URL(url, origin);
  } catch {
    return 'pass';
  }
  if (where.origin !== origin) return 'pass';
  // The API, and the WebSockets and event streams that live under it. Never cached, never replayed.
  if (where.pathname === '/api' || where.pathname.startsWith('/api/')) return 'pass';
  if (mode === 'navigate') return 'shell';
  if (where.pathname.startsWith('/assets/')) return 'asset';
  if (UNHASHED.has(where.pathname)) return 'refresh';
  return 'pass';
}

/** Which caches this version keeps; anything else belongs to a version that is gone. */
export const KEEP = new Set([SHELL_CACHE, ASSET_CACHE]);
export const mine = (name) => name.startsWith('neurocode-');
export const stale = (names) => names.filter((name) => mine(name) && !KEEP.has(name));

/* The last resort: the shell was never cached and the network is not there. Said in the same voice
   the app uses when it cannot reach its API, because it is the same situation one step earlier. */
export const OFFLINE_PAGE = `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>NeuroCode — offline</title><style>
 html,body{height:100%;margin:0;background:#0b0d11;color:#e8eaed;
   font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
 main{height:100%;display:grid;place-items:center;padding:24px;text-align:center}
 h1{font-size:17px;font-weight:600;margin:0 0 8px}p{margin:0;color:#9aa0a6;max-width:34ch}
</style></head><body><main><div><h1>NeuroCode is offline</h1>
<p>This phone has no connection, and nothing of your workspace is kept here. Reconnect and open it again.</p>
</div></main></body></html>`;

const scope = typeof self === 'undefined' ? null : self;

if (scope && typeof scope.addEventListener === 'function') {
  scope.addEventListener('install', (event) => {
    // A precache entry that 404s must not take the whole install down with it, so each is added on
    // its own and a failure is only that one file missing from the first flight.
    event.waitUntil((async () => {
      const shelf = await caches.open(SHELL_CACHE);
      await Promise.all(PRECACHE.map((path) => shelf.add(path).catch(() => {})));
      await scope.skipWaiting();
    })());
  });

  scope.addEventListener('activate', (event) => {
    event.waitUntil((async () => {
      const names = await caches.keys();
      await Promise.all(stale(names).map((name) => caches.delete(name)));
      await scope.clients.claim();
    })());
  });

  scope.addEventListener('fetch', (event) => {
    const { request } = event;
    const what = plan({ method: request.method, url: request.url, mode: request.mode }, scope.location.origin);
    if (what === 'pass') return;
    if (what === 'shell') event.respondWith(navigation(request));
    if (what === 'asset') event.respondWith(fromCache(request, ASSET_CACHE));
    if (what === 'refresh') event.respondWith(refreshing(request, SHELL_CACHE));
  });

  // The page asks for the new version the moment one is waiting, rather than on the next cold start.
  scope.addEventListener('message', (event) => {
    if (event.data === 'nc:skip-waiting') scope.skipWaiting();
  });
}

async function navigation(request) {
  const shelf = await caches.open(SHELL_CACHE);
  try {
    const answer = await fetch(request);
    // Only a real page is kept: a proxy's error page cached as the shell would outlive the outage.
    if (answer.ok) await shelf.put(SHELL, answer.clone());
    return answer;
  } catch {
    return (await shelf.match(SHELL)) ?? new Response(OFFLINE_PAGE, {
      status: 200, headers: { 'Content-Type': 'text/html; charset=utf-8' },
    });
  }
}

async function fromCache(request, where) {
  const shelf = await caches.open(where);
  const kept = await shelf.match(request);
  if (kept) return kept;
  const answer = await fetch(request);
  if (answer.ok) await shelf.put(request, answer.clone());
  return answer;
}

async function refreshing(request, where) {
  const shelf = await caches.open(where);
  const kept = await shelf.match(request);
  const asking = fetch(request).then((answer) => {
    if (answer.ok) shelf.put(request, answer.clone());
    return answer;
  });
  return kept ?? asking;
}
