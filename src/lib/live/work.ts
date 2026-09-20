import { request, type RunDoc } from "@/lib/api";
import type {
  ExtensionRoot,
  LiveHook,
  LivePlugin,
  LivePlugins,
} from "@/lib/live/extensions";
import type { Tone } from "@/components/os";
import type { Project } from "@/types";

/* The work screens' own calls and the few helpers they share: the standing rules the runtime applies
   (GET /permissions/rules), the tool rules a person writes ahead of time (/permissions/tool-rules),
   sending a run back for changes (POST /runs/{ref}/rework), and turning the server's ISO timestamps
   into the days and clock times a feed is read by. */

/** A project's kept answer to running its own tests — the one rule the runtime consults before it acts. */
export interface StandingRule {
  projectId: string;
  projectName: string;
  rule: "tests";
  /** The test command the project would run now; null when none is found on this machine. */
  command: string | null;
  answer: "allowed" | "refused";
  decidedAt: string | null;
  /** The person whose decision wrote it; null when no person's decision is recorded. */
  decidedBy: string | null;
}

/** What a tool rule is about: a path written or read, a command line, a URL, a search, an MCP
    `server/tool`, a custom tool's name, or a repository's hook as `event/command`. */
export type RuleTool =
  | "edit"
  | "command"
  | "read"
  | "web_fetch"
  | "web_search"
  | "mcp"
  | "tool"
  | "hook";
export type RuleAction = "allow" | "ask" | "deny";

/** Allow, ask or deny, for a tool and a glob over what it acts on — for the workspace, or one project. */
export interface ToolRule {
  id: number;
  /** Null: the rule holds across the workspace. */
  projectId: string | null;
  projectName: string | null;
  tool: RuleTool;
  pattern: string;
  action: RuleAction;
  note: string;
  /** The person who wrote it; null once their account is gone. */
  createdBy: string | null;
  createdAt: string | null;
  updatedAt: string | null;
}

/** What would happen, and the rule that says so (null when no rule covers it, which means ask). */
export interface RuleDecision {
  action: RuleAction;
  ruleId: number | null;
  why: string;
}

export interface RuleTrial extends RuleDecision {
  tool: RuleTool;
  subject: string;
  projectId: string | null;
  rule: ToolRule | null;
}

export interface ToolRuleDraft {
  tool: RuleTool;
  pattern: string;
  action: RuleAction;
  note: string;
  projectId: string | null;
}

/** The tools in the order the screen offers them: what each one's pattern is matched against, and what
    consults its rules today — null where nothing does yet, so the screen can say a rule is only kept. */
export const RULE_TOOLS: {
  id: RuleTool;
  label: string;
  subject: string;
  example: string;
  consultedBy: string | null;
}[] = [
  { id: "edit", label: "Edit files", subject: "path", example: "src/billing/**", consultedBy: "every file an agent writes in a run — Live Runs" },
  { id: "command", label: "Run commands", subject: "command line", example: "npm test*", consultedBy: "every test and check a run executes — Live Runs" },
  { id: "read", label: "Read files", subject: "path", example: ".env*", consultedBy: "every file a session reads — Sessions" },
  {
    id: "web_fetch",
    label: "Fetch web pages",
    subject: "URL",
    example: "https://docs.python.org/*",
    consultedBy: "every page read — Settings → Web, and research that searches the web",
  },
  {
    id: "web_search",
    label: "Search the web",
    subject: "query",
    example: "*password*",
    consultedBy: "every search — Settings → Web, and research that searches the web",
  },
  {
    id: "mcp",
    label: "Call MCP tools",
    subject: "server/tool",
    example: "github/search_*",
    consultedBy: "every tool call — Try on the MCP screen",
  },
  {
    id: "tool",
    label: "Call custom tools",
    subject: "custom tool name",
    example: "deploy_preview",
    consultedBy:
      "every call of a tool defined on Commands → Custom tools. With no rule it does not run at all",
  },
  {
    id: "hook",
    label: "Run repository hooks",
    subject: "event/command",
    example: "PostToolUse/*prettier*",
    consultedBy:
      "every hook on the Hooks screen. With no rule it is shown and never run",
  },
];

/** The two kinds where no rule is a no rather than a question — the screen says so beside them. */
export const REFUSED_UNLESS_ALLOWED: RuleTool[] = ["tool", "hook"];

/** Who may write tool rules; reading and trying them needs only a session. */
export const RULES_PERMISSION = "rules:manage";

const seg = encodeURIComponent;

export const work = {
  rules: () =>
    request<StandingRule[]>("/permissions/rules", {
      signal: AbortSignal.timeout(10_000),
    }),
  /** Tool rules, workspace first. `project` is a project id, or "workspace" for only the workspace's own. */
  toolRules: (project?: string) =>
    request<ToolRule[]>(
      `/permissions/tool-rules?${new URLSearchParams({ limit: "200", ...(project ? { project } : {}) })}`,
      { signal: AbortSignal.timeout(10_000) },
    ),
  addToolRule: (draft: ToolRuleDraft) =>
    request<ToolRule>("/permissions/tool-rules", {
      method: "POST",
      json: draft,
    }),
  changeToolRule: (
    id: number,
    patch: Partial<Pick<ToolRule, "pattern" | "action" | "note">>,
  ) =>
    request<ToolRule>(`/permissions/tool-rules/${id}`, {
      method: "PATCH",
      json: patch,
    }),
  removeToolRule: (id: number) =>
    request<{ ok: true; id: number }>(`/permissions/tool-rules/${id}`, {
      method: "DELETE",
    }),
  /** What would happen, without doing anything. */
  tryToolRule: (tool: RuleTool, subject: string, projectId: string | null) =>
    request<RuleTrial>("/permissions/tool-rules/test", {
      method: "POST",
      json: { tool, subject, projectId },
    }),
  /** Sends a run back: the same plan again as a new run told these notes and the review's findings. Returns the new run. */
  rework: (ref: string, notes: string) =>
    request<RunDoc>(`/runs/${seg(ref)}/rework`, {
      method: "POST",
      json: { notes },
      signal: AbortSignal.timeout(60_000),
    }),
};

/* ── what a person defines, allows and installs on the extension screens ───────────────────────────
   These call /extensions, not /permissions, but they belong beside the tool rules rather than beside
   the reading calls in live/extensions.ts: every one of them is a person saying yes to something that
   then runs on this machine, and each is refused without the right that governs it. */

/** A tool a person defined: a command on this machine, or an HTTP call, with a schema for its arguments. */
export interface CustomTool {
  id: string;
  name: string;
  description: string;
  kind: "command" | "http";
  /** What it runs or calls, and `arguments`, its JSON Schema. Stored exactly as the server checked it. */
  spec: Record<string, unknown>;
  enabled: boolean;
  /** Null: the workspace's own tool, offered in every project. */
  projectId: string | null;
  projectName: string | null;
  createdBy: string | null;
  createdAt: string | null;
  updatedAt: string | null;
}

export interface CustomToolDraft {
  name: string;
  description: string;
  kind: "command" | "http";
  spec: Record<string, unknown>;
  projectId: string | null;
}

/** What one firing of a hook did. `ran` is false when a rule did not allow it — `why` says which. */
export interface HookFiring {
  hookId: string;
  event: string;
  command: string;
  ran: boolean;
  exitCode: number | null;
  output: string;
  blocked: boolean;
  why: string;
  ms: number;
}

/** What a plugin this workspace installed brought with it. */
export interface InstalledPlugin {
  id: string;
  name: string;
  path: string;
  provides: Record<string, number>;
  source: string;
  sourceKind: "git" | "path";
}

/** A hook with what this workspace's rules say about it today — which is not a property of the file
    on disk, so it arrives beside the hook rather than inside it. */
export interface GovernedHook extends LiveHook {
  /** True only when a `hook` rule allows it. Anything else is shown and never run. */
  allowed: boolean;
  action: RuleAction;
  ruleId: number | null;
  why: string;
  /** A `hook` rule's pattern that would allow exactly this one, built from the redacted command — the
      `***` redaction leaves behind is itself a wildcard, so it still matches what would really run. The
      unredacted command never leaves the server. */
  pattern: string;
}

export interface GovernedHooks {
  hooks: GovernedHook[];
  files: ExtensionRoot[];
  /** False when machine access is off, or this project has no checkout here: then nothing can run. */
  canRun: boolean;
  checkout: string | null;
  allowed: number;
}

/** One line of the registry file: a plugin somebody wrote down as worth offering. Nothing is fetched. */
export interface RegistryEntry {
  name: string;
  source: string;
  description: string;
  publisher: string;
  category: string;
}

export interface PluginRegistry {
  path: string;
  exists: boolean;
  name: string;
  plugins: RegistryEntry[];
}

/** A plugin card, with the two facts that separate one this workspace installed from Claude Code's own. */
export interface PluginCard extends LivePlugin {
  /** True for a plugin in this workspace's own folder — the only kind this server can remove. */
  workspace: boolean;
  source: string;
  sourceKind: string;
}

export interface PluginsView extends Omit<LivePlugins, "installed" | "marketplace"> {
  installed: PluginCard[];
  marketplace: PluginCard[];
  registry: PluginRegistry;
  workspaceRoot: string;
}

/** Defining a tool and installing a plugin both say what the runtime may reach for. */
export const EXTENSIONS_PERMISSION = "mcp:manage";
/** Reading the hooks list needs this already; running one allowed hook needs no more. */
export const HOOKS_PERMISSION = "settings:write";

const scoped = (projectId: string | null) =>
  projectId ? `?projectId=${seg(projectId)}` : "";

export const extensionsAdmin = {
  /** The hooks configured here, each saying whether a rule allows it or it is only shown. */
  hooks: (projectId: string | null) =>
    request<GovernedHooks>(`/extensions/hooks${scoped(projectId)}`),
  /** Installed plugins — Claude Code's and this workspace's own — and the registry file's offers. */
  plugins: (projectId: string | null) =>
    request<PluginsView>(`/extensions/plugins${scoped(projectId)}`),
  tools: (projectId: string | null) =>
    request<CustomTool[]>(`/extensions/tools${scoped(projectId)}`, {
      signal: AbortSignal.timeout(10_000),
    }),
  defineTool: (draft: CustomToolDraft) =>
    request<CustomTool>("/extensions/tools", { method: "POST", json: draft }),
  changeTool: (
    id: string,
    patch: {
      description?: string;
      spec?: Record<string, unknown>;
      enabled?: boolean;
    },
  ) =>
    request<CustomTool>(`/extensions/tools/${seg(id)}`, {
      method: "PATCH",
      json: patch,
    }),
  removeTool: (id: string) =>
    request<{ ok: true; id: string }>(`/extensions/tools/${seg(id)}`, {
      method: "DELETE",
    }),
  /** Fire one hook now. It runs only if a `hook` rule allows it, exactly as at its own event. */
  runHook: (hookId: string, projectId: string | null) =>
    request<HookFiring>(
      `/extensions/hooks/${seg(hookId)}/run${scoped(projectId)}`,
      { method: "POST", signal: AbortSignal.timeout(90_000) },
    ),
  installPlugin: (source: string, name: string) =>
    request<InstalledPlugin>("/extensions/plugins/install", {
      method: "POST",
      json: { source, name },
      signal: AbortSignal.timeout(180_000),
    }),
  removePlugin: (name: string) =>
    request<{ ok: true; id: string; name: string }>(
      `/extensions/plugins/${seg(name)}`,
      { method: "DELETE" },
    ),
};

/** The review's severities as the runtime writes them. */
export const SEVERITY_TONE: Record<string, Tone> = {
  HIGH: "danger",
  MEDIUM: "warn",
  LOW: "info",
};

/** A project's name, or "Workspace" for what happened to the workspace rather than one project. */
export const projectLabel = (
  projects: Project[],
  id: string | null | undefined,
) => (id ? (projects.find((p) => p.id === id)?.name ?? id) : "Workspace");

const localDay = (d: Date) =>
  `${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}`;

/** A calendar day on this machine's clock, to group by. */
export const dayKey = (iso: string) => localDay(new Date(iso));

/** "Today" · "Yesterday" · "Mon 14 Sep" · "Mon 14 Sep 2025". */
export function dayLabel(iso: string): string {
  const at = new Date(iso);
  const today = new Date();
  const yesterday = new Date(
    today.getFullYear(),
    today.getMonth(),
    today.getDate() - 1,
  );
  if (localDay(at) === localDay(today)) return "Today";
  if (localDay(at) === localDay(yesterday)) return "Yesterday";
  const year =
    at.getFullYear() === today.getFullYear() ? "" : ` ${at.getFullYear()}`;
  return `${at.toLocaleString("en", { weekday: "short" })} ${at.getDate()} ${at.toLocaleString("en", { month: "short" })}${year}`;
}

/** "18:40:07" on this machine's clock. */
export const clock = (iso: string) => new Date(iso).toTimeString().slice(0, 8);
