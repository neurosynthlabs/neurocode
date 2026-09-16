import { request } from "@/lib/api";
import type { Plugin, Skill, SlashCommand } from "@/types";

/* Skills, commands, hooks and plugins as the local API reads them off this machine: Claude Code's own
   files, the project's `.claude/` folder and the plugins it installed. Counts come from NeuroCode
   sessions only — Claude Code's own use of them is not something this app can see. */

/** A folder or file discovery looked in, and whether it was there. */
export interface ExtensionRoot {
  scope: string;
  path: string;
  exists: boolean;
}

/** A skill found on disk. `enabled` is the default (on); the Skills screen merges the pref over it. */
export interface LiveSkill extends Skill {
  /** A project id for a project skill, or 'all'. */
  project: string;
  /** Estimated from the body's length (characters ÷ 4). */
  tokens: number;
  /** NeuroCode sessions on this project that loaded it in the last 24 hours. */
  loads24h: number;
  /** Claude Code skills declare no rules; always empty, kept so the shape matches the sample. */
  rules: { when: string; then: string }[];
  author: string;
}

export interface LiveSkills {
  skills: LiveSkill[];
  /** Sessions on this project that asked something in the last 24 hours. */
  sessions24h: number;
  roots: ExtensionRoot[];
  /** Files whose front matter could not be read; they are listed with what could be. */
  unreadable: string[];
}

export interface LiveSkillDetail extends LiveSkill {
  body: string;
  truncated: boolean;
}

export interface LiveCommand extends Omit<SlashCommand, "model"> {
  /** The model named in the file. NeuroCode's router picks the lane; this is shown, not used. */
  model: string | null;
  tools: string[];
  source: string;
  truncated: boolean;
}

export interface CommandConflict {
  command: string;
  a: string;
  b: string;
  resolution: string;
}

export interface LiveCommands {
  commands: LiveCommand[];
  /** Per command key, the !`shell` lines in its body. NeuroCode never runs them. */
  shellLines: Record<string, string[]>;
  conflicts: CommandConflict[];
  roots: ExtensionRoot[];
}

export interface LiveHook {
  id: string;
  /** Claude Code's event name. An open set: new events appear in real settings files before any list knows them. */
  event: string;
  matcher: string;
  type: string;
  /** Redacted: anything that looked like a token, key or password is ***. */
  command: string;
  scope: "global" | "project" | "local" | "plugin";
  source: string;
  /** Derived from the event: exit code 2 refuses the action in Claude Code. */
  blocking: boolean;
  description: string;
  timeoutS: number | null;
}

export interface LiveHooks {
  hooks: LiveHook[];
  files: ExtensionRoot[];
}

export interface LivePlugin extends Plugin {
  /** False when the plugin's files are not on this machine, so its contents could not be counted. */
  providesKnown: boolean;
  enabledInClaude: boolean;
  /** Installed plugins only: whether its skills and commands appear in NeuroCode sessions. */
  usedInNeuroCode: boolean | null;
  path: string | null;
  /** What to type in Claude Code to install or uninstall it. */
  installCommand: string;
}

export interface LivePlugins {
  installed: LivePlugin[];
  marketplace: LivePlugin[];
  conflicts: CommandConflict[];
  /** When Claude Code last fetched install counts; null when it never has. */
  countsFetchedAt: string | null;
}

const q = (projectId: string, extra: Record<string, string> = {}) =>
  new URLSearchParams({ projectId, ...extra }).toString();

export const extensions = {
  skills: (projectId: string) =>
    request<LiveSkills>(`/extensions/skills?${q(projectId)}`),
  skill: (projectId: string, key: string) =>
    request<LiveSkillDetail>(
      `/extensions/skills/detail?${q(projectId, { key })}`,
    ),
  commands: (projectId: string) =>
    request<LiveCommands>(`/extensions/commands?${q(projectId)}`),
  hooks: (projectId: string) =>
    request<LiveHooks>(`/extensions/hooks?${q(projectId)}`),
  plugins: (projectId: string) =>
    request<LivePlugins>(`/extensions/plugins?${q(projectId)}`),
};
