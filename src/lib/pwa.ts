/* Installing NeuroCode on a phone.
 *
 * Two things make the app installable: the manifest in public/, which names it and its icons, and
 * the service worker in public/sw.js, which keeps the shell so it opens without a network. This file
 * is the decision about whether that worker belongs on this page at all, and the small amount of
 * lifecycle around it.
 *
 * The decision matters more than it looks. A service worker outlives the tab that registered it and
 * is scoped to an origin, not to a build — so one registered by `vite preview` on localhost:5180
 * goes on intercepting the dev server on the same port the next morning, serving yesterday's chunks
 * and looking like a bug in whatever you were working on. And the desktop app serves this same page
 * from its own local server, updating when the app updates; a worker in there would quietly become a
 * second, older copy of the product. So the rule is written down, tested, and — this is the part
 * that is easy to forget — it removes a worker that should not be here as well as registering the
 * one that should.
 */

/** Everything the rule below looks at, passed in rather than read, so it can be read back in a test. */
export interface Surroundings {
  /** Whether this browser has service workers at all. */
  supported: boolean;
  /** The Vite dev server, where every file must come fresh from Vite. */
  dev: boolean;
  /** Inside the Electron desktop app, which serves this page itself. */
  desktop: boolean;
  /** HTTPS, or localhost, which counts. Service workers are refused anywhere else. */
  secure: boolean;
  /** A browser being driven by a check (`navigator.webdriver`). */
  automated: boolean;
}

/** `register` puts the worker on; `remove` takes off one that should not be here; `nothing` leaves it alone. */
export interface Verdict {
  act: 'register' | 'remove' | 'nothing';
  /** Why, in a sentence — this is what goes in the console, and what a person can act on. */
  why: string;
}

export function verdict({ supported, dev, desktop, secure, automated }: Surroundings): Verdict {
  if (!supported) {
    return { act: 'nothing', why: 'This browser has no service workers, so NeuroCode runs as an ordinary page.' };
  }
  if (desktop) {
    return {
      act: 'remove',
      why: 'The desktop app serves this page from its own local server and updates with the app, so a service worker here would only ever be an older copy of it.',
    };
  }
  if (dev) {
    return { act: 'remove', why: 'In development every file comes from Vite, so nothing is cached in front of it.' };
  }
  if (automated) {
    // The browser checks (scripts/smoke.mjs, layout-lint, e2e) run against a real production build
    // on localhost, which is everything a worker asks for. They are there to see what the build
    // serves; a cache in front of it would be a second thing under test, and the first flaky one.
    return { act: 'nothing', why: 'This browser is being driven by a check, which should see the build itself.' };
  }
  if (!secure) {
    return {
      act: 'nothing',
      why: 'Installing needs HTTPS (localhost counts). Served over plain http from another machine, NeuroCode still works — it just cannot be added to a home screen.',
    };
  }
  return { act: 'register', why: 'The shell is kept so the app opens without a network. Your workspace is never cached.' };
}

/** What `verdict` is looking at, read off this page. */
export function surroundings(): Surroundings {
  return {
    supported: typeof navigator !== 'undefined' && 'serviceWorker' in navigator,
    dev: import.meta.env.DEV,
    desktop: typeof window !== 'undefined' && window.neurocode != null,
    secure: typeof window !== 'undefined' && window.isSecureContext,
    automated: typeof navigator !== 'undefined' && navigator.webdriver === true,
  };
}

/**
 * Whether a worker taking over this page means the page has to be read again.
 *
 * Only when there was an older one to replace. The very first install claims the page it was
 * registered from — that is what `clients.claim()` is for — and a page that reloaded on that would
 * flash white on every first visit, for a worker that has changed nothing about what it is showing.
 * The second guard is the one that matters more: a deploy that cannot settle must not become a tab
 * that reloads for ever.
 */
export function shouldReload(hadController: boolean, alreadyReloading: boolean): boolean {
  return hadController && !alreadyReloading;
}

let reloading = false;

function takeOver(registration: ServiceWorkerRegistration): void {
  const hadController = navigator.serviceWorker.controller != null;
  navigator.serviceWorker.addEventListener('controllerchange', () => {
    if (!shouldReload(hadController, reloading)) return;
    reloading = true;
    window.location.reload();
  });
  registration.addEventListener('updatefound', () => {
    const arriving = registration.installing;
    if (!arriving) return;
    arriving.addEventListener('statechange', () => {
      // `controller` is null on the very first install: there is no older version to replace, and
      // reloading a page that is already the new one would be a flash for nothing.
      if (arriving.state === 'installed' && navigator.serviceWorker.controller) {
        arriving.postMessage('nc:skip-waiting');
      }
    });
  });
}

/**
 * Called once from main.tsx. Never throws: a browser that refuses to register a worker — a private
 * window, a policy, a stale registration — is a browser the app still has to run in.
 */
export async function installServiceWorker(where: Surroundings = surroundings()): Promise<Verdict> {
  const answer = verdict(where);
  try {
    if (answer.act === 'register') {
      const registration = await navigator.serviceWorker.register('/sw.js', { type: 'module', scope: '/' });
      takeOver(registration);
    } else if (answer.act === 'remove') {
      for (const registration of await navigator.serviceWorker.getRegistrations()) {
        await registration.unregister();
      }
    }
  } catch (e) {
    return { act: 'nothing', why: `The service worker could not be set up, so nothing is cached: ${String(e)}` };
  }
  return answer;
}
