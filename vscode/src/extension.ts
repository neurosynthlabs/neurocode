/* NeuroCode in the editor.
 *
 * Four things, and deliberately no more: sign in with a personal access token; ask about what you
 * have selected; compile what you have selected as a requirement; and watch the agent runs. Every
 * one of them is a request to the same API the web app and `nc` use — this extension holds no
 * model, no rules and no state of its own beyond the token and which project you are in.
 *
 * Where something needs a person — a permission card in a session, a gate in a run, the signature at
 * the end of one — the editor is the wrong place for it: those need the diff, the review and the
 * findings in front of you. So each of those opens the web app at exactly that screen instead of
 * growing a thin copy of it here.
 */
import * as vscode from 'vscode';
import { Api, locate, Refused } from './api';
import {
  folderOf,
  looksLikeToken,
  planUrl,
  relativeTo,
  runUrl,
  selectionQuestion,
  sessionTitle,
  sessionUrl,
  workbenchUrl,
  type Run,
  type Selected,
} from './protocol';
import { RunsView } from './runs';

//: Where the token is kept: the editor's own secret store (the OS keychain behind it), never a setting.
const TOKEN = 'neurocode.token';
//: The address the token was made for, so changing the server does not silently reuse another one's.
const TOKEN_SERVER = 'neurocode.tokenServer';

interface Open {
  api: Api;
  /** The web app's address, when the server was found through it; null when the API was given directly. */
  web: string | null;
}

export function activate(context: vscode.ExtensionContext): void {
  const settings = () => vscode.workspace.getConfiguration('neurocode');
  const out = vscode.window.createOutputChannel('NeuroCode');
  context.subscriptions.push(out);

  /* One connection per server address, remade when the address or the token changes. Nothing is
     cached across a sign-out: a stale client would keep working for one command and then 401. */
  let open: Open | null = null;
  let openedFor = '';

  const connect = async (): Promise<Open | null> => {
    const address = String(settings().get('server') ?? '').trim();
    const token = await context.secrets.get(TOKEN);
    const madeFor = context.globalState.get<string>(TOKEN_SERVER);
    if (!address || !token) return null;
    if (madeFor && madeFor !== address) return null;
    if (open && openedFor === `${address}|${token}`) return open;
    const where = await locate(address);
    open = { api: new Api(where.api, token), web: where.web };
    openedFor = `${address}|${token}`;
    return open;
  };

  /** Connect, or say in one sentence which of the two things is missing. Never throws. */
  const connected = async (): Promise<Open | null> => {
    try {
      const found = await connect();
      if (found) return found;
      const pick = await vscode.window.showWarningMessage(
        'NeuroCode is not signed in on this machine. A personal access token is made in Settings → Access tokens.',
        'Sign in',
      );
      if (pick === 'Sign in') await vscode.commands.executeCommand('neurocode.signIn');
      return null;
    } catch (e) {
      refused(e);
      return null;
    }
  };

  const refused = (e: unknown): void => {
    const words = e instanceof Refused ? e.detail : e instanceof Error ? e.message : String(e);
    out.appendLine(`${new Date().toISOString()}  ${words}`);
    void vscode.window.showErrorMessage(`NeuroCode: ${words}`, 'Show log').then((pick) => {
      if (pick === 'Show log') out.show(true);
    });
  };

  /** The project every command works in, asked for once and then remembered in the settings. */
  const project = async (found: Open): Promise<string | null> => {
    const chosen = String(settings().get('project') ?? '').trim();
    if (chosen) return chosen;
    return chooseProject(found);
  };

  const chooseProject = async (found: Open): Promise<string | null> => {
    const projects = (await found.api.projects()) as { id: string; name: string; repo?: string }[];
    if (!projects.length) {
      void vscode.window.showWarningMessage(
        'This workspace has no projects yet. Open a folder as a project in NeuroCode first — it is empty until somebody does.',
      );
      return null;
    }
    const here = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
    const pick = await vscode.window.showQuickPick(
      projects.map((it) => ({
        label: it.name,
        description: it.id,
        detail: here && it.repo && it.repo === here ? 'the folder open here' : it.repo,
        id: it.id,
      })),
      { title: 'Which NeuroCode project?', matchOnDescription: true },
    );
    if (!pick) return null;
    await settings().update('project', pick.id, vscode.ConfigurationTarget.Workspace);
    return pick.id;
  };

  /** What is selected, as the server will read it, or a sentence saying why it cannot be sent. */
  const selection = (): Selected | string => {
    const editor = vscode.window.activeTextEditor;
    if (!editor) return 'Open a file first: there is nothing selected to ask about.';
    const root = vscode.workspace.getWorkspaceFolder(editor.document.uri)?.uri.fsPath;
    if (!root) {
      return 'This file is not inside the folder open here, so NeuroCode has no path for it. Open the project’s folder in this window.';
    }
    const path = relativeTo(root, editor.document.uri.fsPath);
    if (!path) return 'This file is not inside the folder open here, so NeuroCode has no path for it.';
    const range = editor.selection.isEmpty
      ? new vscode.Range(0, 0, editor.document.lineCount, 0)
      : editor.selection;
    const text = editor.document.getText(range);
    if (!text.trim()) return 'That selection is empty.';
    return {
      path,
      from: range.start.line + 1,
      to: (range.end.character === 0 ? range.end.line : range.end.line + 1) || 1,
      text,
      language: editor.document.languageId,
    };
  };

  /** Open a screen of the web app, or say plainly that this server was reached without one. */
  const openWeb = async (found: Open, url: (web: string) => string, instead: string): Promise<void> => {
    if (!found.web) {
      void vscode.window.showInformationMessage(
        `${instead} The web app’s address is not known — “neurocode.server” points straight at an API. Point it at the web app to open screens from here.`,
      );
      return;
    }
    await vscode.env.openExternal(vscode.Uri.parse(url(found.web)));
  };

  const runs = new RunsView(connected);
  const tree = vscode.window.createTreeView('neurocode.runs', { treeDataProvider: runs });
  context.subscriptions.push(tree, runs);
  const badge = () => {
    tree.badge = runs.waiting ? { value: runs.waiting, tooltip: `${runs.waiting} waiting on you` } : undefined;
  };
  context.subscriptions.push(runs.onDidChangeTreeData(badge));
  void runs.refresh();

  context.subscriptions.push(
    vscode.commands.registerCommand('neurocode.signIn', async () => {
      const address = await vscode.window.showInputBox({
        title: 'Which NeuroCode server?',
        prompt: 'The web app’s address (its API is under /api), or an API’s own — the same address nc login takes.',
        value: String(settings().get('server') ?? 'http://localhost:5180'),
        ignoreFocusOut: true,
      });
      if (!address) return;
      const token = await vscode.window.showInputBox({
        title: 'Personal access token',
        prompt: 'Settings → Access tokens in NeuroCode makes one. It is shown once, and kept here in the editor’s secret store.',
        password: true,
        ignoreFocusOut: true,
        validateInput: (value) =>
          value && !looksLikeToken(value)
            ? 'That is not a NeuroCode personal access token — they begin with nc_pat_. A password will not do: make a token in Settings → Access tokens.'
            : undefined,
      });
      if (!token) return;
      try {
        const where = await locate(address);
        const me = await new Api(where.api, token).me();
        await settings().update('server', address, vscode.ConfigurationTarget.Global);
        await context.secrets.store(TOKEN, token);
        await context.globalState.update(TOKEN_SERVER, address);
        open = null;
        void vscode.window.showInformationMessage(
          `NeuroCode: signed in to ${where.api} as ${me.user.name}${me.workspace ? ` (${me.workspace.name})` : ''}.`,
        );
        void runs.refresh();
      } catch (e) {
        refused(e);
      }
    }),

    vscode.commands.registerCommand('neurocode.signOut', async () => {
      await context.secrets.delete(TOKEN);
      await context.globalState.update(TOKEN_SERVER, undefined);
      open = null;
      void vscode.window.showInformationMessage(
        'NeuroCode: the token is forgotten here. Revoke it in Settings → Access tokens if it should stop working everywhere.',
      );
      void runs.refresh();
    }),

    vscode.commands.registerCommand('neurocode.chooseProject', async () => {
      const found = await connected();
      if (!found) return;
      try {
        const chosen = await chooseProject(found);
        if (chosen) void vscode.window.showInformationMessage(`NeuroCode: working in ${chosen}.`);
      } catch (e) {
        refused(e);
      }
    }),

    vscode.commands.registerCommand('neurocode.askAboutSelection', async () => {
      const what = selection();
      if (typeof what === 'string') {
        void vscode.window.showWarningMessage(`NeuroCode: ${what}`);
        return;
      }
      const found = await connected();
      if (!found) return;
      const question = await vscode.window.showInputBox({
        title: `Ask NeuroCode about ${sessionTitle(what)}`,
        prompt: 'It is answered in a session, grounded in this project’s code and memory — the same session the web app shows.',
        ignoreFocusOut: true,
      });
      if (!question) return;
      try {
        const pid = await project(found);
        if (!pid) return;
        const chat = await found.api.startSession(pid, sessionTitle(what));
        await found.api.ask(chat.ref, selectionQuestion(question, what), [
          { kind: 'file', ref: what.path, name: what.path },
        ]);
        out.appendLine(`${new Date().toISOString()}  asked in ${chat.ref}: ${what.path}:${what.from}-${what.to}`);
        await openWeb(found, (web) => sessionUrl(web, chat.ref), `Asked in ${chat.ref}.`);
      } catch (e) {
        refused(e);
      }
    }),

    vscode.commands.registerCommand('neurocode.compileRequirement', async () => {
      const what = selection();
      if (typeof what === 'string') {
        void vscode.window.showWarningMessage(`NeuroCode: ${what}`);
        return;
      }
      const found = await connected();
      if (!found) return;
      try {
        const pid = await project(found);
        if (!pid) return;
        const plan = await vscode.window.withProgress(
          { location: vscode.ProgressLocation.Notification, title: 'NeuroCode is compiling this requirement…' },
          () => found.api.compile(pid, what.text.trim()),
        );
        const questions = plan.openQuestions?.length ?? 0;
        out.appendLine(`${new Date().toISOString()}  compiled ${plan.ref} from ${what.path}`);
        await openWeb(
          found,
          (web) => planUrl(web, plan.ref),
          // The open questions are the point of the compiler, so they are said before the plan is opened.
          `${plan.ref} compiled${questions ? `, with ${questions} question${questions === 1 ? '' : 's'} it refused to guess at` : ''}.`,
        );
      } catch (e) {
        refused(e);
      }
    }),

    vscode.commands.registerCommand('neurocode.openInWorkbench', async () => {
      const found = await connected();
      if (!found) return;
      try {
        const pid = await project(found);
        if (!pid) return;
        const file = vscode.window.activeTextEditor?.document.uri.fsPath;
        await openWeb(
          found,
          (web) => workbenchUrl(web, pid, file ? folderOf(file) : undefined),
          'The Workbench is a screen of the web app.',
        );
      } catch (e) {
        refused(e);
      }
    }),

    vscode.commands.registerCommand('neurocode.refreshRuns', () => void runs.refresh()),

    vscode.commands.registerCommand('neurocode.openRun', async (run: Run) => {
      const found = await connected();
      if (!found || !run?.ref) return;
      await openWeb(found, (web) => runUrl(web, run.ref), `${run.ref} lives in the web app.`);
    }),

    // Changing the server or the project means the next command talks to the right place, and the
    // runs in the sidebar are the right workspace's.
    vscode.workspace.onDidChangeConfiguration((change) => {
      if (change.affectsConfiguration('neurocode.server')) {
        open = null;
        void runs.refresh();
      }
    }),
  );
}

export function deactivate(): void {
  /* Everything this extension holds is in context.subscriptions, which the editor disposes. */
}
