import { useEffect, type MouseEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { toast } from 'sonner';
import { joinPath } from '@/lib/live/machine';
import type { ProjectSource } from '@/lib/live/sources';

/* The desktop app's bridge. In the NeuroCode desktop app (desktop/, Electron) the page gets `window.neurocode`
   from its preload: the native folder and file dialogs, Reveal in Finder, the person's own editor, native
   notifications, the Dock badge and the app's menus. In a browser there is no bridge, and every caller here
   keeps the web behaviour — so each use is one guarded line, and nothing in the web app depends on it.

   The bridge acts on the disk of the Mac the app runs on. The desktop app only ever talks to an API on that same
   Mac (it starts one, or finds the one already running), so a path from the API is a path the bridge can open. */

export interface DesktopPickOptions {
  title?: string;
  /** Where the dialog opens; ignored when it is not a folder on this Mac. */
  start?: string | null;
  confirmLabel?: string;
  /** File dialogs: the endings a file may have, e.g. ['.zip', '.tar.gz']. */
  accept?: string[];
}

/** `window.neurocode`, exactly as desktop/src/preload.cts exposes it. */
export interface DesktopBridge {
  /** process.platform of the desktop app: 'darwin' on a Mac. */
  platform: string;
  /** The desktop app's version (package.json's, as it was built). */
  version: string;
  /** The native folder dialog; null when the person cancels. */
  pickFolder: (options?: DesktopPickOptions) => Promise<string | null>;
  /** The native file dialog; null when the person cancels. */
  pickFile: (options?: DesktopPickOptions) => Promise<string | null>;
  revealInFinder: (path: string) => Promise<void>;
  /** Opens the file (at the line, when the editor can) in VS Code, Cursor, Windsurf or the default text editor; says which. */
  openInEditor: (path: string, line?: number) => Promise<string>;
  /** The native menu for a file: Open in <editor>, Reveal in Finder, Copy Path — with Cut/Copy/Paste first when `edit`. */
  showPathMenu: (path: string, options?: { line?: number; edit?: boolean }) => Promise<void>;
  /** A native notification; a click brings the window forward, on `route` when one is given. */
  notify: (title: string, body: string, options?: { route?: string }) => void;
  /** The number on the Dock icon; 0 clears it. */
  setBadge: (count: number) => void;
  /** A menu item, a neurocode:// link or a notification asked for a screen of the app. Returns the unsubscribe. */
  onNavigate: (listener: (route: string) => void) => () => void;
}

declare global {
  interface Window {
    /** Present only inside the NeuroCode desktop app. */
    neurocode?: DesktopBridge;
  }
}

/** The bridge, or null in a browser. */
export const desktop: DesktopBridge | null = typeof window !== 'undefined' ? window.neurocode ?? null : null;
export const isDesktop = desktop !== null;
/** What the Mac calls its file browser, for labels ("Reveal in Finder"); the same words elsewhere name the folder. */
export const FILE_BROWSER = desktop && desktop.platform !== 'darwin' ? 'Show in Folder' : 'Reveal in Finder';

const said = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** Reveal a file or folder in the Finder. A no-op in a browser. */
export function revealInFinder(path: string): void {
  desktop?.revealInFinder(path).catch((e: unknown) => toast.error('Not shown in the Finder', { description: said(e) }));
}

/** Open a file in the person's own editor. A no-op in a browser. */
export function openInEditor(path: string, line?: number): void {
  desktop?.openInEditor(path, line).then(
    (editor) => toast(`Opened in ${editor}`),
    (e: unknown) => toast.error('The file did not open in the editor', { description: said(e) }),
  );
}

/**
 * A right click on a file or folder of this Mac: in the desktop app, the native menu (Open in editor, Reveal in
 * Finder, Copy Path) instead of none; in a browser the event is left alone.
 */
export function onPathMenu(e: MouseEvent, path: string | null | undefined, options?: { line?: number; edit?: boolean }): void {
  if (!desktop || !path) return;
  e.preventDefault();
  desktop.showPathMenu(path, options).catch((err: unknown) => toast.error('No menu for this file', { description: said(err) }));
}

/**
 * Where a path of a project's code index is on this Mac: under the further source whose label begins it
 * (`api/app/main.py`), else under the first source — the same rule the Workbench opens a session's links by.
 * Null when that source has no folder here (a clone elsewhere, or a person who may not browse the machine).
 */
export function absoluteIn(sources: ProjectSource[], path: string): string | null {
  const clean = path.replace(/^\/+/, '');
  const extra = sources.find((s) => !s.primary && s.root && clean.startsWith(`${s.label}/`));
  if (extra?.root) return joinPath(extra.root, clean.slice(extra.label.length + 1));
  const primary = sources.find((s) => s.primary && s.root);
  return primary?.root ? joinPath(primary.root, clean) : null;
}

/**
 * The shell's side of the app, run by the sidebar that is always mounted (`on`; the phone drawer's copy passes
 * false): the approvals waiting go to the Dock badge, and the screens the desktop menus, neurocode:// links and
 * notifications ask for are navigated to. Nothing in a browser.
 */
export function useDesktopShell(approvalsWaiting: number, on: boolean): void {
  const navigate = useNavigate();
  useEffect(() => {
    if (on) desktop?.setBadge(approvalsWaiting);
  }, [approvalsWaiting, on]);
  // Signed out, the shell is gone and so is anything waiting for this person.
  useEffect(() => () => { if (on) desktop?.setBadge(0); }, [on]);
  useEffect(() => {
    if (!desktop || !on) return;
    return desktop.onNavigate((route) => {
      if (route.startsWith('/') && !route.startsWith('//')) navigate(route);
    });
  }, [navigate, on]);
}
