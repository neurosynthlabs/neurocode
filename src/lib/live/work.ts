import { request, type RunDoc } from "@/lib/api";
import type { Tone } from "@/components/os";
import type { Project } from "@/types";

/* The work screens' own calls and the few helpers they share: the standing rules the runtime applies
   (GET /permissions/rules), sending a run back for changes (POST /runs/{ref}/rework), and turning the
   server's ISO timestamps into the days and clock times a feed is read by. */

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

const seg = encodeURIComponent;

export const work = {
  rules: () =>
    request<StandingRule[]>("/permissions/rules", {
      signal: AbortSignal.timeout(10_000),
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
