import type { SettingGroup } from '@/types';

export const settingsGroups: SettingGroup[] = [
  { id: 'general', name: 'General', items: [
    { id: 'general.language', label: 'Requirement language', description: 'The OS accepts mixed Hindi/English and compiles it. It never answers a Hinglish requirement literally.', kind: 'select', value: 'Hinglish + English', options: ['Hinglish + English', 'English only', 'Auto-detect'] },
    { id: 'general.startup', label: 'Restore last project on start', description: 'Reopens the active project, its rules and the tasks in flight.', kind: 'toggle', value: true },
    { id: 'general.confirm', label: 'Confirm before switching project', description: 'Switching swaps memory, rules and agents — a misclick is disorienting mid-task.', kind: 'toggle', value: false },
    { id: 'general.telemetry', label: 'Send usage telemetry', description: 'Off, and there is no endpoint configured to send it to.', kind: 'toggle', value: false },
  ]},
  { id: 'projects', name: 'Projects', items: [
    { id: 'projects.autoOnboard', label: 'Auto-onboard on first open', description: 'Runs the 18-step pipeline the first time a repository is opened.', kind: 'toggle', value: true },
    { id: 'projects.reindex', label: 'Full re-index schedule', description: 'Incremental indexing runs on save; a full pass rebuilds the call graph from scratch.', kind: 'select', value: 'Nightly 07:00', options: ['Nightly 07:00', 'Weekly Sunday', 'Manual only'] },
    { id: 'projects.excluded', label: 'Excluded paths', description: 'Never parsed, never embedded, never shown to a model.', kind: 'text', value: 'node_modules, bin, obj, dist, **/*.designer.cs' },
  ]},
  { id: 'models', name: 'Models', items: [
    { id: 'models.router', label: 'Automatic model selection', description: 'The router picks per task class instead of pinning one model to everything.', kind: 'toggle', value: true },
    { id: 'models.localFirst', label: 'Prefer local weights', description: 'Route to local inference whenever it clears the eval bar for that task class.', kind: 'toggle', value: true },
    { id: 'models.reasoning', label: 'Reasoning model', description: 'Architecture, legacy risk and hard debugging.', kind: 'select', value: 'DeepSeek-V3.2', options: ['DeepSeek-V3.2', 'Kimi-K2.5', 'Qwen3-Next-80B'] },
    { id: 'models.coding', label: 'Coding model', description: 'The overwhelming majority of calls land here.', kind: 'select', value: 'Qwen3-Coder-Next', options: ['Qwen3-Coder-Next', 'GLM-4.7', 'Qwen3-Coder-30B (local)'] },
    { id: 'models.embedding', label: 'Embedding model', description: 'Used for every retrieval; changing it requires a full re-index.', kind: 'select', value: 'BGE-M3', options: ['BGE-M3', 'Qwen3-Embedding'] },
    { id: 'models.budget', label: 'Daily spend ceiling', description: 'At 90% the router stops offering remote models entirely.', kind: 'text', value: '$25.00' },
  ]},
  { id: 'memory', name: 'Memory', items: [
    { id: 'memory.decay', label: 'Decay unused facts', description: 'Facts lose strength on a per-category half-life and regain it on every retrieval hit.', kind: 'toggle', value: true },
    { id: 'memory.pinExempt', label: 'Pinned facts never decay', description: 'Pinned and HIGH-confidence decisions are exempt regardless of age.', kind: 'toggle', value: true },
    { id: 'memory.retire', label: 'Retirement threshold', description: 'Below this strength a fact is archived — never deleted.', kind: 'select', value: '20', options: ['10', '20', '30', 'never retire'] },
    { id: 'memory.compaction', label: 'Survive context compaction', description: 'Durable facts are written to the store before the window is summarised.', kind: 'toggle', value: true },
    { id: 'memory.evidence', label: 'Require evidence to write a fact', description: 'A fact without a source is refused at write time.', kind: 'toggle', value: true },
  ]},
  { id: 'mcp', name: 'MCP', items: [
    { id: 'mcp.autoConnect', label: 'Connect servers on start', description: 'Servers marked global connect when a session opens.', kind: 'toggle', value: true },
    { id: 'mcp.untrusted', label: 'Treat tool output as untrusted', description: 'Tool results are data. An instruction found inside one is surfaced, never obeyed.', kind: 'toggle', value: true },
    { id: 'mcp.timeout', label: 'Tool call timeout', description: 'A hung server should not hang the agent.', kind: 'select', value: '30s', options: ['10s', '30s', '60s', '120s'] },
  ]},
  { id: 'agents', name: 'Agents', items: [
    { id: 'agents.autonomy', label: 'Default autonomy', description: 'How much a newly registered agent may do before a gate.', kind: 'select', value: 'semi', options: ['supervised', 'semi', 'autonomous'] },
    { id: 'agents.concurrency', label: 'Concurrency cap', description: 'How many agents may run at once. Higher is not always faster — review becomes the bottleneck.', kind: 'select', value: '8', options: ['2', '4', '8', '12', '16'] },
    { id: 'agents.worktrees', label: 'Max parallel worktrees', description: 'One agent per worktree, always. This caps disk and merge complexity.', kind: 'select', value: '6', options: ['2', '4', '6', '10'] },
    { id: 'agents.handoff', label: 'Require a handoff packet', description: 'An agent cannot report done without recording files touched, decisions made and open questions.', kind: 'toggle', value: true },
  ]},
  { id: 'permissions', name: 'Permissions', items: [
    { id: 'perm.prodGate', label: 'Production requires a human', description: 'Cannot be disabled — this switch exists to show you that it cannot.', kind: 'toggle', value: true },
    { id: 'perm.migrationGate', label: 'Migrations require a human', description: 'With a dry-run count and a verified rollback attached.', kind: 'toggle', value: true },
    { id: 'perm.depGate', label: 'New dependencies require a human', description: 'Supply chain is your signature, not the agent\'s.', kind: 'toggle', value: true },
    { id: 'perm.autoApproveLow', label: 'Auto-approve LOW risk', description: 'Reads, searches and test runs never reach the inbox.', kind: 'toggle', value: true },
  ]},
  { id: 'security', name: 'Security', items: [
    { id: 'sec.secretScan', label: 'Secret scan before commit', description: 'gitleaks on staged changes. A hit aborts the commit with no bypass.', kind: 'toggle', value: true },
    { id: 'sec.egress', label: 'Deny network egress in test containers', description: 'A test that needs the internet is a test that will flake.', kind: 'toggle', value: true },
    { id: 'sec.masked', label: 'Mask the snapshot replica', description: 'Customer names, phone numbers and GSTINs are replaced before an agent can read a row.', kind: 'toggle', value: true },
    { id: 'sec.blockCritical', label: 'Block merge on CRITICAL finding', description: 'The Security Engineer can stop a pipeline outright.', kind: 'toggle', value: true },
  ]},
  { id: 'git', name: 'Git', items: [
    { id: 'git.worktree', label: 'One worktree per task', description: 'Agents never share a checkout. This is what makes parallelism safe.', kind: 'toggle', value: true },
    { id: 'git.mainProtect', label: 'Refuse pushes to main', description: 'PR only, always.', kind: 'toggle', value: true },
    { id: 'git.conventional', label: 'Enforce conventional commits', description: 'Rejected at PreCommit if the message does not parse.', kind: 'toggle', value: true },
    { id: 'git.signoff', label: 'Attribute commits to the agent', description: 'The author line names which agent wrote it, so blame stays honest.', kind: 'toggle', value: true },
  ]},
  { id: 'testing', name: 'Testing', items: [
    { id: 'test.runOnStop', label: 'Run affected tests before done', description: 'An agent cannot claim success without a real runner output.', kind: 'toggle', value: true },
    { id: 'test.visual', label: 'Visual diff on UI changes', description: 'Playwright screenshot against the stored baseline.', kind: 'toggle', value: true },
    { id: 'test.legacyExpected', label: 'Separate legacy-expected failures', description: 'A documented legacy failure is information, not a regression.', kind: 'toggle', value: true },
    { id: 'test.flakeQuarantine', label: 'Quarantine flaky tests', description: 'Three inconsistent runs moves a test out of the gate and into a report.', kind: 'toggle', value: true },
  ]},
  { id: 'notifications', name: 'Notifications', items: [
    { id: 'notif.approvals', label: 'Notify on approval needed', description: 'The only interruption that is always worth it.', kind: 'toggle', value: true },
    { id: 'notif.blocked', label: 'Notify when a task blocks', description: 'A blocked agent burns nothing, but it also finishes nothing.', kind: 'toggle', value: true },
    { id: 'notif.done', label: 'Notify on task completion', description: 'Off by default — completion is visible on the board.', kind: 'toggle', value: false },
    { id: 'notif.channel', label: 'Delivery', description: 'Where a notification actually lands.', kind: 'select', value: 'Desktop', options: ['Desktop', 'Desktop + Slack', 'Slack only', 'None'] },
  ]},
  { id: 'appearance', name: 'Appearance', items: [] },
  { id: 'apikeys', name: 'API Keys', items: [
    { id: 'key.deepseek', label: 'DeepSeek', description: 'Reasoning and planning. Stored in the OS keychain, never rendered into a prompt.', kind: 'secret', value: '' },
    { id: 'key.moonshot', label: 'Moonshot (Kimi)', description: 'Vision and review.', kind: 'secret', value: '' },
    { id: 'key.zhipu', label: 'Zhipu (GLM)', description: 'Fallback coding model.', kind: 'secret', value: '' },
    { id: 'key.github', label: 'GitHub', description: 'Repository and issue access for the research agent.', kind: 'secret', value: '' },
    { id: 'key.jira', label: 'Jira', description: 'Outbound ticket creation — always behind an approval.', kind: 'secret', value: '' },
  ]},
];
