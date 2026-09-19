import { useEffect, useRef, useSyncExternalStore } from 'react';
import { useData } from '@/lib/data';
import type { ChatEvent, RunDoc } from '@/lib/api';

/* "Notify me": a notification when something new needs you or finishes, while NeuroCode is in the
   background. Opt-in, and asked for on a click — never on load. The desktop app's bridge answers first
   when it is there (window.neurocode.notify); in a browser it is the Notification API. Everything it says
   comes from events the store already hears on the stream: a new pending approval, a run that ended, a
   session stopping at a permission card. The choice is this browser's own, kept in localStorage. */

const KEY = 'neurocode.notify';
const CHANGED = 'neurocode:notify-changed';

interface Bridge { notify?: (title: string, body: string, options?: { route?: string }) => unknown }
/** The desktop app's bridge, when this page runs inside it. Typed here so nothing depends on its declaration. */
const bridge = (): Bridge | undefined => (window as unknown as { neurocode?: Bridge }).neurocode;
const hasBridge = () => typeof bridge()?.notify === 'function';
const hasBrowser = () => typeof window !== 'undefined' && 'Notification' in window;

export type NotifyState = 'on' | 'off' | 'blocked' | 'unsupported';

function read(): NotifyState {
  if (!hasBridge() && !hasBrowser()) return 'unsupported';
  if (!hasBridge() && Notification.permission === 'denied') return 'blocked';
  let stored: string | null = null;
  try { stored = localStorage.getItem(KEY); } catch { /* private mode: off */ }
  if (stored !== 'on') return 'off';
  return hasBridge() || Notification.permission === 'granted' ? 'on' : 'off';
}

function write(on: boolean) {
  try { localStorage.setItem(KEY, on ? 'on' : 'off'); } catch { /* private mode: it lasts this page only */ }
  window.dispatchEvent(new Event(CHANGED));
}

function subscribe(cb: () => void) {
  window.addEventListener(CHANGED, cb);
  window.addEventListener('storage', cb);
  return () => {
    window.removeEventListener(CHANGED, cb);
    window.removeEventListener('storage', cb);
  };
}

/** Whether notifications are on here, and why not when they are not. */
export function useNotifyState(): NotifyState {
  return useSyncExternalStore(subscribe, read, () => 'unsupported');
}

/** Turn them on — asking the browser the first time, on the click that called this. */
export async function enableNotify(): Promise<NotifyState> {
  if (!hasBridge() && hasBrowser() && Notification.permission === 'default') await Notification.requestPermission();
  write(true);
  return read();
}

export function disableNotify() {
  write(false);
}

/** Say it: through the desktop app when it is there (which opens `route` on a click), else the browser.
    Silent when it is off. */
export function notify(title: string, body: string, tag?: string, route?: string) {
  if (read() !== 'on') return;
  const desktop = bridge();
  if (desktop?.notify) {
    void desktop.notify(title, body, route ? { route } : undefined);
    return;
  }
  try {
    const n = new Notification(title, { body, tag });
    n.onclick = () => { window.focus(); n.close(); };
  } catch (e) {
    console.error('[NeuroCode] a notification could not be shown:', e);
  }
}

const away = () => document.visibilityState !== 'visible' || !document.hasFocus();
const ENDED = new Set(['done', 'failed', 'cancelled']);
const ENDED_WORD: Record<string, string> = { done: 'finished', failed: 'failed', cancelled: 'was cancelled' };

/**
 * Watches the store and notifies about what is new while the app is in the background. Mounted once, in
 * the shell. What was already there when it started is remembered, never announced.
 */
export function useNotifier() {
  const { mode, approvals, runs, onChat } = useData();
  const state = useNotifyState();
  const known = useRef<{ gates: Set<string>; runs: Map<string, string> } | null>(null);

  useEffect(() => {
    if (mode !== 'live') return;
    const leads = runs.filter((r: RunDoc) => !r.parent);
    const pending = approvals.filter((a) => a.status === 'pending');
    const before = known.current;
    known.current = { gates: new Set(pending.map((a) => a.ref)), runs: new Map(leads.map((r) => [r.ref, r.status])) };
    if (!before || state !== 'on' || !away()) return;
    for (const a of pending) {
      if (before.gates.has(a.ref)) continue;
      const what = a.tool.startsWith('Ask(') ? `${a.agent || 'An agent'} asks` : a.tool.startsWith('Merge(') ? 'Ready for your signature' : 'Needs your decision';
      notify(what, `${a.ref} · ${a.title}`, `gate:${a.ref}`, '/permissions');
    }
    for (const r of leads) {
      const was = before.runs.get(r.ref);
      if (!ENDED.has(r.status) || (was !== undefined && ENDED.has(was)) || was === undefined) continue;
      notify(`${r.ref} ${ENDED_WORD[r.status]}`, r.requirement.slice(0, 140) || r.projectName, `run:${r.ref}`, `/runs?ref=${encodeURIComponent(r.ref)}`);
    }
  }, [mode, approvals, runs, state]);

  useEffect(() => onChat((m: ChatEvent) => {
    if (m.stream || m.tool !== 'permission' || m.permission?.state !== 'pending' || read() !== 'on' || !away()) return;
    notify(`${m.sessionRef} asks to use ${m.permission.tool}`, m.permission.subject.slice(0, 160), `card:${m.sessionRef}:${m.id}`,
      `/sessions?ref=${encodeURIComponent(m.sessionRef)}`);
  }), [onChat]);
}
