// What the browser checks share: a context signed in as the stack's owner, and knowing when a screen has
// finished loading.
//
// `networkidle` never arrives in this app — the activity stream holds a connection open for as long as
// the page lives — so a screen counts as loaded when nothing on it is marked busy and it has said
// something, or after 15 s, which the caller reports.

/** A browser context carrying the owner's session cookie for the stack's web origin. */
export async function signedIn(browser, stack, options = {}) {
  const ctx = await browser.newContext(options);
  await ctx.addCookies([{ name: 'nc_session', value: stack.token, url: stack.web, httpOnly: true, sameSite: 'Lax' }]);
  return ctx;
}

/** Waits for the screen to settle, then describes it.
 *
 * Settled means: past the splash, nothing marked busy, something said — and still so a moment later,
 * because a screen passes through instants that look finished (the splash before the shell, the shell
 * before its route's code has loaded). */
export async function settle(page, ms = 15000) {
  const loaded = () => page.evaluate(() => {
    const text = document.body.innerText || '';
    if (/Opening your workspace…/.test(text) || document.querySelector('[aria-busy="true"]')) return false;
    return ((document.querySelector('main') ?? document.body).innerText || '').trim().length > 0;
  }).catch(() => false);
  let streak = 0;
  for (const t0 = Date.now(); Date.now() - t0 < ms && streak < 2; await page.waitForTimeout(200)) {
    streak = (await loaded()) ? streak + 1 : 0;
  }
  // Screens with data of their own read it after the shell does; give those reads a moment to land.
  if (streak >= 2) await page.waitForTimeout(250);
  return page.evaluate(() => {
    const main = document.querySelector('main') ?? document.body;
    const text = (main.innerText || '').trim();
    return {
      text: text.length,
      busy: !!document.querySelector('[aria-busy="true"]'),
      offline: /^Not connected$/m.test(document.body.innerText || ''),
      stub: /Pending build|has not been implemented/i.test(text),
      crashed: /This screen crashed/.test(text),
      overflowX: document.documentElement.scrollWidth - window.innerWidth,
    };
  });
}
