/* neurocode:// links: how a link opened anywhere on the Mac (a terminal's `open`, `nc open`, a note) names a
   screen of the app. Kept apart from main.ts so it can be tested without Electron. */

/** neurocode:///workbench?x=y and neurocode://workbench?x=y both mean the route /workbench?x=y; anything else is null. */
export function routeOf(link: string): string | null {
  let url: URL;
  try {
    url = new URL(link);
  } catch {
    return null;
  }
  if (url.protocol !== 'neurocode:') return null;
  const where = url.host ? `/${url.host}${url.pathname}` : url.pathname || '/';
  const route = `${where}${url.search}`.replace(/^\/+/, '/');
  return isRoute(route) ? route : null;
}

/** A path inside the app: it begins with one slash — never `//host`, which a browser reads as another site. */
export function isRoute(route: string): boolean {
  return route.startsWith('/') && !route.startsWith('//') && !route.includes('\\') && route.length <= 4000;
}
