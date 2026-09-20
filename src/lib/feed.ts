/**
 * The activity feed's ceiling in the tab.
 *
 * The feed loads the server's newest page and then grows on the stream — one line per thing anyone or
 * any agent does — so on a busy day it had no ceiling at all: it rebuilt a list that only ever got
 * longer, rescanned it for every arriving id, and put all of it in the DOM. Nothing is lost by cutting
 * it: the log is whole in the database, and Activity says how much of it the screen is showing.
 */
export const FEED_CAP = 1_000;

/**
 * One line added to the newest-first feed, once. The stream and a catch-up load can both carry the same
 * line, so a line already held is not added again — and the feed is never longer than its ceiling.
 */
export function addEvent<T extends { id: string }>(feed: T[], event: T, cap = FEED_CAP): T[] {
  if (feed.some((e) => e.id === event.id)) return feed;
  const next = [event, ...feed];
  return next.length > cap ? next.slice(0, cap) : next;
}
