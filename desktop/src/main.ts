/* NeuroCode on the desktop: the same web app and API, in a window of its own, with what only a desktop has —
   the native folder dialog, Reveal in Finder, the person's own editor, native notifications, a Dock badge for
   the approvals waiting, menus and shortcuts.

   Development (`npm run desktop:dev`): the window loads the Vite dev server (NEUROCODE_DESKTOP_DEV_URL,
   http://localhost:5180), which already proxies /api to the running API.
   Otherwise: the stack is found or started (stack.ts), and the built web app is served with /api proxied beside
   it on one 127.0.0.1 origin (server.ts). */
import {
  BrowserWindow, Menu, Notification, app, clipboard, dialog, ipcMain, screen, session, shell,
  type IpcMainEvent, type IpcMainInvokeEvent, type MenuItemConstructorOptions, type WebContents,
} from 'electron';
import { existsSync, mkdirSync, readFileSync, statSync } from 'node:fs';
import path from 'node:path';
import { checkedPath, editor, openInEditor } from './editor.js';
import { isRoute, routeOf } from './links.js';
import { startAppServer, type AppServer } from './server.js';
import { StackProblem, adoptShellPath, findRunning, rotateLog, startApi, type Stack } from './stack.js';
import { keep, onScreen, recall, type Remembered } from './state.js';

const HERE = import.meta.dirname;            // desktop/app/out, or Resources/app.asar/out when packaged
const APP_DIR = path.resolve(HERE, '..');     // desktop/app
const SPLASH = path.join(APP_DIR, 'splash.html');
const SPLASH_URL = new URL(`file://${SPLASH}`).href;
const DEV_URL = process.env.NEUROCODE_DESKTOP_DEV_URL?.replace(/\/$/, '') || null;
const SMOKE = process.env.NEUROCODE_DESKTOP_SMOKE === '1';

// A separate profile (its own window state, cookies and storage), for the smoke run and for trying a build
// beside the app in daily use. Set before anything reads the user-data folder.
if (process.env.NEUROCODE_DESKTOP_PROFILE) {
  const profile = path.resolve(process.env.NEUROCODE_DESKTOP_PROFILE);
  app.setPath('userData', profile);
  app.setAppLogsPath(path.join(profile, 'logs'));
}
app.setName('NeuroCode');
// Development keeps a profile of its own, so it can run beside the installed app (one instance per profile).
if (DEV_URL && !process.env.NEUROCODE_DESKTOP_PROFILE) app.setPath('userData', path.join(app.getPath('appData'), 'NeuroCode Dev'));

/** Written by scripts/prepare.mjs: where the checkout that built this app is, and its version. */
function buildInfo(): { home?: string; version?: string } {
  try {
    return JSON.parse(readFileSync(path.join(APP_DIR, 'build.json'), 'utf8')) as { home?: string; version?: string };
  } catch {
    return {};
  }
}
/** The NeuroCode checkout holding server/: named by NEUROCODE_HOME, else the one this app was built from. */
const HOME = path.resolve(process.env.NEUROCODE_HOME || buildInfo().home || path.resolve(APP_DIR, '..', '..'));
/** The built web app: packed beside the app, or the checkout's own dist/ when run from source (NEUROCODE_DESKTOP_WEB names another build). */
const WEB_ROOT = process.env.NEUROCODE_DESKTOP_WEB
  ? path.resolve(process.env.NEUROCODE_DESKTOP_WEB)
  : app.isPackaged ? path.join(process.resourcesPath, 'web') : path.join(HOME, 'dist');

let win: BrowserWindow | null = null;
let origin: string | null = null;
let stack: Stack | null = null;
let web: AppServer | null = null;
let remembered: Remembered = {};
let splashState: { phase: 'starting' | 'problem'; title: string; detail: string; log: boolean } =
  { phase: 'starting', title: 'Opening…', detail: '', log: false };
/** A route asked for (a menu, a link, a notification) before the page was listening for one. */
let pendingRoute: string | null = null;
let routeListener = false;
let booting: Promise<void> | null = null;
let quitting = false;

const logFile = () => path.join(app.getPath('logs'), 'api.log');

/* ── one instance ─────────────────────────────────────────────── */
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', (_e, argv) => {
    const link = argv.find((a) => a.startsWith('neurocode://'));
    if (link) openLink(link);
    showWindow();
  });
  void app.whenReady().then(ready);
}

// A link opened from anywhere on the Mac — `open neurocode:///workbench?project=erp`, as `nc open` does.
app.on('open-url', (e, url) => {
  e.preventDefault();
  openLink(url);
});
// A folder or file dropped on the Dock icon, or opened with the app: it opens in the Workbench.
app.on('open-file', (e, file) => {
  e.preventDefault();
  let folder = false;
  try {
    folder = statSync(file).isDirectory();
  } catch {
    return;
  }
  go(folder ? `/workbench?folder=${encodeURIComponent(file)}` : `/workbench?path=${encodeURIComponent(file)}`);
});

function openLink(link: string): void {
  const route = routeOf(link);
  if (route) go(route);
}

/** Show a screen of the app: sent to the page, which navigates without reloading. */
function go(route: string): void {
  if (!isRoute(route)) return;
  showWindow();
  if (win && routeListener && isApp(win.webContents.getURL())) win.webContents.send('nc:navigate', route);
  else pendingRoute = route;
}

const isApp = (url: string) => !!origin && (url === origin || url.startsWith(`${origin}/`));

/* ── the stack ────────────────────────────────────────────────── */
function say(title: string): void {
  splashState = { phase: 'starting', title, detail: '', log: false };
  win?.webContents.send('splash:state', splashState);
}

function problem(p: StackProblem): void {
  splashState = { phase: 'problem', title: p.title, detail: p.detail, log: !!stack || existsSync(logFile()) };
  if (win && !isApp(win.webContents.getURL())) win.webContents.send('splash:state', splashState);
  else if (win) void win.loadFile(SPLASH);
}

async function bringUp(): Promise<string> {
  if (DEV_URL) {
    say('Waiting for the dev server…');
    const up = await fetch(DEV_URL, { signal: AbortSignal.timeout(3000) }).then((r) => r.ok, () => false);
    if (!up) {
      throw new StackProblem('The dev server is not answering',
        `Nothing answered at ${DEV_URL}. Start the web app and the API with npm run dev:start, then try again.`);
    }
    return DEV_URL;
  }
  say('Looking for NeuroCode on this Mac…');
  await adoptShellPath();
  let api = await findRunning();
  if (!api) {
    mkdirSync(app.getPath('logs'), { recursive: true });
    rotateLog(logFile());
    stack = await startApi({
      home: HOME,
      log: logFile(),
      status: say,
      lost: (p) => problem(p),
      moved: (next) => web?.retarget(next),
    });
    api = stack.api;
  } else {
    stack = { api, how: 'found', stop: async () => { /* not this app's to stop */ } };
  }
  if (!existsSync(path.join(WEB_ROOT, 'index.html'))) {
    throw new StackProblem('The web app is not built',
      `${WEB_ROOT} has no index.html. Run npm run build in ${HOME}, then try again.`);
  }
  say('Opening the app…');
  web = await startAppServer({ root: WEB_ROOT, api, port: remembered.port ?? 5188 });
  if (remembered.port !== web.port) {
    remembered = { ...remembered, port: web.port };
    keep(app.getPath('userData'), remembered);
  }
  return web.origin;
}

function boot(): Promise<void> {
  booting ??= (async () => {
    try {
      origin = await bringUp();
      installCsp(origin);
      await win?.loadURL(`${origin}/`);
    } catch (e) {
      console.error('[NeuroCode] the stack did not come up:', e);
      problem(e instanceof StackProblem ? e : new StackProblem('NeuroCode could not start', e instanceof Error ? e.message : String(e)));
      if (SMOKE) finishSmoke({ ok: false, reason: e instanceof Error ? e.message : String(e) });
    } finally {
      booting = null;
    }
  })();
  return booting;
}

async function retry(): Promise<void> {
  await stack?.stop();
  stack = null;
  await web?.close();
  web = null;
  origin = null;
  await boot();
}

/* ── the page's rules ─────────────────────────────────────────── */
let cspFor: string | null = null;
/** A strict policy on every page the app serves: its own scripts only, no plugins, no frames of it elsewhere. Vite's
    dev server needs its inline preamble and its HMR socket, so development relaxes exactly those two. */
function installCsp(base: string): void {
  if (cspFor === base) return;
  cspFor = base;
  const host = new URL(base).host;
  const policy = [
    "default-src 'self'",
    DEV_URL ? "script-src 'self' 'unsafe-inline'" : "script-src 'self'",
    // React and CodeMirror set styles from script; there is no user-supplied HTML for a style to hide in.
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    "media-src 'self' data: blob:",
    `connect-src 'self' ws://${host}`,
    "worker-src 'self' blob:",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ].join('; ');
  session.defaultSession.webRequest.onHeadersReceived((details, done) => {
    if (!isApp(details.url) || (details.resourceType !== 'mainFrame' && details.resourceType !== 'subFrame')) {
      done({});
      return;
    }
    const headers = { ...details.responseHeaders };
    for (const k of Object.keys(headers)) if (k.toLowerCase() === 'content-security-policy') delete headers[k];
    done({ responseHeaders: { ...headers, 'Content-Security-Policy': [policy] } });
  });
}

function external(url: string): void {
  try {
    const u = new URL(url);
    if (u.protocol === 'https:' || u.protocol === 'http:' || u.protocol === 'mailto:') void shell.openExternal(u.href);
  } catch {
    // Not a URL at all: nothing to open.
  }
}

function lockDown(contents: WebContents): void {
  contents.on('will-navigate', (e, url) => {
    if (isApp(url) || url === SPLASH_URL) return;
    e.preventDefault();
    external(url);
  });
  contents.on('will-redirect', (e, url) => {
    if (isApp(url) || url === SPLASH_URL) return;
    e.preventDefault();
  });
  contents.setWindowOpenHandler(({ url }) => {
    // A link to another screen of the app stays in this window; anything else opens in the default browser.
    if (isApp(url)) go(url.slice(origin!.length) || '/');
    else external(url);
    return { action: 'deny' };
  });
  contents.on('will-attach-webview', (e) => e.preventDefault());
  // Leaving with unsaved text in the Workbench: the page said so (beforeunload), and a desktop asks rather than
  // silently refusing to close.
  contents.on('will-prevent-unload', (e) => {
    const owner = BrowserWindow.fromWebContents(contents);
    const ask: Electron.MessageBoxSyncOptions = {
      type: 'warning',
      buttons: ['Leave', 'Stay'],
      defaultId: 1,
      cancelId: 1,
      message: 'Leave with unsaved changes?',
      detail: 'A file in the Workbench has changes that are not saved. Leaving now loses them.',
    };
    const choice = owner ? dialog.showMessageBoxSync(owner, ask) : dialog.showMessageBoxSync(ask);
    if (choice === 0) e.preventDefault();
  });
  // Electron shows no menu on a right click by default; text fields and selections get the usual one. The
  // Workbench's files and editor ask for their own through showPathMenu.
  contents.on('context-menu', (_e, params) => {
    const items: MenuItemConstructorOptions[] = [];
    if (params.isEditable) {
      items.push({ role: 'undo', enabled: params.editFlags.canUndo }, { role: 'redo', enabled: params.editFlags.canRedo }, { type: 'separator' },
        { role: 'cut', enabled: params.editFlags.canCut }, { role: 'copy', enabled: params.editFlags.canCopy },
        { role: 'paste', enabled: params.editFlags.canPaste }, { role: 'selectAll' });
    } else if (params.selectionText.trim()) {
      items.push({ role: 'copy' });
    }
    if (params.linkURL && !params.linkURL.startsWith('javascript:')) {
      if (items.length) items.push({ type: 'separator' });
      items.push({ label: 'Copy Link', click: () => clipboard.writeText(params.linkURL) });
      if (!isApp(params.linkURL)) items.push({ label: 'Open Link in Browser', click: () => external(params.linkURL) });
    }
    if (items.length) Menu.buildFromTemplate(items).popup({ window: BrowserWindow.fromWebContents(contents) ?? undefined });
  });
  contents.on('render-process-gone', (_e, details) => {
    if (details.reason === 'clean-exit' || quitting) return;
    console.error('[NeuroCode] the page stopped:', details.reason);
    if (origin) void contents.loadURL(`${origin}/`);
  });
}

/* ── the window ───────────────────────────────────────────────── */
function createWindow(): BrowserWindow {
  const bounds = onScreen(remembered.bounds, screen.getAllDisplays().map((d) => d.workArea));
  const w = new BrowserWindow({
    width: bounds?.width ?? 1440,
    height: bounds?.height ?? 900,
    x: bounds?.x,
    y: bounds?.y,
    minWidth: 720,
    minHeight: 520,
    show: false,
    title: 'NeuroCode',
    backgroundColor: '#0b0d11',
    titleBarStyle: 'default',
    webPreferences: {
      preload: path.join(HERE, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      allowRunningInsecureContent: false,
      navigateOnDragDrop: false,
      spellcheck: true,
      devTools: !app.isPackaged || process.env.NEUROCODE_DESKTOP_DEVTOOLS === '1',
    },
  });
  if (remembered.maximized) w.maximize();
  lockDown(w.webContents);
  w.once('ready-to-show', () => { if (!SMOKE) w.show(); });

  let timer: NodeJS.Timeout | null = null;
  const remember = () => {
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => {
      if (w.isDestroyed()) return;
      const maximized = w.isMaximized() || w.isFullScreen();
      remembered = { ...remembered, maximized, bounds: maximized ? remembered.bounds : w.getBounds() };
      keep(app.getPath('userData'), remembered);
    }, 400);
  };
  w.on('resize', remember);
  w.on('move', remember);
  w.on('close', () => {
    if (timer) clearTimeout(timer);
    const maximized = w.isMaximized() || w.isFullScreen();
    remembered = { ...remembered, maximized, bounds: maximized ? remembered.bounds : w.getBounds() };
    keep(app.getPath('userData'), remembered);
  });
  w.on('closed', () => {
    if (win === w) { win = null; routeListener = false; }
  });
  w.webContents.on('did-start-navigation', (details) => {
    if (details.isMainFrame && !details.isSameDocument) routeListener = false;
  });
  if (SMOKE) {
    w.webContents.on('did-finish-load', () => { if (isApp(w.webContents.getURL())) void smokeCheck(w); });
    // Anything the page's policy blocked is a smoke failure: the strict CSP must not cost the app a feature.
    w.webContents.on('console-message', (details) => {
      if (/Content Security Policy|Refused to/i.test(details.message)) smokeBlocked.push(details.message.slice(0, 300));
    });
  }
  return w;
}

function showWindow(): void {
  if (!app.isReady()) return;
  if (!win) {
    win = createWindow();
    if (origin) void win.loadURL(`${origin}/`);
    else void win.loadFile(SPLASH);
  }
  if (win.isMinimized()) win.restore();
  if (!SMOKE) { win.show(); win.focus(); }
}

/* ── what the page may ask for ────────────────────────────────── */
/** Every call must come from the app's own page in the main frame; the starting page may only ask for its state. */
function fromApp(e: IpcMainEvent | IpcMainInvokeEvent): boolean {
  const frame = e.senderFrame;
  return !!frame && frame === e.sender.mainFrame && isApp(frame.url);
}
function fromSplash(e: IpcMainEvent): boolean {
  return !!e.senderFrame && e.senderFrame.url === SPLASH_URL;
}
function refuse(): never {
  throw new Error('Refused: this call may only come from the NeuroCode app.');
}

/** File-dialog filters take extensions without the dot, and one part only: `.tar.gz` is offered as `gz`. */
function filters(accept: unknown): Electron.FileFilter[] | undefined {
  if (!Array.isArray(accept) || accept.length === 0) return undefined;
  const ext = [...new Set(accept.filter((a): a is string => typeof a === 'string')
    .map((a) => a.replace(/^.*\./, '').toLowerCase()).filter(Boolean))];
  return ext.length ? [{ name: 'Accepted files', extensions: ext }] : undefined;
}

function wire(): void {
  ipcMain.on('nc:info', (e) => {
    e.returnValue = fromApp(e) ? { version: app.getVersion(), platform: process.platform } : null;
  });

  ipcMain.handle('nc:pick', async (e, options: { mode?: unknown; title?: unknown; start?: unknown; confirmLabel?: unknown; accept?: unknown }) => {
    if (!fromApp(e)) refuse();
    const file = options?.mode === 'file';
    const start = typeof options?.start === 'string' && path.isAbsolute(options.start) && existsSync(options.start) ? options.start : undefined;
    const owner = BrowserWindow.fromWebContents(e.sender);
    const opts: Electron.OpenDialogOptions = {
      title: typeof options?.title === 'string' ? options.title.slice(0, 120) : undefined,
      buttonLabel: typeof options?.confirmLabel === 'string' ? options.confirmLabel.slice(0, 40) : undefined,
      defaultPath: start,
      properties: file ? ['openFile', 'treatPackageAsDirectory'] : ['openDirectory', 'createDirectory'],
      filters: file ? filters(options?.accept) : undefined,
    };
    const got = owner ? await dialog.showOpenDialog(owner, opts) : await dialog.showOpenDialog(opts);
    return got.canceled || !got.filePaths[0] ? null : got.filePaths[0];
  });

  ipcMain.handle('nc:reveal', async (e, target: unknown) => {
    if (!fromApp(e)) refuse();
    shell.showItemInFolder(await checkedPath(target));
  });

  ipcMain.handle('nc:edit', async (e, target: unknown, line: unknown) => {
    if (!fromApp(e)) refuse();
    return openInEditor(target, line);
  });

  ipcMain.handle('nc:path-menu', async (e, target: unknown, options: { line?: unknown; edit?: unknown } = {}) => {
    if (!fromApp(e)) refuse();
    const file = await checkedPath(target);
    const ed = await editor();
    const line = typeof options?.line === 'number' && Number.isInteger(options.line) && options.line > 0 ? options.line : null;
    const folder = statSync(file).isDirectory();
    const items: MenuItemConstructorOptions[] = [];
    if (options?.edit === true) {
      items.push({ role: 'cut' }, { role: 'copy' }, { role: 'paste' }, { type: 'separator' }, { role: 'selectAll' }, { type: 'separator' });
    }
    const failed = (what: string) => (err: unknown) => {
      void dialog.showMessageBox({ type: 'warning', message: what, detail: err instanceof Error ? err.message : String(err) });
    };
    if (!(folder && !ed.lines)) {
      items.push({
        label: `Open in ${ed.name}${line && ed.lines ? ` at Line ${line}` : ''}`,
        click: () => { openInEditor(file, line ?? undefined).catch(failed('The file did not open in the editor')); },
      });
    }
    items.push(
      { label: 'Reveal in Finder', click: () => shell.showItemInFolder(file) },
      { type: 'separator' },
      { label: 'Copy Path', click: () => clipboard.writeText(file) },
    );
    const owner = BrowserWindow.fromWebContents(e.sender) ?? undefined;
    await new Promise<void>((resolve) => Menu.buildFromTemplate(items).popup({ window: owner, callback: () => resolve() }));
  });

  ipcMain.on('nc:notify', (e, note: { title?: unknown; body?: unknown; route?: unknown }) => {
    if (!fromApp(e) || !Notification.isSupported()) return;
    const title = typeof note?.title === 'string' && note.title.trim() ? note.title : 'NeuroCode';
    const shown = new Notification({ title, body: typeof note?.body === 'string' ? note.body : '', silent: false });
    const route = typeof note?.route === 'string' ? note.route : '';
    shown.on('click', () => { if (route) go(route); else showWindow(); });
    shown.show();
  });

  ipcMain.on('nc:badge', (e, count: unknown) => {
    if (!fromApp(e)) return;
    const n = typeof count === 'number' && Number.isFinite(count) ? Math.max(0, Math.floor(count)) : 0;
    if (process.platform === 'darwin') app.dock?.setBadge(n > 0 ? (n > 99 ? '99+' : String(n)) : '');
    else app.setBadgeCount(n);
  });

  ipcMain.on('nc:navigate-ready', (e) => {
    if (!fromApp(e)) return;
    routeListener = true;
    if (pendingRoute) {
      e.sender.send('nc:navigate', pendingRoute);
      pendingRoute = null;
    }
  });

  ipcMain.on('splash:ready', (e) => { if (fromSplash(e)) e.sender.send('splash:state', splashState); });
  ipcMain.on('splash:retry', (e) => {
    if (!fromSplash(e) || booting) return;
    say('Trying again…');
    void retry();
  });
  ipcMain.on('splash:log', (e) => {
    if (!fromSplash(e)) return;
    if (existsSync(logFile())) void shell.openPath(logFile());
  });
}

/* ── menus ────────────────────────────────────────────────────── */
async function openFolder(): Promise<void> {
  const owner = win ?? undefined;
  const opts: Electron.OpenDialogOptions = { title: 'Open a folder in the Workbench', buttonLabel: 'Open', properties: ['openDirectory'] };
  const got = owner ? await dialog.showOpenDialog(owner, opts) : await dialog.showOpenDialog(opts);
  if (!got.canceled && got.filePaths[0]) go(`/workbench?folder=${encodeURIComponent(got.filePaths[0])}`);
}

async function openFile(): Promise<void> {
  const owner = win ?? undefined;
  const opts: Electron.OpenDialogOptions = { title: 'Open a file in the Workbench', buttonLabel: 'Open', properties: ['openFile', 'treatPackageAsDirectory'] };
  const got = owner ? await dialog.showOpenDialog(owner, opts) : await dialog.showOpenDialog(opts);
  if (!got.canceled && got.filePaths[0]) go(`/workbench?path=${encodeURIComponent(got.filePaths[0])}`);
}

function menus(): void {
  const mac = process.platform === 'darwin';
  const screenItem = (label: string, route: string, accelerator?: string): MenuItemConstructorOptions =>
    ({ label, accelerator, click: () => go(route) });
  const template: MenuItemConstructorOptions[] = [
    ...(mac ? [{
      label: 'NeuroCode',
      submenu: [
        { role: 'about' },
        { type: 'separator' },
        screenItem('Settings…', '/settings', 'Cmd+,'),
        { type: 'separator' },
        { role: 'services' },
        { type: 'separator' },
        { role: 'hide' },
        { role: 'hideOthers' },
        { role: 'unhide' },
        { type: 'separator' },
        { role: 'quit' },
      ],
    } satisfies MenuItemConstructorOptions] : []),
    {
      label: 'File',
      submenu: [
        screenItem('New Project', '/projects?new=1', 'CmdOrCtrl+N'),
        screenItem('New Blueprint', '/blueprints?new=1', 'Shift+CmdOrCtrl+N'),
        { type: 'separator' },
        { label: 'Open Folder…', accelerator: 'CmdOrCtrl+O', click: () => { void openFolder(); } },
        { label: 'Open File…', accelerator: 'Shift+CmdOrCtrl+O', click: () => { void openFile(); } },
        { type: 'separator' },
        mac ? { role: 'close' } : { role: 'quit' },
      ],
    },
    { role: 'editMenu' },
    {
      label: 'View',
      submenu: [
        { label: 'Back', accelerator: 'CmdOrCtrl+[', click: () => { if (win?.webContents.navigationHistory.canGoBack()) win.webContents.navigationHistory.goBack(); } },
        { label: 'Forward', accelerator: 'CmdOrCtrl+]', click: () => { if (win?.webContents.navigationHistory.canGoForward()) win.webContents.navigationHistory.goForward(); } },
        { type: 'separator' },
        { role: 'reload' },
        ...(!app.isPackaged || process.env.NEUROCODE_DESKTOP_DEVTOOLS === '1' ? [{ role: 'toggleDevTools' } satisfies MenuItemConstructorOptions] : []),
        { type: 'separator' },
        { role: 'resetZoom' },
        { role: 'zoomIn' },
        { role: 'zoomOut' },
        { type: 'separator' },
        { role: 'togglefullscreen' },
      ],
    },
    {
      label: 'Go',
      submenu: [
        screenItem('Command Center', '/', 'CmdOrCtrl+1'),
        screenItem('Projects', '/projects', 'CmdOrCtrl+2'),
        screenItem('Workbench', '/workbench', 'CmdOrCtrl+3'),
        screenItem('Sessions', '/sessions', 'CmdOrCtrl+4'),
        screenItem('Plans', '/plans', 'CmdOrCtrl+5'),
        screenItem('Runs', '/runs', 'CmdOrCtrl+6'),
        screenItem('Approvals', '/permissions', 'CmdOrCtrl+7'),
        { type: 'separator' },
        screenItem('Activity', '/activity'),
        screenItem('Memory', '/memory'),
        screenItem('Code Intelligence', '/code'),
      ],
    },
    { role: 'windowMenu' },
    {
      role: 'help',
      submenu: [
        { label: 'Show the API Log', click: () => { if (existsSync(logFile())) void shell.openPath(logFile()); else void dialog.showMessageBox({ message: 'No API log yet', detail: stack?.how === 'found' ? 'This app is using an API that was already running; its log is wherever that API was started.' : 'The API has not been started by this app.' }); } },
        { label: 'Show App Data in Finder', click: () => { void shell.openPath(app.getPath('userData')); } },
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

/* ── smoke: load the app, check it rendered and reached the API, quit ── */
let smokeDone = false;
const smokeBlocked: string[] = [];
function finishSmoke(result: Record<string, unknown>): void {
  if (smokeDone) return;
  smokeDone = true;
  process.stdout.write(`NC_SMOKE ${JSON.stringify(result)}\n`);
  process.exitCode = result.ok ? 0 : 1;
  app.quit();
}

async function smokeCheck(w: BrowserWindow): Promise<void> {
  const t0 = Date.now();
  while (Date.now() - t0 < 45_000 && !w.isDestroyed()) {
    const seen = await w.webContents.executeJavaScript(`(async () => {
      const root = document.getElementById('root');
      const text = root ? root.innerText.trim().slice(0, 200) : '';
      const bridge = typeof window.neurocode === 'object' && typeof window.neurocode.pickFolder === 'function';
      const status = await fetch('/api/auth/status').then((r) => r.status, () => 0);
      return { title: document.title, rendered: !!root && root.children.length > 0 && text.length > 0, text, bridge, status,
        opening: /Opening your workspace/.test(text) };
    })()`) as { title: string; rendered: boolean; text: string; bridge: boolean; status: number; opening: boolean };
    if (seen.rendered && !seen.opening && seen.status === 200) {
      // A moment more, for anything the policy blocks after the first paint (lazy chunks, fonts).
      await new Promise((r) => setTimeout(r, 1500));
      finishSmoke({
        ok: seen.bridge && smokeBlocked.length === 0, origin, api: stack?.api, how: stack?.how, title: seen.title,
        text: seen.text.replace(/\s+/g, ' '), bridge: seen.bridge, blocked: smokeBlocked,
      });
      return;
    }
    await new Promise((r) => setTimeout(r, 400));
  }
  finishSmoke({ ok: false, reason: 'The app did not render within 45 seconds.' });
}

/* ── life ─────────────────────────────────────────────────────── */
async function ready(): Promise<void> {
  remembered = recall(app.getPath('userData'));
  if (app.isPackaged) app.setAsDefaultProtocolClient('neurocode');
  session.defaultSession.setPermissionRequestHandler((contents, permission, done) => {
    // The page may write to the clipboard (Copy buttons), go full screen and show notifications; nothing else.
    done(isApp(contents.getURL()) && ['clipboard-sanitized-write', 'fullscreen', 'notifications'].includes(permission));
  });
  wire();
  menus();
  win = createWindow();
  await win.loadFile(SPLASH);
  await boot();
}

app.on('activate', () => showWindow());

app.on('window-all-closed', () => {
  // On macOS an app stays open with no window, as every Mac app does; the stack keeps running until Quit.
  if (process.platform !== 'darwin' || SMOKE) app.quit();
});

app.on('before-quit', (e) => {
  quitting = true;
  if (!stack && !web) return;
  // Stopping the API takes a moment (it finishes what it is writing); the quit waits for it, once.
  e.preventDefault();
  const running = stack;
  const served = web;
  stack = null;
  web = null;
  void (async () => {
    await running?.stop().catch((err: unknown) => console.error('[NeuroCode] the API did not stop cleanly:', err));
    await served?.close().catch(() => undefined);
    app.quit();
  })();
});
