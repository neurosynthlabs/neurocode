import type { BrainstormSession, ResearchReport } from '@/types';

/* ── Brainstorm ─────────────────────────────────────────────────── */
export type Objection = { text: string; severity: 'HIGH' | 'MEDIUM' | 'LOW' };
export type Brainstorm = Omit<BrainstormSession, 'devilsAdvocate'> & { devilsAdvocate: Objection[]; projectHint: string };

const STAGES = ['Problem', 'Users', 'Features', 'Architecture', 'Technology', 'Risks', 'MVP', 'Roadmap'] as const;
const nodes = (id: string, items: string[][]) =>
  STAGES.map((stage, i) => ({ id: `${id}-${i}`, stage, title: stage, items: items[i] ?? [] }));

export const brainstorms: Brainstorm[] = [
  {
    id: 'b1', ref: 'BRN-31', idea: 'I want to build a taxi marketplace for tier-2 Indian cities — cheaper than the incumbents, fair to drivers.',
    createdAt: 'today 11:12', status: 'complete', score: 64, projectHint: 'taxi',
    nodes: nodes('b1', [
      ['Tier-2 riders wait 12–18 min at peak; incumbents under-serve because unit economics there are thin', 'Drivers lose 25–30% to commission and churn to the next platform bonus', 'Auto-rickshaws dominate short trips but have no reliable booking layer'],
      ['Office commuters in Indore, Nagpur, Coimbatore', 'Students and hospital visitors — price-sensitive, cash-heavy', 'Drivers who own their vehicle and already run on two apps', 'Small hotels and clinics that book on behalf of guests'],
      ['Flat daily driver subscription instead of per-ride commission', 'Auto + cab in one flow, with shared rides for >4 km', 'Cash, UPI and pay-later for corporate accounts', 'Hindi and regional-language booking by voice', 'Fare shown and locked before confirm', 'SOS with live location to two contacts'],
      ['H3-cell matching with a widening radius capped at 3 rings', 'Fare snapshot stored on every booking so disputes are replayable', 'Event-sourced trip state machine — requested → matched → arrived → started → completed', 'Offline-tolerant driver app: queue events, sync on reconnect'],
      ['Next.js rider web, React Native driver app', 'FastAPI + PostgreSQL/PostGIS, Redis for live positions', 'Razorpay for UPI and subscriptions', 'Self-hosted OSRM for routing to avoid per-call map costs'],
      ['Driver supply cold-start in each new city', 'Incumbent price war the week you launch', 'State-level aggregator licensing varies city to city', 'Cash handling and fraud on pay-later'],
      ['One city, autos only, subscription pricing, UPI + cash', 'Rider app, driver app, ops console — nothing else', 'Target: 400 active drivers, 3,000 trips/day by week 10'],
      ['Weeks 1–6: build MVP, sign 150 drivers before launch', 'Weeks 7–12: launch Indore, tune matching and pricing', 'Months 4–6: add cabs, second city, corporate accounts'],
    ]),
    devilsAdvocate: [
      { severity: 'HIGH', text: 'Unit economics: a flat subscription only beats commission for the driver if they do ~14 trips a day. Below that you are charging your best-retained drivers more, not less.' },
      { severity: 'HIGH', text: 'Driver cold-start: riders open the app, see 18-minute waits, and never open it again. You need supply before demand in every city — that is capital, not code.' },
      { severity: 'HIGH', text: 'Licensing: Madhya Pradesh and Maharashtra have different aggregator rules and fee structures. "Launch fast in tier-2" is really "file in each state and wait".' },
      { severity: 'MEDIUM', text: 'Incumbent price war: they can subsidise a single city for a quarter out of pocket change. Your differentiation has to survive a period of free rides.' },
      { severity: 'MEDIUM', text: 'Fraud: pay-later and cash together invite fake trips and collusion between riders and drivers. The ops cost of policing it is not in the plan.' },
      { severity: 'LOW', text: 'Retention: autos are a short-trip, low-loyalty habit. Riders will flag the nearest auto on the street the moment your app is slower than walking out.' },
    ],
    mvp: ['Autos only in one city', 'Subscription for drivers, no commission', 'UPI + cash, no pay-later', 'Fare locked at confirm', 'Ops console with manual dispute resolution', 'SOS'],
    roadmap: [
      { phase: 'Pre-launch', weeks: '1–6', items: ['Build rider + driver + ops', 'Sign 150 drivers', 'File aggregator licence'] },
      { phase: 'Launch', weeks: '7–12', items: ['Indore only', 'Tune radius and surge', 'Weekly driver earnings review'] },
      { phase: 'Expand', weeks: '13–26', items: ['Add cabs', 'Second city', 'Corporate accounts with pay-later'] },
    ],
    verdict: 'Worth a narrow pilot, not a platform bet. The idea wins only if the subscription model genuinely retains drivers at tier-2 trip volumes — test that with 50 drivers on a spreadsheet for a month before writing the matching engine.',
  },
  {
    id: 'b2', ref: 'BRN-30', idea: 'ERP renewal strategy — big-bang rewrite or module-by-module strangler?', createdAt: 'yesterday 16:02', status: 'complete', score: 88, projectHint: 'erp',
    nodes: nodes('b2', [
      ['A 14-year-old ERP with 2.4M lines, 382 tables and 1,148 procedures', 'Every module renewed so far took twice the estimate because business rules live in T-SQL'],
      ['Finance, stores and dispatch staff who cannot tolerate a bad month-end', 'One operator directing agents, not a team'],
      ['Strangler: route one module at a time behind the same UI', 'Parity harness: old and new run side by side, outputs diffed', 'Rules extracted from procedures into memory before any rewrite'],
      ['New services behind an interface, old procedures called until parity is proven', 'Shared database for now — split only after the module is fully renewed'],
      ['ASP.NET Core 8, React 18 → 19, SQL Server stays', 'Parity diffs stored as test fixtures'],
      ['Parity drift in modules nobody fully understands (GL, bank reconciliation)', 'Morale: renewal never "finishes" if it is module by module'],
      ['Billing and Tax first — highest bug density, best documented'],
      ['Q4: Billing + Tax', 'Q1: Customer, Sales Order, Reports', 'Q2: Inventory, Purchase', 'Last: GL — only after everything else is proven'],
    ]),
    devilsAdvocate: [
      { severity: 'MEDIUM', text: 'Strangler projects die of the long tail. The last 20% of modules are the ones nobody understands, and they are the ones you will never quite finish.' },
      { severity: 'MEDIUM', text: 'A shared database means the new code inherits every trigger and audit quirk of the old schema. You are renewing the code, not the data model.' },
      { severity: 'LOW', text: 'Running old and new side by side doubles the load on the one SQL Server during month-end.' },
    ],
    mvp: ['Billing on the new service with a parity harness', 'Tax resolver as a single interface', 'Rules extracted into memory before rewrite'],
    roadmap: [
      { phase: 'Q4', weeks: '1–12', items: ['Billing', 'Tax'] },
      { phase: 'Q1', weeks: '13–24', items: ['Customer', 'Sales Order', 'Reports'] },
      { phase: 'Q2+', weeks: '25+', items: ['Inventory', 'Purchase', 'GL last'] },
    ],
    verdict: 'Strangler, clearly. A big-bang rewrite of something whose rules live in 1,148 procedures is a bet you cannot verify. Keep parity diffs as the definition of done, and leave GL until the end.',
  },
  {
    id: 'b3', ref: 'BRN-28', idea: 'Memory should decay like a human’s — strong when used, fading when not.', createdAt: '2 d ago', status: 'complete', score: 91, projectHint: 'aios',
    nodes: nodes('b3', [
      ['Retrieval returns stale facts with the same weight as live ones', 'Memory grows without bound and gets noisier every week'],
      ['The operator, and every agent that retrieves context'],
      ['Per-category half-life', 'Reinforcement on actual use', 'Pinned facts exempt', 'Archive, never delete'],
      ['Strength score on every fact, updated on hit and by a nightly decay pass'],
      ['Postgres for strength and hits, Qdrant for vectors'],
      ['Decay hides a rarely-used but critical fact', 'Ranking becomes time-dependent and hard to reproduce'],
      ['Decay + reinforcement + pin exemption only'],
      ['Week 1: model', 'Week 2: backtest against 30 days of retrieval', 'Week 3: ship'],
    ]),
    devilsAdvocate: [
      { severity: 'HIGH', text: 'The most dangerous facts are the rarely-needed ones — a statutory rule you hit once a year. Decay will quietly bury exactly those.' },
      { severity: 'LOW', text: 'Strength-weighted ranking makes it harder to explain why a result appeared.' },
    ],
    mvp: ['Half-life per category', 'Reinforce on hit', 'Pinned and HIGH-confidence decisions never decay'],
    roadmap: [{ phase: 'Build', weeks: '1–3', items: ['Model', 'Backtest', 'Ship'] }],
    verdict: 'Ship it — with the exemption list doing the real work. The backtest against 30 days of real retrieval is the gate.',
  },
  {
    id: 'b4', ref: 'BRN-26', idea: 'Could NeuroCode itself be a product? Pricing for other one-person engineering shops.', createdAt: '4 d ago', status: 'complete', score: 57, projectHint: 'aios',
    nodes: nodes('b4', [
      ['Solo developers maintaining legacy client systems have no leverage beyond their own hours'],
      ['Freelance consultants', 'Small agencies with one senior and a few juniors'],
      ['Self-hosted install', 'BYO model keys', 'Legacy onboarding as the headline feature'],
      ['Single-tenant, runs on the customer machine'],
      ['Same stack, packaged as a desktop app'],
      ['Support cost of self-hosted installs', 'Buyers expect it to work on 8 GB laptops'],
      ['Onboarding + memory + review only'],
      ['Private beta with 10 consultants'],
    ]),
    devilsAdvocate: [
      { severity: 'HIGH', text: 'Self-hosted means every customer machine is a support ticket. You would be running an IT help desk, not a product.' },
      { severity: 'MEDIUM', text: 'The value is tuned to one operator’s habits — Hinglish requirements, ERP conventions. It may not travel.' },
    ],
    mvp: ['Onboarding', 'Memory', 'Reviewer'],
    roadmap: [{ phase: 'Beta', weeks: '1–8', items: ['10 consultants', 'Weekly interviews'] }],
    verdict: 'Not yet. Use it on your own work for another quarter; the product question gets easier once you know which three features you could not live without.',
  },
  {
    id: 'b5', ref: 'BRN-24', idea: 'Hospital OPD queue system that tells patients their real wait time.', createdAt: '6 d ago', status: 'complete', score: 76, projectHint: 'hims',
    nodes: nodes('b5', [
      ['OPD waits of 2–4 hours with no visibility; patients crowd the desk asking'],
      ['Patients and attendants', 'Front-desk staff', 'Doctors who want fewer interruptions'],
      ['Token on registration', 'SMS/WhatsApp live position', 'Estimated wait from each doctor’s actual consult time'],
      ['Queue per doctor, estimate from rolling median consult duration'],
      ['Built into HIMS v3, WhatsApp Business API'],
      ['Estimates wrong on emergency interruptions', 'WhatsApp template approval delays'],
      ['Token + SMS position only'],
      ['Pilot one department for a month'],
    ]),
    devilsAdvocate: [
      { severity: 'MEDIUM', text: 'A wrong estimate is worse than none — a patient who leaves because they were told 90 minutes and gets called at 40 is a complaint.' },
      { severity: 'LOW', text: 'Doctors may resist visible consult-time metrics.' },
    ],
    mvp: ['Token', 'SMS position', 'Range estimate, never a single number'],
    roadmap: [{ phase: 'Pilot', weeks: '1–4', items: ['Orthopaedics OPD', 'Measure complaint rate'] }],
    verdict: 'Good, cheap, and inside an existing product. Show a range, not a number, and pilot one department.',
  },
  {
    id: 'b6', ref: 'BRN-21', idea: 'FIFA analytics product for fantasy-league players.', createdAt: '9 d ago', status: 'complete', score: 42, projectHint: 'fifa',
    nodes: nodes('b6', [
      ['Fantasy players drown in stats but have no decision support'],
      ['Serious fantasy players', 'Content creators'],
      ['Captain picks', 'Fixture difficulty', 'Transfer planner'],
      ['ClickHouse for event data, nightly models'],
      ['React, Node, ClickHouse'],
      ['Data licensing costs', 'Crowded market'],
      ['Captain picks only'],
      ['One season, free tier'],
    ]),
    devilsAdvocate: [
      { severity: 'HIGH', text: 'Event data licensing alone costs more than the likely revenue for the first two seasons.' },
      { severity: 'HIGH', text: 'A dozen free tools already do captain picks. There is no reason for someone to switch.' },
    ],
    mvp: ['Captain picks from public data'],
    roadmap: [{ phase: 'Season 1', weeks: '1–38', items: ['Free tier', 'Measure retention'] }],
    verdict: 'Keep it paused. It is a fun side project with no edge — the paused project status is the right call.',
  },
];

/* ── Research ───────────────────────────────────────────────────── */
export type Citation = { label: string; url: string; via: 'web' | 'github' | 'docs' | 'internal' | 'memory' };
export type Report = Omit<ResearchReport, 'citations'> & {
  citations: Citation[];
  architecture: string;
  sweep: { angle: string; hits: number }[];
  gaps: string[];
  projectHint: string;
};

const sweep = (a: number, b: number, c: number, d: number) => [
  { angle: 'by keyword', hits: a }, { angle: 'by repository', hits: b },
  { angle: 'by documentation', hits: c }, { angle: 'by internal memory', hits: d },
];

export const reports: Report[] = [
  {
    id: 'r1', ref: 'RES-77', question: 'Which surge-pricing approach keeps riders’ trust while protecting driver supply?', status: 'complete',
    createdAt: 'today 11:41', durationS: 660, agents: 4, confidence: 84, projectHint: 'taxi',
    sources: [{ kind: 'web', count: 6 }, { kind: 'github', count: 3 }, { kind: 'docs', count: 3 }, { kind: 'memory', count: 2 }],
    summary: 'A smoothed demand/supply ratio mapped through a capped curve beats step-function surge on rider trust, provided the multiplier is shown and locked before confirm. The cap matters more to perception than the average level.',
    findings: [
      'Step functions produce visible jumps that riders read as manipulation; smoothing over 5 minutes removes most complaints.',
      'A hard ceiling (2–3×) is what regulators and press focus on — the average multiplier barely registers.',
      'Showing the multiplier before confirm reduces cancellation more than lowering it by 0.2×.',
      'Per-zone computation needs a minimum sample, or a single cancelled trip can spike a quiet zone.',
    ],
    alternatives: [
      { name: 'Step-function surge', pros: ['Simple to explain', 'Cheap to compute'], cons: ['Visible jumps', 'Gameable at thresholds'], verdict: 'Rejected' },
      { name: 'Smoothed ratio, capped curve', pros: ['No sudden jumps', 'Cap is legible', 'Tunable per city'], cons: ['Needs a sample floor per zone'], verdict: 'Recommended' },
      { name: 'Auction / driver bidding', pros: ['Market-efficient'], cons: ['Rider sees unpredictable prices', 'Hard to regulate'], verdict: 'Rejected' },
    ],
    architecture: 'Per-H3-cell counters in Redis on a 60s rolling window → 5-minute exponential smoothing → capped curve → multiplier snapshotted onto the booking together with its inputs.',
    risks: ['Quiet zones spiking on tiny samples', 'Legal position on surging the cancellation fee is still unanswered', 'Drivers gaming zone boundaries'],
    recommendation: 'Implement the smoothed capped curve with a 2.5× ceiling and a minimum of 8 requests per window before a zone may surge. Snapshot inputs on every booking.',
    citations: [
      { label: 'Surge pricing and rider cancellation — field study', url: 'arxiv.org/abs/2403.11871', via: 'web' },
      { label: 'H3 hierarchical geospatial index', url: 'github.com/uber/h3', via: 'github' },
      { label: 'Motor Vehicle Aggregator Guidelines 2020', url: 'morth.nic.in', via: 'docs' },
      { label: 'ADR-09 · H3 cells for driver matching', url: 'internal://taxi/adr/09', via: 'internal' },
      { label: 'MEM-512 · riders cancel on unannounced price change', url: 'internal://memory/512', via: 'memory' },
    ],
    sweep: sweep(18, 7, 5, 4),
    gaps: ['No data on tier-2 Indian rider price sensitivity — every study found is metro or US.', 'Did not model driver behaviour under a cap — only rider behaviour.'],
  },
  {
    id: 'r2', ref: 'RES-74', question: 'How should ERP session state survive IIS app-pool recycles across three nodes?', status: 'complete',
    createdAt: 'today 08:10', durationS: 420, agents: 3, confidence: 91, projectHint: 'erp',
    sources: [{ kind: 'docs', count: 5 }, { kind: 'web', count: 3 }, { kind: 'memory', count: 3 }],
    summary: 'In-proc session state cannot survive a recycle by definition. A Redis-backed provider fixes it with the smallest code change; SQL Server session state works too but adds load to the one database that already struggles at month-end.',
    findings: ['Every forced logout correlated to a recycle within 4 seconds.', 'Sticky sessions at the load balancer only move the problem — a recycle still drops the node’s sessions.', 'The Redis provider needs a decided behaviour for when Redis itself is down.'],
    alternatives: [
      { name: 'Redis session provider', pros: ['Survives recycles', 'Small change'], cons: ['New production dependency'], verdict: 'Recommended' },
      { name: 'SQL Server session state', pros: ['No new infra'], cons: ['Load on the primary', 'Slower'], verdict: 'Fallback' },
      { name: 'Sticky sessions only', pros: ['Zero code'], cons: ['Does not fix recycles'], verdict: 'Rejected' },
    ],
    architecture: 'Custom SessionStateStoreProvider pointing at a Redis primary with a replica, rolling restart across web01–03, revert by web.config.',
    risks: ['Redis outage becomes an auth outage', 'Session fixation if IDs are not regenerated on login'],
    recommendation: 'Ship the Redis provider behind APPR-118, and decide fail-closed vs fall-back-to-in-proc before it goes live.',
    citations: [
      { label: 'ASP.NET session state modes', url: 'learn.microsoft.com/aspnet/session-state', via: 'docs' },
      { label: 'IIS application pool recycling', url: 'learn.microsoft.com/iis/recycling', via: 'docs' },
      { label: 'BUG-905 · users logged out after ~20 min', url: 'internal://erp/bug/905', via: 'internal' },
    ],
    sweep: sweep(9, 2, 8, 3),
    gaps: ['No load test of Redis under month-end concurrency.'],
  },
  {
    id: 'r3', ref: 'RES-71', question: 'Streaming CSV parsing for 50,000-row customer imports in the browser', status: 'complete',
    createdAt: 'yesterday 10:04', durationS: 380, agents: 3, confidence: 88, projectHint: 'erp',
    sources: [{ kind: 'github', count: 4 }, { kind: 'docs', count: 2 }, { kind: 'web', count: 2 }],
    summary: 'papaparse in worker + streaming mode handles 50k rows with flat memory and correct quoted-newline handling. A hand-rolled parser is the most common source of import bugs in the repositories surveyed.',
    findings: ['Quoted fields containing newlines appear in 3 of 5 sample files.', 'Worker mode keeps the UI responsive at 50k rows.', 'Formula injection must be neutralised on display, not just on write.'],
    alternatives: [
      { name: 'papaparse (worker, streaming)', pros: ['Battle-tested', '0 deps', 'Correct quoting'], cons: ['New dependency — needs approval'], verdict: 'Recommended' },
      { name: 'Hand-rolled split on commas', pros: ['No dependency'], cons: ['Breaks on quoted newlines'], verdict: 'Rejected' },
      { name: 'Server-side parse only', pros: ['One code path'], cons: ['No preview before upload'], verdict: 'Fallback' },
    ],
    architecture: 'Parse in a Web Worker, validate per chunk, upload in 2 MB chunks with an idempotency key, commit through the staging table.',
    risks: ['Approval delay on the dependency', 'Very wide files (200+ columns) not tested'],
    recommendation: 'Request papaparse@5.4.1 (APPR-120). Do not ship the hand-rolled parser.',
    citations: [
      { label: 'papaparse', url: 'github.com/mholt/PapaParse', via: 'github' },
      { label: 'RFC 4180 — CSV format', url: 'rfc-editor.org/rfc/rfc4180', via: 'docs' },
      { label: 'OWASP CSV injection', url: 'owasp.org/www-community/attacks/CSV_Injection', via: 'web' },
    ],
    sweep: sweep(11, 9, 4, 1),
    gaps: ['Excel-exported CSVs with BOM and ; delimiters not sampled.'],
  },
  {
    id: 'r4', ref: 'RES-69', question: 'Which open-weight coding models are worth running locally, September 2026?', status: 'complete',
    createdAt: '2 d ago', durationS: 910, agents: 4, confidence: 76, projectHint: 'aios',
    sources: [{ kind: 'web', count: 9 }, { kind: 'github', count: 5 }, { kind: 'docs', count: 4 }],
    summary: 'Qwen3-Coder-Next is the strongest open-weight choice for repository-scale agentic coding, but needs serious VRAM; GLM-4.7 is the best fallback. On an 8–16 GB laptop, heavy models must run remotely.',
    findings: ['Benchmark gaps between the top open models are within noise on real repository tasks.', 'Context length matters less than tool-use reliability for agent loops.', 'Quantised 30B-class models are the practical local ceiling on consumer GPUs.'],
    alternatives: [
      { name: 'Qwen3-Coder-Next', pros: ['Strongest agentic coding', 'Long context'], cons: ['Heavy'], verdict: 'Primary (remote/GPU box)' },
      { name: 'GLM-4.7', pros: ['Strong, cheaper to serve'], cons: ['Weaker at long edits'], verdict: 'Fallback' },
      { name: 'Qwen3-Coder-30B (quantised)', pros: ['Runs locally on a 24 GB GPU'], cons: ['Quality drop on hard tasks'], verdict: 'Local default' },
    ],
    architecture: 'Router sends coding to the local 30B by default, escalates to Qwen3-Coder-Next on failure or on large diffs.',
    risks: ['Benchmarks age within weeks', 'Vendor licence terms change between releases'],
    recommendation: 'Keep the router model-agnostic. Re-run the internal eval suite monthly rather than trusting published benchmarks.',
    citations: [
      { label: 'Qwen3-Coder model card', url: 'qwen.ai', via: 'docs' },
      { label: 'SWE-bench leaderboard', url: 'swebench.com', via: 'web' },
      { label: 'Internal eval · cost-efficiency suite', url: 'internal://evals/e9', via: 'internal' },
    ],
    sweep: sweep(22, 8, 6, 2),
    gaps: ['Published numbers could not be reproduced on the internal ERP tasks — only the internal suite is trusted.', 'Nothing measured on Apple-silicon inference speed.'],
  },
  {
    id: 'r5', ref: 'RES-66', question: 'Qdrant vs pgvector for a single-machine memory store', status: 'complete',
    createdAt: '4 d ago', durationS: 540, agents: 3, confidence: 82, projectHint: 'aios',
    sources: [{ kind: 'docs', count: 5 }, { kind: 'github', count: 3 }, { kind: 'web', count: 3 }],
    summary: 'At under a few million vectors both are fast enough. pgvector wins on operational simplicity (one database); Qdrant wins on filtering and payload-aware search, which the memory store leans on heavily.',
    findings: ['Filtered search by project + category is the dominant query shape.', 'pgvector HNSW with filters degrades when the filter is selective.', 'Running Postgres anyway makes pgvector nearly free to operate.'],
    alternatives: [
      { name: 'Qdrant', pros: ['Fast filtered search', 'Payload indexes'], cons: ['A second datastore'], verdict: 'Recommended' },
      { name: 'pgvector', pros: ['One database', 'Transactions with metadata'], cons: ['Selective filters are slow'], verdict: 'Fallback' },
    ],
    architecture: 'Postgres holds facts, strength and hits; Qdrant holds vectors with project/category payloads; ids are shared.',
    risks: ['Two stores can drift out of sync'],
    recommendation: 'Qdrant for vectors, Postgres for everything else, with a nightly consistency check.',
    citations: [
      { label: 'Qdrant filtering', url: 'qdrant.tech/documentation/concepts/filtering', via: 'docs' },
      { label: 'pgvector', url: 'github.com/pgvector/pgvector', via: 'github' },
    ],
    sweep: sweep(12, 6, 7, 2),
    gaps: ['No benchmark run on the real memory corpus.'],
  },
  {
    id: 'r6', ref: 'RES-63', question: 'ACP vs MCP — do we need both?', status: 'complete',
    createdAt: '1 d ago', durationS: 300, agents: 2, confidence: 93, projectHint: 'aios',
    sources: [{ kind: 'docs', count: 4 }, { kind: 'github', count: 2 }],
    summary: 'Yes — they point in opposite directions. MCP connects the agent to tools; ACP connects an editor to the agent. Neither replaces the other.',
    findings: ['ACP carries session lifecycle and permission requests back to the editor.', 'MCP carries tool schemas and results to the agent.', 'The permission bridge is the security boundary when both are present.'],
    alternatives: [
      { name: 'Both', pros: ['Editor control + tool reach'], cons: ['Two protocols to secure'], verdict: 'Recommended' },
      { name: 'MCP only', pros: ['Simpler'], cons: ['No editor-native control'], verdict: 'Rejected' },
    ],
    architecture: 'Editor ←ACP→ NeuroCode ←MCP→ tools; ACP permission requests resolve through the existing approval gates.',
    risks: ['An ACP client bypassing gates if the bridge is miswired'],
    recommendation: 'Implement ACP as TASK-521 with the permission bridge reviewed by the Security Engineer.',
    citations: [
      { label: 'Agent Client Protocol spec', url: 'agentclientprotocol.com', via: 'docs' },
      { label: 'Model Context Protocol spec', url: 'modelcontextprotocol.io', via: 'docs' },
    ],
    sweep: sweep(6, 3, 6, 1),
    gaps: [],
  },
  {
    id: 'r7', ref: 'RES-60', question: 'Playwright visual regression — thresholds that catch real bugs without flaking', status: 'complete',
    createdAt: '1 w ago', durationS: 480, agents: 3, confidence: 79, projectHint: 'erp',
    sources: [{ kind: 'docs', count: 3 }, { kind: 'github', count: 4 }, { kind: 'web', count: 2 }],
    summary: 'A 0.2% pixel threshold with masked dynamic regions (dates, ids) catches layout regressions while keeping flake rate under 1%.',
    findings: ['Font rendering differs between CI and local — pin the container.', 'Masking timestamps removes most false positives.'],
    alternatives: [
      { name: 'Pixel diff, 0.2%, masked', pros: ['Catches layout shifts'], cons: ['Needs masks'], verdict: 'Recommended' },
      { name: 'DOM snapshot', pros: ['Stable'], cons: ['Misses CSS regressions'], verdict: 'Supplement' },
    ],
    architecture: 'Baselines stored per branch, approved by a human on intended changes.',
    risks: ['Baseline approvals becoming a rubber stamp'],
    recommendation: 'Adopt 0.2% masked diffs, pin the rendering container.',
    citations: [{ label: 'Playwright visual comparisons', url: 'playwright.dev/docs/test-snapshots', via: 'docs' }],
    sweep: sweep(8, 5, 4, 1),
    gaps: ['Not tested on the print-preview surface.'],
  },
  {
    id: 'r8', ref: 'RES-58', question: 'Models of memory decay — what does the literature suggest?', status: 'complete',
    createdAt: '5 d ago', durationS: 600, agents: 3, confidence: 71, projectHint: 'aios',
    sources: [{ kind: 'web', count: 7 }, { kind: 'docs', count: 2 }],
    summary: 'Exponential forgetting with retrieval-based reinforcement (spaced-repetition style) is simple and well supported; per-category half-lives are a reasonable engineering approximation.',
    findings: ['Retrieval practice strengthens memory more than re-exposure.', 'A floor prevents important but rare facts from vanishing.'],
    alternatives: [
      { name: 'Exponential + reinforcement', pros: ['Simple', 'Explainable'], cons: ['Needs tuned half-lives'], verdict: 'Recommended' },
      { name: 'Learned relevance model', pros: ['Adaptive'], cons: ['Opaque', 'Needs training data'], verdict: 'Later' },
    ],
    architecture: 'Strength decays nightly; hits add a category-specific bump; pinned facts skip decay.',
    risks: ['Half-lives chosen by intuition'],
    recommendation: 'Ship the simple model, backtest against real retrieval logs.',
    citations: [{ label: 'Ebbinghaus forgetting curve replication', url: 'journals.plos.org/plosone', via: 'web' }],
    sweep: sweep(14, 1, 3, 2),
    gaps: ['No study found on decay for machine-agent memory specifically.'],
  },
  {
    id: 'r9', ref: 'RES-80', question: 'IIS app-pool recycling defaults — which to change on the ERP nodes?', status: 'running',
    createdAt: '8 min ago', durationS: 0, agents: 2, confidence: 0, projectHint: 'erp',
    sources: [{ kind: 'docs', count: 2 }],
    summary: 'Research in progress.', findings: [], alternatives: [], architecture: '—', risks: [],
    recommendation: 'Pending.', citations: [], sweep: sweep(3, 0, 2, 1), gaps: ['Still running.'],
  },
];
