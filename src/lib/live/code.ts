import { request, type CodeSummary } from '@/lib/api';

/* Code Intelligence's live shapes beyond the shared summary: what read each language. Every language
   a bundled grammar covers is read by its syntax tree; Python by its own `ast`; T-SQL by patterns. */

export type CodeParser = 'python-ast' | 'tree-sitter' | 'patterns';

/** One language of the index: its files, the symbols read out of them, and the parser that read them. */
export interface CodeParsing {
  language: string;
  files: number;
  symbols: number;
  /** null: the language was seen, and nothing on this machine reads it — its files are counted only. */
  parser: CodeParser | null;
}

export interface LiveCodeSummary extends CodeSummary {
  /** Most files first. Present once the project is indexed. */
  parsing?: CodeParsing[];
  /** The languages seen and not read, by name. */
  unparsed?: string[];
}

const seg = encodeURIComponent;

export const codeApi = {
  summary: (pid: string) => request<LiveCodeSummary>(`/projects/${seg(pid)}/code`, { signal: AbortSignal.timeout(15_000) }),
};

/** The parsers of an index, each with the languages it read: "tree-sitter" → ["Go", "Rust", …]. */
export function byParser(parsers: Record<string, string>): [string, string[]][] {
  const out = new Map<string, string[]>();
  for (const [lang, parser] of Object.entries(parsers)) out.set(parser, [...(out.get(parser) ?? []), lang]);
  return [...out.entries()].sort((a, b) => b[1].length - a[1].length);
}
