import type { ModelEntry, RoutingRule } from '@/types';

export const models: ModelEntry[] = [
  {
    id: 'deepseek-v32', name: 'DeepSeek-V3.2', vendor: 'DeepSeek', kind: 'reasoning', hosting: 'remote',
    contextK: 164, inPer1M: 0.28, outPer1M: 0.42, status: 'ready', latencyMs: 1840, quality: 94,
    calls24h: 312, cost24h: 1.94,
    notes: 'Commander + Architect default. Best on 14-year-old C# it has never seen.',
  },
  {
    id: 'kimi-k25', name: 'Kimi-K2.5', vendor: 'Moonshot', kind: 'reasoning', hosting: 'remote',
    contextK: 256, inPer1M: 0.55, outPer1M: 2.20, status: 'ready', latencyMs: 2210, quality: 96,
    calls24h: 74, cost24h: 1.41,
    notes: 'Only for ADR-grade decisions and vision. Expensive per output token — router keeps it rare.',
  },
  {
    id: 'qwen3-coder-next', name: 'Qwen3-Coder-Next', vendor: 'Alibaba', kind: 'coding', hosting: 'remote',
    contextK: 262, inPer1M: 0.18, outPer1M: 0.72, status: 'ready', latencyMs: 1120, quality: 91,
    calls24h: 1104, cost24h: 1.82,
    notes: 'Primary coder. Holds the whole TaxService.cs + SP_CalculateTax pair in one context.',
  },
  {
    id: 'glm-47', name: 'GLM-4.7', vendor: 'Zhipu', kind: 'coding', hosting: 'remote',
    contextK: 200, inPer1M: 0.21, outPer1M: 0.84, status: 'ready', latencyMs: 1390, quality: 88,
    calls24h: 268, cost24h: 0.51,
    notes: 'Fallback coder. Slightly weaker on stored-proc SQL, better on Razor views.',
  },
  {
    id: 'qwen3-coder-30b', name: 'Qwen3-Coder-30B', vendor: 'Ollama (local)', kind: 'coding', hosting: 'local',
    contextK: 128, inPer1M: 0, outPer1M: 0, status: 'ready', latencyMs: 640, quality: 82,
    calls24h: 2941, cost24h: 0,
    notes: 'M4 Max 128GB, q5_K_M, 38 tok/s. Does 71% of all coding turns for free.',
  },
  {
    id: 'qwen3-next-80b', name: 'Qwen3-Next-80B', vendor: 'Alibaba', kind: 'reasoning', hosting: 'remote',
    contextK: 262, inPer1M: 0.14, outPer1M: 0.56, status: 'ready', latencyMs: 1560, quality: 89,
    calls24h: 196, cost24h: 0.34,
    notes: 'Researcher default — long web sweeps where output stays short.',
  },
  {
    id: 'qwen3-vl', name: 'Qwen3-VL-32B', vendor: 'Alibaba', kind: 'vision', hosting: 'remote',
    contextK: 128, inPer1M: 0.24, outPer1M: 0.90, status: 'ready', latencyMs: 2740, quality: 85,
    calls24h: 61, cost24h: 0.19,
    notes: 'Screenshot → JSX. Loses table alignment on the legacy Reports grid; Kimi handles those.',
  },
  {
    id: 'kimi-vision', name: 'Kimi-K2.5 Vision', vendor: 'Moonshot', kind: 'vision', hosting: 'remote',
    contextK: 256, inPer1M: 0.55, outPer1M: 2.20, status: 'ready', latencyMs: 3120, quality: 93,
    calls24h: 18, cost24h: 0.28,
    notes: 'Visual-diff arbiter. Called only when Qwen3-VL confidence < 0.7.',
  },
  {
    id: 'qwen3-4b', name: 'Qwen3-4B', vendor: 'Ollama (local)', kind: 'small', hosting: 'local',
    contextK: 32, inPer1M: 0, outPer1M: 0, status: 'ready', latencyMs: 180, quality: 64,
    calls24h: 4820, cost24h: 0,
    notes: 'Commit messages, file-name guesses, log classification, Hinglish language detect.',
  },
  {
    id: 'gemma3-4b', name: 'Gemma3-4B', vendor: 'Ollama (local)', kind: 'small', hosting: 'local',
    contextK: 128, inPer1M: 0, outPer1M: 0, status: 'ready', latencyMs: 210, quality: 66,
    calls24h: 1387, cost24h: 0,
    notes: 'Summarises tool output before it enters an expensive context. Saves ~2.1M tokens/day.',
  },
  {
    id: 'qwen3-30b-local', name: 'Qwen3-30B-A3B', vendor: 'Ollama (local)', kind: 'reasoning', hosting: 'local',
    contextK: 64, inPer1M: 0, outPer1M: 0, status: 'loading', latencyMs: 0, quality: 78,
    calls24h: 214, cost24h: 0,
    notes: 'Warming — 18.4GB weights loading into VRAM, ETA 40s. Router is skipping it meanwhile.',
  },
  {
    id: 'bge-m3', name: 'BGE-M3', vendor: 'BAAI (local)', kind: 'embedding', hosting: 'local',
    contextK: 8, inPer1M: 0, outPer1M: 0, status: 'ready', latencyMs: 22, quality: 90,
    calls24h: 9140, cost24h: 0,
    notes: '1024-dim dense + sparse. Indexed 412,880 chunks of ERP code and 2,946 stored procs.',
  },
  {
    id: 'qwen3-embedding', name: 'Qwen3-Embedding-0.6B', vendor: 'Alibaba (local)', kind: 'embedding', hosting: 'local',
    contextK: 32, inPer1M: 0, outPer1M: 0, status: 'ready', latencyMs: 14, quality: 86,
    calls24h: 2610, cost24h: 0,
    notes: 'Used for the hinglish_pairs collection — better on romanised Hindi than BGE-M3.',
  },
  {
    id: 'bge-reranker-v2', name: 'BGE-Reranker-v2', vendor: 'BAAI (local)', kind: 'reranker', hosting: 'local',
    contextK: 8, inPer1M: 0, outPer1M: 0, status: 'ready', latencyMs: 41, quality: 92,
    calls24h: 3140, cost24h: 0,
    notes: 'top_k 40 → 8. Lifted impact-analysis recall from 0.71 to 0.88 (see eval suite).',
  },
];

export const routingRules: RoutingRule[] = [
  { id: 'rr-01', when: 'Architecture / ADR / blast-radius analysis', route: 'DeepSeek-V3.2', fallback: 'Kimi-K2.5', enabled: true, hits24h: 188 },
  { id: 'rr-02', when: 'Code write, refactor, patch (C# / TS / SQL)', route: 'Qwen3-Coder-Next', fallback: 'GLM-4.7', enabled: true, hits24h: 1104 },
  { id: 'rr-03', when: 'Code write when diff < 40 lines & repo indexed', route: 'Qwen3-Coder-30B (local)', fallback: 'Qwen3-Coder-Next', enabled: true, hits24h: 2941 },
  { id: 'rr-04', when: 'Screenshot / visual diff / UI from image', route: 'Kimi-K2.5', fallback: 'Qwen3-VL-32B', enabled: true, hits24h: 79 },
  { id: 'rr-05', when: 'Simple task: commit msg, log triage, rename', route: 'Qwen3-4B (local)', fallback: 'Gemma3-4B (local)', enabled: true, hits24h: 4820 },
  { id: 'rr-06', when: 'Tool-output summarisation before expensive context', route: 'Gemma3-4B (local)', fallback: 'Qwen3-4B (local)', enabled: true, hits24h: 1387 },
  { id: 'rr-07', when: 'Embedding — code, sprocs, ADRs', route: 'BGE-M3', fallback: 'Qwen3-Embedding-0.6B', enabled: true, hits24h: 9140 },
  { id: 'rr-08', when: 'Embedding — Hinglish requirement pairs', route: 'Qwen3-Embedding-0.6B', fallback: 'BGE-M3', enabled: true, hits24h: 2610 },
  { id: 'rr-09', when: 'Rerank retrieved chunks before context assembly', route: 'BGE-Reranker-v2', fallback: 'none (pass-through top_k)', enabled: true, hits24h: 3140 },
  { id: 'rr-10', when: 'Long web research sweep, short answer', route: 'Qwen3-Next-80B', fallback: 'GLM-4.7', enabled: true, hits24h: 196 },
  { id: 'rr-11', when: 'Reviewer verdict on HIGH-risk diff', route: 'DeepSeek-V3.2', fallback: 'Kimi-K2.5', enabled: true, hits24h: 41 },
  { id: 'rr-12', when: 'Hinglish requirement compile (Commander)', route: 'DeepSeek-V3.2', fallback: 'Qwen3-Next-80B', enabled: true, hits24h: 63 },
  { id: 'rr-13', when: 'Legacy stored-proc semantics (>800 lines)', route: 'Kimi-K2.5', fallback: 'DeepSeek-V3.2', enabled: false, hits24h: 0 },
  { id: 'rr-14', when: 'Budget ≥ 90% of daily cap', route: 'Local-only (Qwen3-Coder-30B / Qwen3-4B)', fallback: 'reject with warning', enabled: true, hits24h: 0 },
];

export const routerToggles = [
  {
    id: 'auto', label: 'Automatic model selection', on: true,
    onText: 'Router picks per task class. 14 rules active, 21,113 decisions in 24h.',
    offText: 'Every agent uses the model pinned in its own config — Commander would go DeepSeek for commit messages too.',
  },
  {
    id: 'cost', label: 'Cost optimization', on: true,
    onText: 'Downgrades to the cheapest model that passed the eval bar for that task class. Saved $18.42 today.',
    offText: 'Router always picks the highest-quality candidate — projected day cost rises to ~$24.73.',
  },
  {
    id: 'fallback', label: 'Fallback models', on: true,
    onText: 'On timeout or 5xx, retry once on the fallback. 23 fallbacks fired today, 21 succeeded.',
    offText: 'A DeepSeek 502 fails the whole agent turn — TASK-492 stalled 11 min this way last Thursday.',
  },
  {
    id: 'localfirst', label: 'Local model first', on: true,
    onText: 'Try Qwen3-Coder-30B first; escalate only when self-check confidence < 0.72. 71% of turns stay local.',
    offText: 'Everything goes remote. Latency drops ~480ms, cost rises about 4.1× on coding turns.',
  },
];

export const routerSplit = {
  localPct: 71,
  remotePct: 29,
  localCalls: 24_252,
  remoteCalls: 9_912,
  savedToday: 18.42,
  wouldHaveCost: 24.73,
  actualCost: 6.31,
};

export const routerDiagram = `                              ┌──────────────────────────┐
   task + risk + size  ─────▶ │      MODEL  ROUTER       │
   agent + context tokens     │  rules 14 · hits 21,113  │
                              └────────────┬─────────────┘
                                           │
             ┌─────────────────────────────┼─────────────────────────────┐
             ▼                             ▼                             ▼
   ┌───────────────────┐       ┌───────────────────┐        ┌───────────────────┐
   │     EXPENSIVE     │       │      MEDIUM       │        │       LOCAL       │
   │  Kimi-K2.5        │       │  Qwen3-Coder-Next │        │  Qwen3-Coder-30B  │
   │  DeepSeek-V3.2    │       │  GLM-4.7          │        │  Qwen3-4B         │
   │                   │       │  Qwen3-Next-80B   │        │  Gemma3-4B        │
   ├───────────────────┤       ├───────────────────┤        ├───────────────────┤
   │ ADR · HIGH-risk   │       │ normal code write │        │ small diffs       │
   │ review · vision   │       │ research sweeps   │        │ commit msgs       │
   │ 404 calls  $3.63  │       │ 1,568 calls $2.68 │        │ 24,252 calls $0.00│
   └───────────────────┘       └───────────────────┘        └───────────────────┘
        3% of calls                  6% of calls                 71% of calls
                                                          (+20% embed/rerank, local)`;
