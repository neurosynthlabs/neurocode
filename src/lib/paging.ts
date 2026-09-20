/**
 * Walking a paged list.
 *
 * The server answers at most PAGE_MAX rows at a time and says nothing about how many are left, so a
 * whole collection is read by asking for pages until one comes back short. This is the loop on its
 * own, with the fetching handed in, because it is the part that can be got wrong: stopping one page
 * early loses rows silently, and stopping one page late says a workspace is capped when it is not.
 */

/** A list as far as it could be read, and whether the ceiling stopped it. */
export interface Walked<T> {
  items: T[];
  /** The walk stopped at `loadCap` with rows still behind it, so what screens hold is not all there is. */
  capped: boolean;
}

export interface Walk {
  /** The most rows one page holds. A page shorter than this is the end of the list. */
  pageMax: number;
  /** The most rows one walk reads. */
  loadCap: number;
  /** How many pages are asked for at a time once the first one comes back full. */
  pagesAtOnce: number;
}

/**
 * Every page, until a short page says there is no more, or the ceiling.
 *
 * The first page is asked for on its own, because most workspaces fit in it and one request is all
 * they should cost. Once it comes back full the rest are asked for several at a time: a collection at
 * the ceiling was ten round trips one behind the other, and signing in waits on the slowest of five
 * such collections before the first screen is drawn.
 *
 * Paging by offset over a table that is being written can repeat a row when one is added between two
 * pages, so rows are kept once each, in the order they came.
 */
export async function walkPages<T extends { id: string }>(
  fetchPage: (offset: number, limit: number) => Promise<T[]>,
  { pageMax, loadCap, pagesAtOnce }: Walk,
): Promise<Walked<T>> {
  const found = new Map<string, T>();
  const keep = (rows: T[]) => rows.forEach((r) => { if (!found.has(r.id)) found.set(r.id, r); });
  const whole = (): Walked<T> => ({ items: [...found.values()], capped: false });

  const first = await fetchPage(0, pageMax);
  keep(first);
  if (first.length < pageMax) return whole();

  for (let offset = pageMax; offset < loadCap; offset += pageMax * pagesAtOnce) {
    const offsets: number[] = [];
    for (let n = 0; n < pagesAtOnce && offset + n * pageMax < loadCap; n += 1) offsets.push(offset + n * pageMax);
    const pages = await Promise.all(offsets.map((o) => fetchPage(o, pageMax)));
    pages.forEach(keep);
    // A page that came back short is the end of the list: the ones asked for beside it are empty or past it.
    if (pages.some((rows) => rows.length < pageMax)) return whole();
  }
  // A last page that was exactly full proves nothing: one row past the ceiling says whether any is left.
  return { items: [...found.values()], capped: (await fetchPage(loadCap, 1)).length > 0 };
}
