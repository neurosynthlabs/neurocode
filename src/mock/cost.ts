import type { CostBucket, CostDay } from '@/types';

export const costDays: CostDay[] = [
  { day: 'Aug 28', cost: 9.84, local: 0.00, remote: 9.84 },
  { day: 'Aug 29', cost: 11.27, local: 0.00, remote: 11.27 },
  { day: 'Aug 30', cost: 4.16, local: 0.00, remote: 4.16 },
  { day: 'Aug 31', cost: 2.08, local: 0.00, remote: 2.08 },
  { day: 'Sep 01', cost: 13.92, local: 0.00, remote: 13.92 },
  { day: 'Sep 02', cost: 12.41, local: 0.00, remote: 12.41 },
  { day: 'Sep 03', cost: 8.77, local: 0.00, remote: 8.77 },
  { day: 'Sep 04', cost: 7.19, local: 0.00, remote: 7.19 },
  { day: 'Sep 05', cost: 5.62, local: 0.00, remote: 5.62 },
  { day: 'Sep 06', cost: 2.94, local: 0.00, remote: 2.94 },
  { day: 'Sep 07', cost: 1.86, local: 0.00, remote: 1.86 },
  { day: 'Sep 08', cost: 7.48, local: 0.00, remote: 7.48 },
  { day: 'Sep 09', cost: 6.90, local: 0.00, remote: 6.90 },
  { day: 'Sep 10', cost: 6.31, local: 0.00, remote: 6.31 },
];

/** Local calls cost nothing in dollars — this is the electricity+amortisation estimate we still track. */
export const localShadowCost: number[] = [
  0.31, 0.36, 0.14, 0.07, 0.44, 0.41, 0.29, 0.24, 0.19, 0.11, 0.06, 0.26, 0.23, 0.22,
];

/** Calls per day, local vs remote — used for the split bars. */
export const callsByDay: { local: number; remote: number }[] = [
  { local: 18_402, remote: 14_918 }, { local: 21_774, remote: 16_204 },
  { local: 9_118, remote: 6_012 }, { local: 4_806, remote: 2_940 },
  { local: 26_311, remote: 19_884 }, { local: 24_007, remote: 17_446 },
  { local: 19_620, remote: 12_338 }, { local: 20_114, remote: 10_902 },
  { local: 21_886, remote: 8_774 }, { local: 12_440, remote: 4_118 },
  { local: 8_012, remote: 2_604 }, { local: 23_190, remote: 10_441 },
  { local: 24_866, remote: 9_708 }, { local: 24_252, remote: 9_912 },
];

export const byAgent: CostBucket[] = [
  { label: 'Backend Engineer', tokensIn: 3_182_400, tokensOut: 486_200, cost: 1.61, calls: 1_842, share: 25.5 },
  { label: 'Architect', tokensIn: 2_940_100, tokensOut: 212_800, cost: 1.08, calls: 604, share: 17.1 },
  { label: 'Code Reviewer', tokensIn: 1_884_600, tokensOut: 164_400, cost: 0.86, calls: 388, share: 13.6 },
  { label: 'AI Commander', tokensIn: 1_411_900, tokensOut: 298_700, cost: 0.74, calls: 512, share: 11.7 },
  { label: 'Database Engineer', tokensIn: 1_206_300, tokensOut: 118_900, cost: 0.61, calls: 466, share: 9.7 },
  { label: 'QA Engineer', tokensIn: 942_800, tokensOut: 174_100, cost: 0.48, calls: 731, share: 7.6 },
  { label: 'Researcher', tokensIn: 1_640_200, tokensOut: 61_300, cost: 0.34, calls: 196, share: 5.4 },
  { label: 'Vision Agent', tokensIn: 204_700, tokensOut: 88_400, cost: 0.28, calls: 79, share: 4.4 },
  { label: 'Security Engineer', tokensIn: 388_100, tokensOut: 41_600, cost: 0.16, calls: 118, share: 2.5 },
  { label: 'Documentation Agent', tokensIn: 262_400, tokensOut: 96_800, cost: 0.09, calls: 141, share: 1.4 },
  { label: 'DevOps Engineer', tokensIn: 148_900, tokensOut: 22_100, cost: 0.04, calls: 208, share: 0.6 },
  { label: 'Frontend Engineer', tokensIn: 2_310_000, tokensOut: 604_800, cost: 0.02, calls: 2_904, share: 0.3 },
];

export const byModel: CostBucket[] = [
  { label: 'DeepSeek-V3.2', tokensIn: 4_912_000, tokensOut: 1_204_000, cost: 1.94, calls: 312, share: 30.7 },
  { label: 'Qwen3-Coder-Next', tokensIn: 6_882_000, tokensOut: 1_641_000, cost: 1.82, calls: 1_104, share: 28.8 },
  { label: 'Kimi-K2.5', tokensIn: 1_106_000, tokensOut: 358_000, cost: 1.41, calls: 74, share: 22.3 },
  { label: 'GLM-4.7', tokensIn: 1_408_000, tokensOut: 249_000, cost: 0.51, calls: 268, share: 8.1 },
  { label: 'Qwen3-Next-80B', tokensIn: 1_882_000, tokensOut: 141_000, cost: 0.34, calls: 196, share: 5.4 },
  { label: 'Kimi-K2.5 Vision', tokensIn: 188_000, tokensOut: 61_000, cost: 0.28, calls: 18, share: 4.4 },
  { label: 'Qwen3-VL-32B', tokensIn: 214_000, tokensOut: 96_000, cost: 0.19, calls: 61, share: 3.0 },
  { label: 'Qwen3-Coder-30B (local)', tokensIn: 18_940_000, tokensOut: 4_112_000, cost: 0.00, calls: 2_941, share: 0.0 },
  { label: 'Qwen3-4B (local)', tokensIn: 9_204_000, tokensOut: 1_882_000, cost: 0.00, calls: 4_820, share: 0.0 },
  { label: 'Gemma3-4B (local)', tokensIn: 6_118_000, tokensOut: 904_000, cost: 0.00, calls: 1_387, share: 0.0 },
  { label: 'BGE-M3 (local)', tokensIn: 22_410_000, tokensOut: 0, cost: 0.00, calls: 9_140, share: 0.0 },
  { label: 'BGE-Reranker-v2 (local)', tokensIn: 8_804_000, tokensOut: 0, cost: 0.00, calls: 3_140, share: 0.0 },
];

export const byProject: CostBucket[] = [
  { label: 'Legacy ERP', tokensIn: 9_884_000, tokensOut: 1_602_000, cost: 4.12, calls: 4_918, share: 65.3 },
  { label: 'HIMS v3', tokensIn: 2_744_000, tokensOut: 418_000, cost: 1.06, calls: 1_204, share: 16.8 },
  { label: 'NeuroCode', tokensIn: 1_882_000, tokensOut: 388_000, cost: 0.61, calls: 806, share: 9.7 },
  { label: 'Taxi Marketplace', tokensIn: 906_000, tokensOut: 174_000, cost: 0.34, calls: 441, share: 5.4 },
  { label: 'FIFA Stats', tokensIn: 412_000, tokensOut: 66_000, cost: 0.18, calls: 188, share: 2.9 },
];

export const budget = {
  daily: 25.0,
  spent: 6.31,
  monthSpent: 94.75,
  monthDays: 10,
  projectedMonth: 189.5,
  lastMonth: 214.08,
  guard: 90,
};

export interface ExpensiveOp {
  id: string;
  op: string;
  task: string;
  agent: string;
  model: string;
  tokensIn: number;
  tokensOut: number;
  cost: number;
  at: string;
}

export const expensiveOps: ExpensiveOp[] = [
  { id: 'op-1', op: 'SP_CalculateTax full-body semantic read (1,142 lines)', task: 'TASK-492', agent: 'Architect', model: 'Kimi-K2.5', tokensIn: 148_200, tokensOut: 6_400, cost: 0.42, at: '11:41' },
  { id: 'op-2', op: 'Blast-radius sweep across 31 MST_TAX callers', task: 'TASK-495', agent: 'Architect', model: 'DeepSeek-V3.2', tokensIn: 612_000, tokensOut: 18_900, cost: 0.35, at: '10:18' },
  { id: 'op-3', op: 'Reviewer verdict on PR #1284 (+214 −61, 7 files)', task: 'TASK-492', agent: 'Code Reviewer', model: 'DeepSeek-V3.2', tokensIn: 388_400, tokensOut: 41_200, cost: 0.29, at: '11:38' },
  { id: 'op-4', op: 'Excel export profiling — 100k row query plan analysis', task: 'TASK-513', agent: 'Database Engineer', model: 'DeepSeek-V3.2', tokensIn: 296_100, tokensOut: 22_800, cost: 0.24, at: '09:52' },
  { id: 'op-5', op: 'Reports grid screenshot → JSX, 3 iterations', task: 'TASK-479', agent: 'Vision Agent', model: 'Kimi-K2.5 Vision', tokensIn: 96_800, tokensOut: 28_400, cost: 0.21, at: '10:44' },
  { id: 'op-6', op: 'Hinglish requirement compile + decomposition (9 subtasks)', task: 'TASK-517', agent: 'AI Commander', model: 'DeepSeek-V3.2', tokensIn: 184_600, tokensOut: 38_100, cost: 0.18, at: '08:31' },
  { id: 'op-7', op: 'Regression test authoring — 18 cases for interstate tax', task: 'TASK-492', agent: 'QA Engineer', model: 'Qwen3-Coder-Next', tokensIn: 211_400, tokensOut: 64_900, cost: 0.16, at: '11:09' },
  { id: 'op-8', op: 'CBIC place-of-supply circular research sweep (14 sources)', task: 'TASK-492', agent: 'Researcher', model: 'Qwen3-Next-80B', tokensIn: 604_800, tokensOut: 9_100, cost: 0.09, at: '09:14' },
];

export interface SavingsRow {
  label: string;
  localCalls: number;
  localCost: number;
  frontierCost: number;
  qualityDelta: string;
  honest: string;
}

export const savings: SavingsRow[] = [
  {
    label: 'Small diffs (< 40 lines, indexed repo)', localCalls: 2_941, localCost: 0,
    frontierCost: 11.84, qualityDelta: '−3 pts', honest: 'Local wins. Reviewer rejected 4 more local patches than remote ones.',
  },
  {
    label: 'Commit messages & log triage', localCalls: 4_820, localCost: 0,
    frontierCost: 4.16, qualityDelta: '−1 pt', honest: 'No measurable difference. Should never have been remote.',
  },
  {
    label: 'Tool-output summarisation', localCalls: 1_387, localCost: 0,
    frontierCost: 2.94, qualityDelta: '−2 pts', honest: 'Gemma3 drops the odd stack frame; Architect re-reads the raw output twice a day.',
  },
  {
    label: 'Embeddings (BGE-M3 vs hosted)', localCalls: 9_140, localCost: 0,
    frontierCost: 1.82, qualityDelta: '+1 pt', honest: 'Local is genuinely better here — domain-tuned on ERP sproc text.',
  },
  {
    label: 'Reranking (BGE-Reranker-v2)', localCalls: 3_140, localCost: 0,
    frontierCost: 0.94, qualityDelta: '+4 pts', honest: 'Local beats LLM-as-reranker and is 60× faster.',
  },
  {
    label: 'Legacy stored-proc semantics', localCalls: 0, localCost: 0,
    frontierCost: 0, qualityDelta: '−19 pts', honest: 'Tried local for a week. It hallucinated MST_TAX column names. Routed back to Kimi permanently.',
  },
  {
    label: 'HIGH-risk review verdicts', localCalls: 0, localCost: 0,
    frontierCost: 0, qualityDelta: '−14 pts', honest: 'Local missed the CGST/SGST reversal in BUG-883. Not a cost decision.',
  },
];

export const savingsTotals = {
  localCost: 0,
  frontierCost: 21.7,
  actualToday: 6.31,
  ifAllFrontier: 28.01,
  note: 'Local hardware is a sunk M4 Max already bought for other work; power ≈ $0.22/day is not counted below.',
};
