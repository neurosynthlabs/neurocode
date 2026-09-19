/// <reference lib="dom" />
/* The bridge the web app sees as `window.neurocode` — a few typed calls and nothing else. The page never gets
   ipcRenderer or Node: contextIsolation keeps this file's world apart from the page's, the sandbox keeps Node
   out of both, and every call here is answered in the main process, which checks that it came from the app's
   own page. src/lib/desktop.ts in the web app declares the same shape.

   The same window first shows the starting page (app/splash.html, from the app's own files) while the stack
   comes up; that page gets `window.splash` instead — what the app is doing, and "Try again" when it cannot. */
import { contextBridge, ipcRenderer, type IpcRendererEvent } from 'electron';

interface PickOptions { title?: string; start?: string | null; confirmLabel?: string; accept?: string[] }
interface SplashState { phase: 'starting' | 'problem'; title: string; detail: string; log: boolean }

/** Electron wraps a refusal as "Error invoking remote method 'nc:x': Error: <what was said>"; the page gets only what was said. */
async function call<T>(channel: string, ...args: unknown[]): Promise<T> {
  try {
    return await ipcRenderer.invoke(channel, ...args) as T;
  } catch (e) {
    const said = e instanceof Error ? e.message : String(e);
    throw new Error(said.replace(/^Error invoking remote method '[^']+': (Error: )?/, ''));
  }
}

const text = (v: unknown, max: number) => (typeof v === 'string' ? v.slice(0, max) : '');

function exposeSplash(): void {
  contextBridge.exposeInMainWorld('splash', Object.freeze({
    onState: (listener: (state: SplashState) => void) => {
      ipcRenderer.on('splash:state', (_e: IpcRendererEvent, state: SplashState) => listener(state));
      ipcRenderer.send('splash:ready');
    },
    retry: () => ipcRenderer.send('splash:retry'),
    showLog: () => ipcRenderer.send('splash:log'),
  }));
}

function exposeBridge(): void {
  const info = ipcRenderer.sendSync('nc:info') as { version: string; platform: string };
  contextBridge.exposeInMainWorld('neurocode', Object.freeze({
    platform: info.platform,
    version: info.version,
    pickFolder: (options: PickOptions = {}) => call<string | null>('nc:pick', { ...options, mode: 'folder' }),
    pickFile: (options: PickOptions = {}) => call<string | null>('nc:pick', { ...options, mode: 'file' }),
    revealInFinder: (path: string) => call<void>('nc:reveal', path),
    openInEditor: (path: string, line?: number) => call<string>('nc:edit', path, line),
    showPathMenu: (path: string, options: { line?: number; edit?: boolean } = {}) => call<void>('nc:path-menu', path, options),
    notify: (title: string, body: string, options: { route?: string } = {}) => {
      ipcRenderer.send('nc:notify', { title: text(title, 200), body: text(body, 1000), route: text(options.route, 2000) });
    },
    setBadge: (count: number) => {
      ipcRenderer.send('nc:badge', Number.isFinite(count) ? Math.max(0, Math.floor(count)) : 0);
    },
    onNavigate: (listener: (route: string) => void) => {
      const handler = (_e: IpcRendererEvent, route: unknown) => { if (typeof route === 'string') listener(route); };
      ipcRenderer.on('nc:navigate', handler);
      // A menu item chosen, or a neurocode:// link opened, before the page was listening is sent now.
      ipcRenderer.send('nc:navigate-ready');
      return () => { ipcRenderer.removeListener('nc:navigate', handler); };
    },
  }));
}

if (window.location.protocol === 'file:') exposeSplash();
else exposeBridge();
