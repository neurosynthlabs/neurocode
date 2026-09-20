# NeuroCode for VS Code

The editor's door into a NeuroCode server. It is a client, not a second brain: every command here is
a request to the same HTTP API the web app and `nc` use, signed in with a personal access token, so
every permission, every tool rule and every gate is the server's. Nothing is decided in the editor,
and nothing a model says is ever run by it.

## What it does

| Command | What happens |
| --- | --- |
| **NeuroCode: Sign in with a personal access token** | Asks for the server and the token (made in Settings → Access tokens). The token goes to the editor's secret store — the OS keychain behind it — never to a setting. |
| **Ask NeuroCode about this selection** | Starts a session in your project with the file attached and the selected lines quoted into the question, then opens that session in the web app, where the answer streams in. |
| **Compile this as a requirement** | Sends the selection to the requirement compiler and opens the plan — including the questions it refused to guess at. |
| **Open in the Workbench** | Opens the project's Workbench at the folder of the file you are in. |
| **Runs** (the NeuroCode view) | Every agent run and its state, with whatever is waiting on you at the top, following `/activity/stream` — the same server-sent events every open tab reads. |

Right-click in the editor for the first three; the Runs view is in the activity bar.

## What it does not do

- **It never signs a run.** Accepting a run, merging it, answering a gate or a permission card all
  need the diff, the review and the findings in front of you — so those open the web app at exactly
  that screen rather than growing a thin copy of it here.
- **It holds no model.** Asking and compiling are the server's AI gateway; with no model configured
  they refuse, in the server's own words, and so does this.
- **It keeps nothing but the token and the project.** There is no cache of your workspace here.

## Settings

| Setting | Means |
| --- | --- |
| `neurocode.server` | The web app's address (its API is under `/api`) or an API's own — the same address `nc login` takes, resolved the same way. |
| `neurocode.project` | The project every command works in. “NeuroCode: Choose the project” sets it, per workspace. |

A token made with no scopes carries everything you hold **except `machine:access`**, so a leaked one
cannot open a shell on the server's machine. Nothing here needs `machine:access`.

Reaching a server on another machine is the same as `nc`: point `neurocode.server` at it. For a
NeuroCode running on your own laptop, that means it has to be listening beyond `127.0.0.1` —
`NEUROCODE_LISTEN_ON_LAN=true`, which the repository's `README.md` says the cost of.

## Building it

```sh
cd vscode
npm install
npm run build          # tsc -p . → out/
npm test               # builds, then node --test over the pure parts
npm run package        # vsce package → neurocode-<version>.vsix
```

`npm run package` needs `@vscode/vsce`, which `npm install` brings. Install the result with
`code --install-extension neurocode-0.9.4.vsix`, or press F5 in this folder to run it in an
Extension Development Host.

The half of this extension that can be tested without an editor is all of the half that decides
anything: `src/protocol.ts` imports nothing and holds the address resolution, the server-sent event
reader, the path a file is attached under and the shape of the question. `test/protocol.test.mjs`
reads it back. `src/extension.ts` and `src/runs.ts` are the editor's side of those decisions and
hold no rules of their own; they are exercised by running the extension, not by a test here.
