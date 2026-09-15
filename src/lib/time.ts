/** How a time is shown.
 *
 * The API sends real timestamps — it has to, they are what the database sorts and filters by — while
 * the screens have always read like a person talking: "2 min ago", "3 h ago". This is the one place
 * that turns one into the other.
 *
 * It passes anything that is not a timestamp straight through, which is what lets the same component
 * render the live API and the seed data the public demo runs on without knowing which it is holding.
 */

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/** "just now" · "41 min ago" · "3 h ago" · "6 d ago" · "11 Sep" once it is older than a month. */
export function ago(value: string | null | undefined): string {
  if (!value) return '—';
  const at = new Date(value);
  if (Number.isNaN(at.getTime())) return value;     // already a phrase, or something we should not touch

  const elapsed = Date.now() - at.getTime();
  if (elapsed < 0) return on(at);                    // the future is a date, not a countdown
  if (elapsed < MINUTE) return 'just now';
  if (elapsed < HOUR) return `${Math.floor(elapsed / MINUTE)} min ago`;
  if (elapsed < DAY) return `${Math.floor(elapsed / HOUR)} h ago`;
  if (elapsed < 30 * DAY) return `${Math.floor(elapsed / DAY)} d ago`;
  return on(at);
}

/** "11 Sep" — or "11 Sep 2025" when it is not this year. */
function on(at: Date): string {
  const month = at.toLocaleString('en', { month: 'short' });
  const year = at.getFullYear() === new Date().getFullYear() ? '' : ` ${at.getFullYear()}`;
  return `${at.getDate()} ${month}${year}`;
}

/** "11 Sep, 18:40" — for the places that want the moment itself rather than how long ago it was. */
export function at(value: string | null | undefined): string {
  if (!value) return 'never';
  const moment = new Date(value);
  if (Number.isNaN(moment.getTime())) return value;
  return `${on(moment)}, ${moment.toTimeString().slice(0, 5)}`;
}
