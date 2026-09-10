import type { WorkflowDef, EvalSuite, EvalCase, Session } from '@/types';

const P = (id: string, title: string, detail: string, mode: WorkflowDef['phases'][number]['mode'], agents: number, state: WorkflowDef['phases'][number]['state']) =>
  ({ id, title, detail, mode, agents, state });

export const workflows: WorkflowDef[] = [
  { id: 'w1', name: 'legacy-module-audit', scope: 'project', trigger: 'manual · /audit <module>', runs: 14, avgAgents: 11, avgMinutes: 22, lastRun: '2 h ago', lastResult: 'success',
    description: 'Fans one agent per audit dimension across a legacy module, deduplicates what they find, then makes three independent skeptics try to refute each finding before it is allowed to reach you.',
    phases: [
      P('p1', 'Map', 'Parse the module, its callers and its database surface', 'single', 1, 'done'),
      P('p2', 'Find', 'One agent per dimension: correctness · security · performance · contracts · dead code · tests', 'parallel', 6, 'done'),
      P('p3', 'Dedupe', 'Plain code, not an agent — merge findings by file and line', 'single', 0, 'done'),
      P('p4', 'Verify', 'Three skeptics per finding, each prompted to refute. Majority kills it.', 'parallel', 3, 'done'),
      P('p5', 'Synthesise', 'Rank survivors by blast radius and write the report', 'single', 1, 'done'),
    ] },
  { id: 'w2', name: 'requirement-to-pr', scope: 'global', trigger: 'automatic · on requirement submit', runs: 118, avgAgents: 7, avgMinutes: 34, lastRun: '12 min ago', lastResult: 'success',
    description: 'The main loop. Compiles a requirement, plans it, implements it across parallel worktrees, tests it for real, reviews it, and stops at the human merge gate.',
    phases: [
      P('p1', 'Compile', 'Hinglish → business → technical requirement', 'single', 1, 'done'),
      P('p2', 'Analyse', 'Architecture and impact in parallel', 'parallel', 2, 'done'),
      P('p3', 'Implement', 'Frontend · backend · database, each in its own worktree', 'pipeline', 3, 'active'),
      P('p4', 'Verify', 'Tests run for real; output attached', 'single', 1, 'todo'),
      P('p5', 'Review', 'Reviewer round-trips until clean', 'loop', 1, 'todo'),
      P('p6', 'Gate', 'Stops. Waits for you.', 'single', 0, 'todo'),
    ] },
  { id: 'w3', name: 'db-contract-sweep', scope: 'project', trigger: 'weekly · Sunday 02:00', runs: 9, avgAgents: 9, avgMinutes: 41, lastRun: '2 d ago', lastResult: 'success',
    description: 'Walks every stored procedure, maps who calls it, and reports the ones nothing calls any more — the safest deletions in a legacy system, and the ones nobody dares make.',
    phases: [
      P('p1', 'Enumerate', '1,148 procedures across 382 tables', 'single', 1, 'done'),
      P('p2', 'Trace', 'Callers from C#, T-SQL, jobs and reports — four angles in parallel', 'parallel', 4, 'done'),
      P('p3', 'Classify', 'live · deprecated · dead · unknown', 'pipeline', 4, 'done'),
      P('p4', 'Report', '12 dead, 31 deprecated, 6 unknown', 'single', 1, 'done'),
    ] },
  { id: 'w4', name: 'architecture-judge-panel', scope: 'global', trigger: 'manual · /design <problem>', runs: 6, avgAgents: 8, avgMinutes: 28, lastRun: '30 min ago', lastResult: 'success',
    description: 'Three independent designs from deliberately different angles, scored by independent judges, then synthesised from the winner while grafting the best ideas from the runners-up.',
    phases: [
      P('p1', 'Design', 'MVP-first · risk-first · scale-first, blind to each other', 'parallel', 3, 'done'),
      P('p2', 'Judge', 'Each design scored on correctness, cost, reversibility', 'parallel', 3, 'done'),
      P('p3', 'Synthesise', 'Winner plus grafts, written as an ADR', 'single', 1, 'done'),
    ] },
  { id: 'w5', name: 'flaky-test-hunt', scope: 'project', trigger: 'nightly · 03:00', runs: 22, avgAgents: 5, avgMinutes: 48, lastRun: '11 h ago', lastResult: 'partial',
    description: 'Reruns the suite until two consecutive rounds surface nothing new. A counter would miss the tail; dryness is the only honest stop condition.',
    phases: [
      P('p1', 'Round', 'Full suite, isolated, randomised order', 'loop', 1, 'done'),
      P('p2', 'Diff', 'Compare against the previous round', 'single', 0, 'done'),
      P('p3', 'Attribute', 'Timing · shared state · ordering · environment', 'parallel', 4, 'done'),
    ] },
  { id: 'w6', name: 'screenshot-to-fix', scope: 'global', trigger: 'automatic · on screenshot attach', runs: 31, avgAgents: 4, avgMinutes: 12, lastRun: '1 h ago', lastResult: 'success',
    description: 'A screenshot becomes a located component, a patch, and a Playwright screenshot proving the patch worked — without you naming a single file.',
    phases: [
      P('p1', 'See', 'Vision model reads the UI', 'single', 1, 'done'),
      P('p2', 'Locate', 'Match to a component in the parsed tree', 'single', 1, 'done'),
      P('p3', 'Patch', 'Edit in an isolated worktree', 'single', 1, 'done'),
      P('p4', 'Prove', 'Re-screenshot and pixel-diff', 'single', 1, 'done'),
    ] },
  { id: 'w7', name: 'onboarding-sweep', scope: 'global', trigger: 'manual · /onboard <repo>', runs: 6, avgAgents: 12, avgMinutes: 24, lastRun: '2 d ago', lastResult: 'success',
    description: 'The eighteen steps that turn an unknown repository into a graph, a memory and a rule set the agents can be trusted with.',
    phases: [
      P('p1', 'Detect', 'Stack, framework, architecture', 'single', 1, 'done'),
      P('p2', 'Parse', 'AST · LSP · schema · git history in parallel', 'parallel', 4, 'done'),
      P('p3', 'Infer', 'Conventions, patterns, anti-patterns, risky modules', 'parallel', 4, 'done'),
      P('p4', 'Write', 'Graph, project memory, rule set', 'pipeline', 3, 'done'),
    ] },
  { id: 'w8', name: 'dependency-risk-scan', scope: 'global', trigger: 'daily · 06:00', runs: 38, avgAgents: 4, avgMinutes: 9, lastRun: '8 h ago', lastResult: 'success',
    description: 'Advisories, licence drift, transitive weight and maintenance signal for every dependency — before a human is asked to approve a new one.',
    phases: [
      P('p1', 'Enumerate', 'Direct and transitive across four lockfiles', 'single', 1, 'done'),
      P('p2', 'Assess', 'Advisories · licences · maintenance · size', 'parallel', 4, 'done'),
      P('p3', 'Rank', 'By exploitability against this codebase, not by CVSS alone', 'single', 1, 'done'),
    ] },
  { id: 'w9', name: 'regression-guard', scope: 'project', trigger: 'automatic · pre-merge', runs: 141, avgAgents: 3, avgMinutes: 7, lastRun: '18 min ago', lastResult: 'success',
    description: 'Runs only the tests that cover the lines a change touched, then the full suite if any of them move. Fast when it can be, thorough when it must be.',
    phases: [
      P('p1', 'Select', 'Coverage-mapped tests for the changed lines', 'single', 1, 'done'),
      P('p2', 'Run', 'Targeted, then full if anything moved', 'pipeline', 2, 'done'),
    ] },
  { id: 'w10', name: 'release-notes-compiler', scope: 'global', trigger: 'manual · /ship', runs: 18, avgAgents: 3, avgMinutes: 6, lastRun: 'yesterday', lastResult: 'success',
    description: 'Turns a commit range into notes an operator can read — grouped by what changed for them, not by what changed in the code.',
    phases: [
      P('p1', 'Collect', 'Commits, tasks, ADRs in the range', 'single', 1, 'done'),
      P('p2', 'Group', 'By operator-visible outcome', 'single', 1, 'done'),
      P('p3', 'Write', 'Plain language, no commit hashes', 'single', 1, 'done'),
    ] },
];

export const liveRun = {
  workflow: 'requirement-to-pr',
  ref: 'WF-2211',
  phase: 'Implement',
  concurrencyCap: 8,
  running: 3,
  queued: 2,
  completed: 6,
  tokensSpent: 1_842_000,
  tokensBudget: 4_000_000,
  elapsed: '18m 42s',
  dropped: 'Nothing dropped. If a phase had capped its fan-out, the skipped items would be named here rather than silently omitted.',
  agents: [
    { label: 'compile:requirement', phase: 'Compile', state: 'done', elapsed: '46s', tokens: 84_200 },
    { label: 'analyse:architecture', phase: 'Analyse', state: 'done', elapsed: '2m 12s', tokens: 214_800 },
    { label: 'analyse:impact', phase: 'Analyse', state: 'done', elapsed: '1m 58s', tokens: 188_400 },
    { label: 'implement:backend', phase: 'Implement', state: 'active', elapsed: '9m 04s', tokens: 512_600 },
    { label: 'implement:database', phase: 'Implement', state: 'active', elapsed: '8m 41s', tokens: 402_100 },
    { label: 'implement:frontend', phase: 'Implement', state: 'active', elapsed: '6m 12s', tokens: 288_900 },
    { label: 'verify:tests', phase: 'Verify', state: 'todo', elapsed: '—', tokens: 0 },
    { label: 'review:round-1', phase: 'Review', state: 'todo', elapsed: '—', tokens: 0 },
  ] as { label: string; phase: string; state: 'done' | 'active' | 'todo' | 'failed'; elapsed: string; tokens: number }[],
};

export const wfHistory = [
  { id: 'h1', workflow: 'requirement-to-pr', trigger: 'You · TASK-492', agents: 8, durationS: 1_842, tokens: 2_140_000, cost: 1.82, result: 'success', at: '12 min ago' },
  { id: 'h2', workflow: 'regression-guard', trigger: 'pre-merge hook', agents: 3, durationS: 402, tokens: 218_000, cost: 0, result: 'success', at: '18 min ago' },
  { id: 'h3', workflow: 'architecture-judge-panel', trigger: 'You · surge pricing', agents: 7, durationS: 1_612, tokens: 1_884_000, cost: 2.41, result: 'success', at: '30 min ago' },
  { id: 'h4', workflow: 'screenshot-to-fix', trigger: 'screenshot attach', agents: 4, durationS: 688, tokens: 412_000, cost: 0.61, result: 'success', at: '1 h ago' },
  { id: 'h5', workflow: 'legacy-module-audit', trigger: 'You · Tax module', agents: 11, durationS: 1_340, tokens: 3_120_000, cost: 0, result: 'success', at: '2 h ago' },
  { id: 'h6', workflow: 'dependency-risk-scan', trigger: 'daily 06:00', agents: 4, durationS: 540, tokens: 288_000, cost: 0, result: 'success', at: '8 h ago' },
  { id: 'h7', workflow: 'flaky-test-hunt', trigger: 'nightly 03:00', agents: 5, durationS: 2_880, tokens: 1_040_000, cost: 0, result: 'partial', at: '11 h ago' },
  { id: 'h8', workflow: 'onboarding-sweep', trigger: 'You · HIMS v3', agents: 12, durationS: 1_460, tokens: 4_200_000, cost: 0, result: 'success', at: '2 d ago' },
  { id: 'h9', workflow: 'db-contract-sweep', trigger: 'weekly', agents: 9, durationS: 2_460, tokens: 2_880_000, cost: 0, result: 'success', at: '2 d ago' },
  { id: 'h10', workflow: 'release-notes-compiler', trigger: 'You · v3.14.1', agents: 3, durationS: 362, tokens: 141_000, cost: 0.18, result: 'success', at: 'yesterday' },
  { id: 'h11', workflow: 'requirement-to-pr', trigger: 'You · TASK-488', agents: 8, durationS: 2_240, tokens: 2_610_000, cost: 2.04, result: 'partial', at: 'yesterday' },
  { id: 'h12', workflow: 'legacy-module-audit', trigger: 'You · GL module', agents: 11, durationS: 1_980, tokens: 3_640_000, cost: 0, result: 'failed', at: '3 d ago' },
];

export const patterns = [
  { name: 'Adversarial verify', note: 'Spawn N independent skeptics per finding, each prompted to refute it. Kill it if a majority succeed. Stops plausible-but-wrong findings from reaching you.' },
  { name: 'Perspective-diverse verify', note: 'When a finding can fail in more than one way, give each verifier a different lens — correctness, security, does-it-reproduce. Diversity catches what redundancy cannot.' },
  { name: 'Judge panel', note: 'Generate N designs from deliberately different angles, score with independent judges, synthesise from the winner. Beats one-attempt-iterated when the solution space is wide.' },
  { name: 'Loop until dry', note: 'For unknown-size discovery, keep going until K consecutive rounds find nothing new. A fixed count always misses the tail.' },
  { name: 'Multi-modal sweep', note: 'Search four ways at once — by container, by content, by entity, by time — each blind to the others. One angle never finds everything.' },
  { name: 'Completeness critic', note: 'A final agent asks what is missing: which modality was not run, which claim was not verified, which source was not read. What it finds becomes the next round.' },
];

export const scripts: Record<string, string> = {
  w1: `phase('Map')
const map = await agent('Parse the module, callers and DB surface', { schema: MAP })

phase('Find')
const found = await parallel(DIMENSIONS.map(d => () =>
  agent(\`Audit \${map.module} for \${d}\`, { schema: FINDINGS })))

const fresh = dedupeByFileAndLine(found.filter(Boolean).flatMap(r => r.findings))

phase('Verify')
const judged = await parallel(fresh.map(f => () =>
  parallel(Array.from({ length: 3 }, () => () =>
    agent(\`Try to refute: \${f.claim}. Default to refuted if uncertain.\`, { schema: VERDICT })))
    .then(v => ({ f, real: v.filter(Boolean).filter(x => !x.refuted).length >= 2 }))))

phase('Synthesise')
return agent('Rank the survivors by blast radius and write the report', {
  schema: REPORT, effort: 'high',
})`,
  w2: `phase('Compile')
const req = await agent('Compile the Hinglish requirement', { schema: REQUIREMENT })

phase('Analyse')
const [arch, impact] = await parallel([
  () => agent(\`Architecture for \${req.technical}\`, { schema: ARCH }),
  () => agent(\`Blast radius for \${req.modules.join(', ')}\`, { schema: IMPACT }),
])

if (impact.risk === 'HIGH') await requestHumanApproval(impact)

phase('Implement')
const built = await pipeline(LAYERS,
  l => agent(\`Implement \${l} of \${req.ref}\`, { isolation: 'worktree', label: l }),
  r => agent(\`Self-review \${r.files.join(', ')}\`, { schema: SELF_REVIEW }))

phase('Review')
let round = 0, verdict
do { verdict = await agent('Review the diff', { schema: REVIEW }) } while (++round < 3 && !verdict.approved)

phase('Gate')
return { built, verdict, awaitingHuman: true }`,
};

/* ── Evals ──────────────────────────────────────────────────────── */
export const evalSuites: EvalSuite[] = [
  { id: 'e1', name: 'reviewer-strictness', target: 'Code Reviewer', kind: 'regression', cases: 84, passed: 75, score: 89, delta: -3, lastRun: '4 h ago', status: 'warn' },
  { id: 'e2', name: 'requirement-compiler-accuracy', target: 'AI Commander', kind: 'capability', cases: 120, passed: 112, score: 93, delta: +2, lastRun: '4 h ago', status: 'pass' },
  { id: 'e3', name: 'impact-analysis-recall', target: 'Architect', kind: 'capability', cases: 62, passed: 57, score: 92, delta: +1, lastRun: '4 h ago', status: 'pass' },
  { id: 'e4', name: 'sproc-mapping', target: 'Database Engineer', kind: 'capability', cases: 148, passed: 141, score: 95, delta: 0, lastRun: '4 h ago', status: 'pass' },
  { id: 'e5', name: 'hinglish-parsing', target: 'AI Commander', kind: 'capability', cases: 96, passed: 89, score: 93, delta: +5, lastRun: '4 h ago', status: 'pass' },
  { id: 'e6', name: 'legacy-contract-safety', target: 'All agents', kind: 'safety', cases: 41, passed: 41, score: 100, delta: 0, lastRun: '4 h ago', status: 'pass' },
  { id: 'e7', name: 'test-generation-quality', target: 'QA Engineer', kind: 'capability', cases: 74, passed: 61, score: 82, delta: -6, lastRun: '4 h ago', status: 'fail' },
  { id: 'e8', name: 'memory-retrieval-precision', target: 'Memory Brain', kind: 'capability', cases: 210, passed: 194, score: 92, delta: +3, lastRun: '4 h ago', status: 'pass' },
  { id: 'e9', name: 'cost-efficiency', target: 'Model Router', kind: 'cost', cases: 52, passed: 48, score: 92, delta: +8, lastRun: '4 h ago', status: 'pass' },
  { id: 'e10', name: 'regression-detection', target: 'QA Engineer', kind: 'regression', cases: 68, passed: 66, score: 97, delta: +1, lastRun: '4 h ago', status: 'pass' },
];

export const evalCases: Record<string, EvalCase[]> = {
  e1: [
    { id: 'c1', suite: 'reviewer-strictness', name: 'Catches a Helper-suffixed class', expected: 'blocker · naming convention', got: 'blocker · naming convention', status: 'pass', scoreDelta: 0, judge: 'DeepSeek-V3.2' },
    { id: 'c2', suite: 'reviewer-strictness', name: 'Catches a repository-pattern break', expected: 'blocker · SQL in a service', got: 'minor · style suggestion', status: 'fail', scoreDelta: -4, judge: 'DeepSeek-V3.2' },
    { id: 'c3', suite: 'reviewer-strictness', name: 'Refuses a better-but-inconsistent pattern', expected: 'changes requested', got: 'changes requested', status: 'pass', scoreDelta: 0, judge: 'DeepSeek-V3.2' },
    { id: 'c4', suite: 'reviewer-strictness', name: 'Does not block on formatting alone', expected: 'nit', got: 'major', status: 'partial', scoreDelta: -1, judge: 'DeepSeek-V3.2' },
    { id: 'c5', suite: 'reviewer-strictness', name: 'Flags an unguarded nullable dereference', expected: 'blocker · null safety', got: 'blocker · null safety', status: 'pass', scoreDelta: 0, judge: 'DeepSeek-V3.2' },
    { id: 'c6', suite: 'reviewer-strictness', name: 'Human disagreed with the judge', expected: 'blocker · unbounded collection', got: 'major · resource bound', status: 'partial', scoreDelta: -2, judge: 'Human override — the human called it major, the judge insisted blocker. Recorded, not resolved.' },
  ],
  e7: [
    { id: 'd1', suite: 'test-generation-quality', name: 'Generates a boundary case for a slab table', expected: 'tests the exact slab boundary', got: 'tests the midpoint only', status: 'fail', scoreDelta: -3, judge: 'Kimi-K2.5' },
    { id: 'd2', suite: 'test-generation-quality', name: 'Covers a quoted newline in CSV', expected: 'case present', got: 'case absent', status: 'fail', scoreDelta: -3, judge: 'Kimi-K2.5' },
    { id: 'd3', suite: 'test-generation-quality', name: 'Does not assert on log output', expected: 'no log assertions', got: 'no log assertions', status: 'pass', scoreDelta: 0, judge: 'Kimi-K2.5' },
    { id: 'd4', suite: 'test-generation-quality', name: 'Names tests by behaviour, not method', expected: 'behavioural name', got: 'behavioural name', status: 'pass', scoreDelta: 0, judge: 'Kimi-K2.5' },
  ],
};

export const evalTrend: Record<string, number[]> = {
  e1: [84, 86, 88, 91, 92, 92, 89],
  e2: [88, 89, 90, 91, 91, 91, 93],
  e3: [90, 90, 91, 91, 92, 91, 92],
  e4: [93, 94, 95, 95, 95, 95, 95],
  e5: [78, 81, 84, 86, 88, 88, 93],
  e6: [100, 100, 100, 100, 100, 100, 100],
  e7: [86, 88, 89, 88, 88, 88, 82],
  e8: [85, 87, 88, 89, 90, 89, 92],
  e9: [71, 74, 78, 81, 84, 84, 92],
  e10: [94, 95, 95, 96, 96, 96, 97],
};

export const lessons = [
  { at: '2 h ago', from: 'reviewer-strictness', text: 'The reviewer downgraded a repository-pattern break to a style note. Rule added to solid-enforcer: SQL text inside a service file is always a blocker, never a suggestion.' },
  { at: '11 h ago', from: 'flaky-test-hunt', text: 'A test was called flaky three nights running before anyone noticed it fails only on network-backed volumes. Attribution now records the disk profile.' },
  { at: 'yesterday', from: 'requirement-to-pr', text: 'An agent hand-rolled a CSV parser rather than asking for a dependency. Skill updated: reinventing a solved problem is a reviewer blocker, and asking is cheap.' },
  { at: '2 d ago', from: 'test-generation-quality', text: 'Generated tests hit midpoints and skip boundaries. The test-generator skill now requires an explicit boundary case for any table-driven rule.' },
  { at: '4 d ago', from: 'memory-retrieval-precision', text: 'Retrieval kept surfacing superseded MST_TAX rates. MEM-209 written: every MST_TAX read needs an effective-date predicate.' },
  { at: '1 w ago', from: 'cost-efficiency', text: 'Commit messages were being written by a frontier reasoning model. Routing rule added — summarising goes local, always.' },
];

/* ── Sessions ───────────────────────────────────────────────────── */
const S = (id: string, ref: string, title: string, projectId: string, startedAt: string, duration: string, status: Session['status'], messages: number, tokens: number, cost: number, agents: string[], summary: string, cps: [string, string, number, number][]): Session =>
  ({ id, ref, title, projectId, startedAt, duration, status, messages, tokens, cost, agents, summary,
     checkpoints: cps.map(([label, at, files, tk], i) => ({ id: `${id}-c${i}`, label, at, files, tokens: tk, restorable: true })) });

export const sessions: Session[] = [
  S('s1', 'SES-441', 'Interstate tax reversal — TASK-492', 'erp', 'today 13:52', '1h 08m', 'active', 84, 2_140_000, 1.82,
    ['AI Commander', 'Architect', 'Backend Engineer', 'Database Engineer', 'QA Engineer', 'Code Reviewer'],
    'Compiled a Hinglish bug report into PLAN-492, traced the double derivation in SP_CalculateTax, shipped a resolver behind an interface, and reached the human merge gate after two review rounds.',
    [['Requirement compiled', '13:52', 0, 84_000], ['Impact analysis complete', '13:58', 0, 302_000], ['Backend patch applied', '14:12', 4, 812_000], ['Tests green', '14:19', 7, 1_404_000], ['Review round 2 passed', '14:20', 7, 2_140_000]]),
  S('s2', 'SES-440', 'Customer bulk upload — TASK-488', 'erp', 'yesterday 09:14', '4h 22m', 'ended', 162, 2_610_000, 2.04,
    ['AI Commander', 'Architect', 'Backend Engineer', 'Frontend Engineer', 'Database Engineer', 'Code Reviewer'],
    'Staging-table pattern established as ADR-49 and shipped end to end, except the upload UI, which is blocked on a dependency approval.',
    [['ADR-49 accepted', 'y 10:31', 0, 402_000], ['Migration verified', 'y 11:02', 2, 780_000], ['Endpoint shipped', 'y 13:40', 6, 1_610_000], ['Review requested changes', 'y 15:22', 11, 2_610_000]]),
  S('s3', 'SES-439', 'Session drops after 20 minutes — TASK-501', 'erp', 'today 08:02', '52m', 'ended', 61, 940_000, 0.71,
    ['DevOps Engineer', 'Backend Engineer', 'Security Engineer'],
    'Correlated 214 forced logouts to app-pool recycles, proved a Redis-backed provider on staging, and stopped at the production approval gate.',
    [['Correlation found', '08:06', 0, 188_000], ['Staging validated', '08:44', 3, 640_000], ['Blocked at APPR-118', '08:45', 3, 940_000]]),
  S('s4', 'SES-438', 'Memory decay policy — TASK-509', 'aios', 'today 07:30', '1h 14m', 'ended', 48, 610_000, 0,
    ['Architect', 'Backend Engineer'],
    'Per-category half-life model designed and implemented, with pinned facts and HIGH-confidence decisions exempt by construction.',
    [['Half-life model', '07:41', 0, 140_000], ['Reinforcement shipped', '08:12', 4, 420_000], ['Exemptions in progress', '08:44', 6, 610_000]]),
  S('s5', 'SES-437', 'Surge pricing design', 'taxi', 'today 11:40', '38m', 'forked', 34, 1_884_000, 2.41,
    ['Researcher', 'Architect'],
    'Three designs scored by an independent judge panel. Forked at the judging step to try a fourth angle without losing the original run.',
    [['Research complete', '11:52', 0, 660_000], ['Three designs generated', '12:04', 0, 1_240_000], ['Judged — forked here', '12:18', 0, 1_884_000]]),
  S('s6', 'SES-436', 'OPD rounding mismatch — TASK-503', 'hims', 'today 10:05', '1h 02m', 'ended', 52, 720_000, 0,
    ['Architect', 'Backend Engineer', 'Database Engineer'],
    'Found two independent rounding sites disagreeing on exact .005 and began consolidating on a single Money type.',
    [['Both sites located', '10:09', 0, 210_000], ['Money type drafted', '10:38', 3, 520_000], ['34 call sites mapped', '11:07', 3, 720_000]]),
  S('s7', 'SES-435', 'HIMS v3 onboarding', 'hims', '2 d ago 09:02', '2h 11m', 'ended', 96, 4_200_000, 0,
    ['Architect', 'Database Engineer', 'Security Engineer', 'Documentation Agent'],
    'Full eighteen-step onboarding. 31 modules, 214 tables, 640 procedures parsed; understanding reached 74%.',
    [['Stack detected', 'd2 09:04', 0, 120_000], ['AST parsed', 'd2 09:38', 0, 1_400_000], ['Schema mapped', 'd2 10:14', 0, 2_900_000], ['Rules generated', 'd2 11:13', 0, 4_200_000]]),
  S('s8', 'SES-434', 'Reports grid migration — TASK-479', 'erp', '2 d ago 14:20', '3h 04m', 'ended', 118, 1_640_000, 0.42,
    ['Frontend Engineer', 'QA Engineer', 'Code Reviewer'],
    'Legacy grid replaced with column and export parity. One regression found and fixed inside the same session.',
    [['Column parity proved', 'd2 15:40', 5, 640_000], ['Export parity proved', 'd2 16:22', 7, 1_100_000], ['Regression fixed', 'd2 17:04', 9, 1_640_000]]),
  S('s9', 'SES-433', 'Legacy module audit — Tax', 'erp', '2 h ago', '22m', 'ended', 28, 3_120_000, 0,
    ['Architect', 'Security Engineer', 'Database Engineer', 'QA Engineer'],
    'Eleven agents across six audit dimensions. Nine findings survived adversarial verification out of twenty-four raised.',
    [['Six dimensions dispatched', '2h ago', 0, 900_000], ['24 findings deduped to 17', '2h ago', 0, 1_800_000], ['9 survived three skeptics', '2h ago', 0, 3_120_000]]),
  S('s10', 'SES-432', 'ACP bridge design — TASK-521', 'aios', 'today 12:55', '26m', 'ended', 22, 380_000, 0.18,
    ['Architect'], 'Mapped the ACP session lifecycle onto internal runs and established the permission bridge as the security boundary.',
    [['Lifecycle mapped', '13:04', 0, 180_000], ['Bridge scoped', '13:21', 0, 380_000]]),
  S('s11', 'SES-431', 'Standup', 'erp', 'today 07:05', '4m', 'ended', 6, 42_000, 0.02,
    ['AI Commander'], 'What moved since yesterday across all five projects, and what is blocked on a human.',
    [['Summary written', '07:09', 0, 42_000]]),
  S('s12', 'SES-430', 'Excel export triage — BUG-991', 'erp', 'today 12:20', '11m', 'ended', 14, 188_000, 0,
    ['QA Engineer', 'Architect'], 'Reproduced at 104k rows, attributed to full in-memory buffering, filed as TASK-513 rather than patched on the spot.',
    [['Reproduced', '12:24', 0, 62_000], ['Root cause attributed', '12:31', 0, 188_000]]),
];

export const transcriptDemo = [
  { role: 'you', at: '13:52', text: 'Invoice mein tax galat aa raha hai — CGST/SGST interstate orders pe reverse ho raha hai. Fix karo, aur purane invoices bhi theek karne padenge shayad.' },
  { role: 'commander', at: '13:52', text: 'Compiling. Reading this as: interstate supplies are being charged CGST+SGST against the billing state instead of IGST against the place of supply. Checking how many invoices are affected before planning anything.' },
  { role: 'commander', at: '13:53', text: 'Memory returned 42 facts, 8 above threshold. MEM-142 is relevant and blocking: TRANS_INVOICE is append-only, so "purane invoices theek karna" cannot be an UPDATE. I will plan a credit-note path and flag it as an open question.' },
  { role: 'you', at: '13:55', text: 'haan credit note hi karna padega, GL already reconcile ho chuka hai August ka' },
  { role: 'commander', at: '13:55', text: 'Recorded as a business rule. That closes one of the three open questions on PLAN-492 — the other two still need you: the SEZ flag source, and whether to reopen the August period for two already-reconciled invoices.' },
];

export const compaction = {
  at: '14:06',
  before: 128_400,
  after: 18_200,
  preserved: ['MEM-142 · TRANS_INVOICE is append-only', 'MEM-311 · interstate uses place of supply', 'The credit-note decision you made at 13:55', 'PLAN-492 open questions'],
  note: 'Compaction summarises the conversation, never the memory. Durable facts were written to the store by the PreCompact hook before the window was reduced, so nothing learned in this session depends on the transcript surviving.',
};
