import { request, type RunDoc } from "@/lib/api";
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

/** What a tool rule is about: a path written or read, a command line, a URL, a search, an MCP `server/tool`. */
export type RuleTool =
  | "edit"
  | "command"
  | "read"
  | "web_fetch"
  | "web_search"
  | "mcp";
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
  { id: "edit", label: "Edit files", subject: "path", example: "src/billing/**", consultedBy: null },
  { id: "command", label: "Run commands", subject: "command line", example: "npm test*", consultedBy: null },
  { id: "read", label: "Read files", subject: "path", example: ".env*", consultedBy: null },
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
];

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
