import { request } from "@/lib/api";
import type { Plan } from "@/types";

/* A project's instruction files and a plan's grounding, as the API reports them. The files are read off
   the checkout on every request — nothing about them is stored — so what the Instructions tab lists is
   what the compiler and a session are handed right now. */

/** One file at the checkout root, under .claude/rules or .neurocode/rules, or pulled in by an `@import`. */
export interface InstructionFile {
  path: string;
  sha1: string;
  /** Its size on disk. */
  bytes: number;
  scope: "project" | "rules";
  /** The rule glob a target matched; null for a file that always applies, or a rule that did not. */
  matched: string | null;
  /** A rule's `paths:` globs; empty when it applies to everything. */
  paths: string[];
  applied: boolean;
  /** The file whose `@import` brought this one in; null for a file read from its own place. */
  importedBy: string | null;
  /** The cap cut this file short, or left it out. */
  cut: boolean;
}

export interface RefusedImport {
  /** As it was written after the `@`. */
  path: string;
  why: string;
  /** The file the import was in. */
  from: string;
}

export interface InstructionsDoc {
  files: InstructionFile[];
  refused: RefusedImport[];
  /** What the model is handed, after comments are stripped and the cap applied. */
  bytes: number;
  capped: boolean;
  cap: number;
}

/** What checking a plan's named files against the code index found. */
export interface FileCheck {
  /** False when the project had no index to check against: then nothing is called new. */
  checked: boolean;
  /** Named, and not in the index: files the change would create. */
  newFiles: string[];
  /** A bare name that matched several indexed paths → a few of them. */
  ambiguous: Record<string, string[]>;
}

export interface GroundingRef {
  /** "instructions", "code" or "doc". */
  kind: string;
  /** A code or document piece's ref (path#symbol:line), or an instruction file's sha1 prefix. */
  ref: string;
  path: string;
}

/** A plan as the API sends it since plans were grounded. Older plans carry empty lists and no check. */
export type GroundedPlan = Plan & {
  acceptanceCriteria?: string[];
  /** True once a person changed the criteria the compiler proposed; a re-compile keeps them. */
  criteriaEdited?: boolean;
  fileCheck?: FileCheck | null;
  grounding?: GroundingRef[];
};

const seg = encodeURIComponent;
/** Drafting compiles a plan, so it waits as long as a compile does. */
const modelTimeout = () => AbortSignal.timeout(180_000);

export const instructionsApi = {
  /** With targets, a rule scoped by `paths:` says whether it would apply to those files. */
  list: (projectId: string, targets: string[] = []) => {
    const query = targets.map((t) => `target=${seg(t)}`).join("&");
    return request<InstructionsDoc>(
      `/projects/${seg(projectId)}/instructions${query ? `?${query}` : ""}`,
    );
  },
  /** Compile a plan that writes the project's first AGENTS.md from its code index. */
  draft: (projectId: string) =>
    request<GroundedPlan & { task: { ref: string } }>(
      `/projects/${seg(projectId)}/instructions/draft`,
      { method: "POST", signal: modelTimeout() },
    ),
  /** A person's acceptance criteria for a plan not yet under way (plans:decide). */
  setCriteria: (ref: string, acceptanceCriteria: string[]) =>
    request<GroundedPlan>(`/plans/${seg(ref)}`, {
      method: "PATCH",
      json: { acceptanceCriteria },
    }),
};

/** "12.4 KB" — sizes the Instructions tab and the Plans screen print. */
export function kb(bytes: number): string {
  return bytes < 1_000 ? `${bytes} B` : `${(bytes / 1_000).toFixed(1)} KB`;
}
