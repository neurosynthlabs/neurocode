/* The Runs view: what the agents are doing, in the sidebar, live.
 *
 * It reads `GET /runs` once and then follows `/activity/stream` — the same server-sent events every
 * open tab of the web app reads — so a run that starts, finishes or stops at your signature moves in
 * this list at the moment it moves everywhere else. There is no polling and no second source of
 * truth; when the stream says it missed events (`resync`), the list is read again rather than
 * patched, because a list patched from events it did not see is a list that is quietly wrong.
 *
 * The view never acts. Stopping, accepting, merging — everything that decides something — is a
 * person's, in the web app or in `nc`, and this opens the run there.
 */
import * as vscode from 'vscode';
import { Api, Refused } from './api';
import { applyChange, needsAPerson, order, RUN_STATES, runDescription, runLabel, type Run } from './protocol';

/** How long to wait before opening the stream again, growing to a minute so a stopped server is not hammered. */
const BACKOFF = [1_000, 2_000, 5_000, 15_000, 60_000];

type Node = Run | { note: string };
const isNote = (node: Node): node is { note: string } => 'note' in node;

export class RunsView implements vscode.TreeDataProvider<Node> {
  private readonly changed = new vscode.EventEmitter<void>();
  readonly onDidChangeTreeData = this.changed.event;

  private runs: Run[] = [];
  private note = 'Not signed in. Run “NeuroCode: Sign in with a personal access token”.';
  private watching: AbortController | null = null;
  private attempts = 0;
  private gone = false;

  constructor(private readonly connect: () => Promise<{ api: Api; web: string | null } | null>) {}

  dispose(): void {
    this.gone = true;
    this.watching?.abort();
    this.changed.dispose();
  }

  getTreeItem(node: Node): vscode.TreeItem {
    if (isNote(node)) {
      const item = new vscode.TreeItem(node.note, vscode.TreeItemCollapsibleState.None);
      item.tooltip = node.note;
      return item;
    }
    const item = new vscode.TreeItem(runLabel(node), vscode.TreeItemCollapsibleState.None);
    item.description = runDescription(node);
    item.contextValue = 'run';
    item.id = node.ref;
    item.tooltip = new vscode.MarkdownString(
      [
        `**${node.ref}** — ${RUN_STATES[node.status] ?? node.status}`,
        node.projectName ? `Project: ${node.projectName}` : '',
        node.branch ? `Branch: \`${node.branch}\`` : '',
        node.requirement ? `\n${node.requirement}` : '',
      ]
        .filter(Boolean)
        .join('\n\n'),
    );
    item.iconPath = new vscode.ThemeIcon(ICONS[node.status] ?? 'circle-outline');
    item.command = { command: 'neurocode.openRun', title: 'Open this run in NeuroCode', arguments: [node] };
    return item;
  }

  getChildren(node?: Node): Node[] {
    if (node) return [];
    if (this.runs.length) return this.runs;
    return [{ note: this.note }];
  }

  /** Read the list again from the server, and (re)open the stream behind it. */
  async refresh(): Promise<void> {
    const open = await this.connect();
    if (!open) {
      this.say('Not signed in. Run “NeuroCode: Sign in with a personal access token”.');
      return;
    }
    try {
      this.runs = order((await open.api.runs()) as Run[]);
      this.note = this.runs.length ? '' : 'No agent runs yet. Dispatch a plan and they appear here.';
      this.changed.fire();
    } catch (e) {
      this.say(e instanceof Refused ? e.detail : String(e));
      return;
    }
    void this.watch(open.api);
  }

  private say(note: string): void {
    this.runs = [];
    this.note = note;
    this.changed.fire();
  }

  /** Follow the stream, reopening it when it ends, until the view is disposed or refreshed again. */
  private async watch(api: Api): Promise<void> {
    this.watching?.abort();
    const mine = new AbortController();
    this.watching = mine;
    this.attempts = 0;
    while (!this.gone && !mine.signal.aborted) {
      try {
        await api.stream((event) => {
          if (event.kind === 'change') {
            const before = this.runs;
            this.runs = applyChange(this.runs, event.data);
            if (this.runs !== before) {
              this.note = '';
              this.changed.fire();
            }
          } else if (event.kind === 'resync' || event.kind === 'reset') {
            // What went past is not kept, so the only true thing to do is read the list again.
            mine.abort();
            void this.refresh();
          }
        }, mine.signal);
        this.attempts = 0;
      } catch (e) {
        if (mine.signal.aborted) return;
        // Said once per outage, in the list itself, rather than as a notification every few seconds.
        if (this.attempts === 0 && !this.runs.length) this.say(e instanceof Refused ? e.detail : String(e));
      }
      if (mine.signal.aborted || this.gone) return;
      const wait = BACKOFF[Math.min(this.attempts, BACKOFF.length - 1)];
      this.attempts += 1;
      await new Promise((done) => setTimeout(done, wait));
    }
  }

  /** How many runs are stopped at a person — what the badge on the view counts. */
  get waiting(): number {
    return this.runs.filter(needsAPerson).length;
  }
}

/** A state, as one of the editor's own icons. Nothing here invents a colour; these are the theme's. */
const ICONS: Record<string, string> = {
  queued: 'clock',
  running: 'sync',
  waiting: 'person',
  review: 'eye',
  done: 'check',
  merged: 'git-merge',
  failed: 'error',
  cancelled: 'circle-slash',
  discarded: 'trash',
  interrupted: 'debug-disconnect',
};
