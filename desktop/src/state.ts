/* What the app remembers between launches, in one small JSON file in its user-data folder: the window's size
   and place, whether it was full size, and the port its page was served on (kept, because the page's own
   browser storage — open tabs, sidebar widths, the last folder a picker used — belongs to that origin). */
import { readFileSync, writeFileSync, renameSync } from 'node:fs';
import path from 'node:path';

export interface Bounds { x: number; y: number; width: number; height: number }

export interface Remembered {
  bounds?: Bounds;
  maximized?: boolean;
  port?: number;
}

const isBounds = (b: unknown): b is Bounds => !!b && typeof b === 'object'
  && ['x', 'y', 'width', 'height'].every((k) => Number.isFinite((b as Record<string, unknown>)[k]));

export function recall(dir: string): Remembered {
  try {
    const raw = JSON.parse(readFileSync(path.join(dir, 'window.json'), 'utf8')) as Record<string, unknown>;
    return {
      bounds: isBounds(raw.bounds) ? raw.bounds : undefined,
      maximized: raw.maximized === true,
      port: Number.isInteger(raw.port) && (raw.port as number) > 1024 && (raw.port as number) < 65536 ? raw.port as number : undefined,
    };
  } catch {
    // First launch, or a file from a hand edit that no longer parses: the defaults are the answer either way.
    return {};
  }
}

export function keep(dir: string, state: Remembered): void {
  const file = path.join(dir, 'window.json');
  try {
    // Written beside and moved into place, so a crash mid-write never leaves half a file for the next launch.
    writeFileSync(`${file}.tmp`, JSON.stringify(state, null, 2));
    renameSync(`${file}.tmp`, file);
  } catch (e) {
    console.warn('[NeuroCode] the window state was not kept:', e);
  }
}

/** Bounds that still fit one of the displays; a window last seen on a monitor since unplugged starts centred. */
export function onScreen(bounds: Bounds | undefined, displays: { x: number; y: number; width: number; height: number }[]): Bounds | undefined {
  if (!bounds) return undefined;
  const visible = displays.some((d) => bounds.x + 80 < d.x + d.width && bounds.x + bounds.width - 80 > d.x
    && bounds.y >= d.y - 10 && bounds.y + 40 < d.y + d.height);
  return visible ? bounds : undefined;
}
