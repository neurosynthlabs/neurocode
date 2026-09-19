# NeuroCode for the desktop

The same web app and the same local API, in a Mac window of its own — every screen the web has — plus what only a
desktop can do: the Mac's own folder and file dialogs, Reveal in Finder, Open in your editor, native notifications,
a Dock badge with the approvals waiting, menus and shortcuts, and `neurocode://` links.

It is an Electron shell (`desktop/src`, TypeScript, compiled with the desktop's own `tsconfig.json` into
`desktop/app/out`). macOS on Apple silicon first.

## Commands

| Command | What it does |
| --- | --- |
| `npm run desktop:dev` | The app against the dev servers: loads `http://localhost:5180` (or `NEUROCODE_DESKTOP_DEV_URL`), whose Vite proxy already reaches the API. Start them first with `npm run dev:start`. Uses its own profile, so it runs beside the installed app. |
| `npm run desktop:start` | The app from source the way a packed app runs: it finds or starts the API and serves `dist/` (run `npm run build` first). |
| `npm run desktop:build` | Builds the web app, then packs `desktop/dist/NeuroCode-<version>-arm64.dmg`. |
| `npm run desktop:test` | The desktop's own tests (`node --test`): the server and proxy against real sockets, links, window state. |
| `NEUROCODE_SMOKE_DATABASE_URL=… npm run desktop:smoke [-- --packed]` | Launches the real app with a window that is never shown, on its own API and the database you name, checks the page rendered, reached the API through the proxy, has the bridge and was blocked by nothing in its policy, quits, and checks the API stopped. `--packed` runs the built `.app`. It refuses the `neurocode` database by name. |

## What happens when it opens

1. **The stack is found or started.** An API already answering on this Mac (`NC_API_PORT`, else 8787 — the one
   `npm run dev:start` runs) is used as it is and is left running on quit. Otherwise the app asks
   `python -m app.data.check` first — no Postgres, no database and no migrations each print their own one-line fix,
   which the starting page shows with **Try again** and **Show the API log** — then starts the API the way
   `npm run api` does, on a free 127.0.0.1 port, as a child process. It is restarted if it dies (at most three times
   a minute, then the reason is shown), and stopped (SIGTERM to its process group, SIGKILL after 7 s) on Quit.
2. **The web app is served beside it.** A small server in the app serves the built web app and proxies `/api` —
   plain requests, the change stream (SSE) and the Workbench's terminal and debugger WebSockets — on one
   `http://127.0.0.1:<port>` origin, so the session cookie and the `X-NC-Client` rule work exactly as in a browser.
   It answers only `127.0.0.1` / `localhost` by that port (a rebound DNS name is refused) and never serves a file
   outside the web app. The port is remembered (5188 first), because the page's own storage — open tabs, sidebar
   widths, the last folder a picker used — belongs to that origin.
3. **The window is locked down.** `contextIsolation` on, `nodeIntegration` off, `sandbox` on; a strict
   Content-Security-Policy on every page (the app's own scripts only, no plugins, no framing); navigation held to
   the app's origin, with every other link opened in the default browser; no `<webview>`; only clipboard writes,
   full screen and notifications may be granted. Every bridge call is answered only for the app's own page.

Its window size and place are remembered (and put back on a display that is still there), one instance runs at a
time, and leaving with unsaved Workbench changes asks first.

## The bridge (`window.neurocode`)

Declared for the web app in `src/lib/desktop.ts`; exposed by `desktop/src/preload.cts`.

| Call | |
| --- | --- |
| `pickFolder(options)` / `pickFile(options)` | The Mac's dialogs (`title`, `start`, `confirmLabel`, `accept`); `null` on Cancel. |
| `revealInFinder(path)` | Shows the file or folder in the Finder. |
| `openInEditor(path, line?)` | VS Code, Cursor or Windsurf when installed (`-g file:line`), else `NEUROCODE_EDITOR`, else the default text editor (`open -t`, no line). Never plain `open`, which would run a `.command` or an `.app`. Answers with the editor's name. |
| `showPathMenu(path, { line?, edit? })` | The native menu for a file: Cut/Copy/Paste (with `edit`), Open in the editor, Reveal in Finder, Copy Path. |
| `notify(title, body, { route? })` | A native notification; a click brings the window forward, on `route` when given. |
| `setBadge(count)` | The Dock badge; 0 clears it. |
| `onNavigate(listener)` | Screens asked for by the menus, `neurocode://` links, a dropped folder and notifications. |
| `platform`, `version` | |

Where the web app uses it: the FolderPicker opens the Mac's dialog first, in both of its modes (a folder, or a file
with the accepted endings), and falls back to its own browser with the reason when the pick is outside the API's
roots; right-click on the Workbench's files, folders and editor gives the native menu; Code Intelligence shows
**Open in editor** and **Reveal in Finder** on a file (double-click a symbol opens it at its line); the sidebar
sends the approvals waiting to the Dock badge and follows the menus. In a browser none of it appears.

## Menus

- **NeuroCode**: About, Settings… (⌘,), Hide, Quit.
- **File**: New Project (⌘N), New Blueprint (⇧⌘N), Open Folder… (⌘O) and Open File… (⇧⌘O) into the Workbench, Close Window.
- **Edit**, **View** (Back ⌘[, Forward ⌘], Reload, zoom, full screen), **Go** (⌘1–⌘7: Command Center, Projects,
  Workbench, Sessions, Plans, Runs, Approvals; Activity, Memory, Code Intelligence), **Window**, **Help** (Show the
  API Log, Show App Data in Finder).

The app's own shortcuts (⌘K, ⌘B, ⌘P, ⌘S, ⌘↵) are left to the page. A folder dropped on the Dock icon opens in the
Workbench; `open "neurocode:///workbench?project=<id>"` opens a screen (the scheme is registered by the packed app).

## Packaging and signing

`desktop:build` compiles the shell, draws the icon from `public/neurocode.svg` (Electron renders a 1024 px PNG;
`sips` and `iconutil` make `icon.icns`), and runs electron-builder with the Electron already in `node_modules`. The
app holds only the shell's own files (about 70 KB) plus the built web app in `Resources/web`; the server is not
bundled — the app starts the one in the checkout it was built from (`desktop/app/build.json`), or the folder
`NEUROCODE_HOME` names, and needs `uv` and Postgres there as `npm run dev:start` does.

**Signing.** With a "Developer ID Application" identity in the keychain the app is signed with it (and notarised when
`APPLE_ID`, `APPLE_APP_SPECIFIC_PASSWORD` and `APPLE_TEAM_ID` are set). Without one — as on the machine it was first
built on — it is **unsigned**: signed ad hoc only, which lets Apple silicon run it on this Mac. On another Mac,
Gatekeeper refuses it until it is opened once with right-click → Open.

## Settings it reads

| Variable | |
| --- | --- |
| `NEUROCODE_HOME` | The NeuroCode checkout holding `server/`, when not the one the app was built from. |
| `NEUROCODE_API_URL` | Use this API (on this Mac only) instead of looking for one. |
| `NC_API_PORT` | Where to look for a running API (8787). |
| `NEUROCODE_DESKTOP_FIND=0` | Never use a running API; always start one. |
| `NEUROCODE_DESKTOP_DEV_URL` | Load this dev server instead (set by `desktop:dev`). |
| `NEUROCODE_DESKTOP_WEB` | Serve this web build instead of `dist/` / `Resources/web`. |
| `NEUROCODE_DESKTOP_PROFILE` | A separate profile folder (window state, cookies, storage, logs). |
| `NEUROCODE_EDITOR` | The editor command for Open in editor (it is given `-g file:line`). |
| `NEUROCODE_DESKTOP_DEVTOOLS=1` | Developer tools in a packed app. |

Everything else (`NEUROCODE_DATABASE_URL`, `NEUROCODE_MACHINE_ROOTS`, …) passes through to the API it starts. The
API's log is `~/Library/Logs/NeuroCode/api.log` (the previous launch's is `api.previous.log`).
