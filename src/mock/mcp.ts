import type { McpServer } from '@/types';

/** Servers whose tool output originates outside the operator's own machine. */
export const untrustedSourceIds = ['github', 'browser', 'fetch', 'jira', 'linear', 'sentry', 'slack'];

export const mcpServers: McpServer[] = [
  {
    id: 'filesystem', name: 'filesystem', transport: 'stdio', status: 'connected', scope: 'global',
    command: 'npx -y @modelcontextprotocol/server-filesystem /Users/rajat/work/careworks-erp',
    resources: 214, prompts: 0, latencyMs: 4, calls24h: 4128, errorRate: 0.02,
    tools: [
      { name: 'read_text_file', description: 'Read a file under the allowed roots, optional head/tail line window', risk: 'LOW' },
      { name: 'read_multiple_files', description: 'Batch read — used by Architect for impact analysis sweeps', risk: 'LOW' },
      { name: 'write_file', description: 'Overwrite a file inside the agent worktree only', risk: 'HIGH' },
      { name: 'edit_file', description: 'Line-based patch with dry-run diff preview', risk: 'MEDIUM' },
      { name: 'create_directory', description: 'mkdir -p under an allowed root', risk: 'LOW' },
      { name: 'move_file', description: 'Rename or relocate; refuses to cross root boundary', risk: 'MEDIUM' },
      { name: 'directory_tree', description: 'Recursive JSON tree, depth-capped at 6', risk: 'LOW' },
      { name: 'search_files', description: 'Glob + content grep across roots', risk: 'LOW' },
    ],
  },
  {
    id: 'git', name: 'git', transport: 'stdio', status: 'connected', scope: 'project',
    command: 'uvx mcp-server-git --repository /Users/rajat/work/careworks-erp',
    resources: 12, prompts: 2, latencyMs: 11, calls24h: 1873, errorRate: 0.1,
    tools: [
      { name: 'git_status', description: 'Working tree status for the active worktree', risk: 'LOW' },
      { name: 'git_diff_unstaged', description: 'Unified diff of uncommitted changes', risk: 'LOW' },
      { name: 'git_log', description: 'Commit history with author + file counts', risk: 'LOW' },
      { name: 'git_commit', description: 'Commit staged files — gated behind approval rule PR-04', risk: 'MEDIUM' },
      { name: 'git_create_branch', description: 'Branch from origin/main into task/*', risk: 'LOW' },
      { name: 'git_reset', description: 'Unstage; hard reset is blocked by policy', risk: 'HIGH' },
    ],
  },
  {
    id: 'github', name: 'github', transport: 'http', status: 'connected', scope: 'global',
    command: 'https://api.githubcopilot.com/mcp/  (PAT: gho_••••7f2a, repo+read:org)',
    resources: 6, prompts: 4, latencyMs: 312, calls24h: 604, errorRate: 1.8,
    tools: [
      { name: 'create_pull_request', description: 'Open PR from task/* into main', risk: 'HIGH' },
      { name: 'get_pull_request_diff', description: 'Fetch PR patch for Reviewer agent', risk: 'LOW' },
      { name: 'add_issue_comment', description: 'Post a comment — output is remote user text', risk: 'MEDIUM' },
      { name: 'search_code', description: 'Org-wide code search, 30 req/min', risk: 'LOW' },
      { name: 'list_workflow_runs', description: 'CI run status for the branch', risk: 'LOW' },
      { name: 'merge_pull_request', description: 'Squash-merge — human approval always required', risk: 'CRITICAL' },
    ],
  },
  {
    id: 'sqlserver', name: 'sqlserver', transport: 'stdio', status: 'connected', scope: 'project',
    command: 'node ./mcp/mssql/dist/index.js --server 10.2.14.9 --db CAREWORKS_ERP --readonly',
    resources: 1188, prompts: 3, latencyMs: 58, calls24h: 2461, errorRate: 0.4,
    tools: [
      { name: 'list_tables', description: '1,188 tables in CAREWORKS_ERP incl. MST_TAX, TRANS_INVOICE', risk: 'LOW' },
      { name: 'describe_table', description: 'Columns, PK/FK, defaults, computed columns', risk: 'LOW' },
      { name: 'get_stored_procedure_definition', description: 'Full text of SP_CalculateTax and 2,946 others', risk: 'LOW' },
      { name: 'search_stored_procedures_by_content', description: 'Grep proc bodies — found 31 callers of MST_TAX', risk: 'LOW' },
      { name: 'execute_query', description: 'SELECT only; connection opened with ApplicationIntent=ReadOnly', risk: 'MEDIUM' },
      { name: 'get_relationships', description: 'FK graph used by the impact analyser', risk: 'LOW' },
      { name: 'sample_data', description: 'TOP 100 rows, PII columns masked at the driver', risk: 'MEDIUM' },
    ],
  },
  {
    id: 'postgres', name: 'postgres', transport: 'stdio', status: 'connected', scope: 'project',
    command: 'uvx mcp-server-postgres postgresql://aios@localhost:5432/hims_v3',
    resources: 96, prompts: 0, latencyMs: 19, calls24h: 388, errorRate: 0.0,
    tools: [
      { name: 'query', description: 'Read-only SQL against hims_v3 (search_path=billing,opd)', risk: 'MEDIUM' },
      { name: 'list_schemas', description: 'billing / opd / ipd / pathology / audit', risk: 'LOW' },
      { name: 'explain', description: 'EXPLAIN ANALYZE for the reorder-point query in TASK-512', risk: 'LOW' },
    ],
  },
  {
    id: 'playwright', name: 'playwright', transport: 'stdio', status: 'connected', scope: 'global',
    command: 'npx -y @playwright/mcp@latest --browser chromium --isolated --viewport 1440x900',
    resources: 0, prompts: 1, latencyMs: 640, calls24h: 512, errorRate: 3.1,
    tools: [
      { name: 'browser_navigate', description: 'Open a URL in the isolated profile', risk: 'MEDIUM' },
      { name: 'browser_snapshot', description: 'Accessibility tree snapshot — cheaper than a screenshot', risk: 'LOW' },
      { name: 'browser_click', description: 'Click by ref from the last snapshot', risk: 'MEDIUM' },
      { name: 'browser_type', description: 'Type into a field; credential fields are refused', risk: 'MEDIUM' },
      { name: 'browser_take_screenshot', description: 'PNG for the Vision agent visual diff', risk: 'LOW' },
      { name: 'browser_console_messages', description: 'Console + page errors after a run', risk: 'LOW' },
    ],
  },
  {
    id: 'docker', name: 'docker', transport: 'stdio', status: 'connected', scope: 'global',
    command: 'docker run -i --rm -v /var/run/docker.sock:/var/run/docker.sock mcp/docker',
    resources: 0, prompts: 0, latencyMs: 74, calls24h: 231, errorRate: 0.9,
    tools: [
      { name: 'list_containers', description: 'erp-api, erp-web, mssql-2019, redis, ollama', risk: 'LOW' },
      { name: 'container_logs', description: 'Tail logs — DevOps agent pulls 500 lines per check', risk: 'LOW' },
      { name: 'compose_up', description: 'Bring the erp stack up from compose.staging.yml', risk: 'HIGH' },
      { name: 'exec', description: 'Run a command inside a container', risk: 'CRITICAL' },
      { name: 'prune', description: 'Reclaim space — blocked on staging by policy', risk: 'CRITICAL' },
    ],
  },
  {
    id: 'browser', name: 'browser', transport: 'sse', status: 'connected', scope: 'global',
    command: 'https://127.0.0.1:9223/sse  (chrome extension bridge, profile "research")',
    resources: 0, prompts: 0, latencyMs: 418, calls24h: 176, errorRate: 5.7,
    tools: [
      { name: 'get_page_text', description: 'Readable text of the active tab', risk: 'MEDIUM' },
      { name: 'read_console_messages', description: 'Console for the tab under test', risk: 'LOW' },
      { name: 'navigate', description: 'Open a URL in the operator profile — allowlisted hosts only', risk: 'HIGH' },
    ],
  },
  {
    id: 'memory', name: 'memory', transport: 'stdio', status: 'connected', scope: 'global',
    command: 'node ./mcp/aios-memory/index.js --store ~/.aios/memory.db --decay 0.94',
    resources: 3417, prompts: 6, latencyMs: 8, calls24h: 5902, errorRate: 0.01,
    tools: [
      { name: 'memory_search', description: 'Hybrid BM25 + vector over 3,417 memories (MEM-142 lives here)', risk: 'LOW' },
      { name: 'memory_write', description: 'Persist a lesson to global or project scope', risk: 'MEDIUM' },
      { name: 'memory_link', description: 'Relate a memory to a task, ADR or file', risk: 'LOW' },
      { name: 'memory_forget', description: 'Tombstone a memory — audited, never hard-deleted', risk: 'HIGH' },
    ],
  },
  {
    id: 'qdrant', name: 'qdrant', transport: 'http', status: 'connected', scope: 'global',
    command: 'http://localhost:6333/mcp  (collections: erp_code, erp_sprocs, adr, hinglish_pairs)',
    resources: 4, prompts: 0, latencyMs: 27, calls24h: 3140, errorRate: 0.06,
    tools: [
      { name: 'qdrant_search', description: 'BGE-M3 dense + sparse hybrid, top_k 40 then reranked', risk: 'LOW' },
      { name: 'qdrant_upsert', description: 'Index new chunks after a merge', risk: 'MEDIUM' },
      { name: 'collection_info', description: 'erp_code: 412,880 points, 1024-dim', risk: 'LOW' },
      { name: 'qdrant_delete', description: 'Drop points by filter — full-collection delete refused', risk: 'HIGH' },
    ],
  },
  {
    id: 'jira', name: 'jira', transport: 'http', status: 'auth_required', scope: 'project',
    command: 'https://careworks.atlassian.net/rest/mcp/v1  (OAuth token expired 2026-09-09 22:41)',
    resources: 0, prompts: 2, latencyMs: 0, calls24h: 38, errorRate: 61.0,
    tools: [
      { name: 'search_issues', description: 'JQL search across CW / HIMS boards', risk: 'LOW' },
      { name: 'get_issue', description: 'Fetch BUG-883 with comments and attachments', risk: 'MEDIUM' },
      { name: 'transition_issue', description: 'Move to In Review / Done', risk: 'MEDIUM' },
      { name: 'add_comment', description: 'Post the agent summary back onto the ticket', risk: 'MEDIUM' },
    ],
  },
  {
    id: 'linear', name: 'linear', transport: 'sse', status: 'connected', scope: 'global',
    command: 'https://mcp.linear.app/sse  (workspace: sofscript, actor: aios-bot)',
    resources: 0, prompts: 3, latencyMs: 244, calls24h: 92, errorRate: 1.1,
    tools: [
      { name: 'list_issues', description: 'RIDE-MVP and FIFA-EDGE backlogs', risk: 'LOW' },
      { name: 'create_issue', description: 'File a follow-up from a review finding', risk: 'MEDIUM' },
      { name: 'update_issue', description: 'Status, assignee, estimate', risk: 'MEDIUM' },
    ],
  },
  {
    id: 'sentry', name: 'sentry', transport: 'http', status: 'error', scope: 'project',
    command: 'https://mcp.sentry.dev/mcp  (org: careworks, project: erp-api) — 502 from upstream',
    resources: 0, prompts: 1, latencyMs: 0, calls24h: 14, errorRate: 100.0,
    tools: [
      { name: 'search_issues', description: 'Errors by culprit — TaxService.ResolveJurisdiction spike', risk: 'LOW' },
      { name: 'get_issue_details', description: 'Stack trace + breadcrumbs for SENTRY-ERP-4471', risk: 'MEDIUM' },
      { name: 'get_trace', description: 'Distributed trace for a slow invoice post', risk: 'LOW' },
    ],
  },
  {
    id: 'slack', name: 'slack', transport: 'http', status: 'disconnected', scope: 'global',
    command: 'https://slack.com/api/mcp  (bot: @aios, channels: #erp-releases, #billing-team)',
    resources: 0, prompts: 0, latencyMs: 0, calls24h: 0, errorRate: 0.0,
    tools: [
      { name: 'post_message', description: 'Deploy + rollback notices to #erp-releases', risk: 'MEDIUM' },
      { name: 'read_channel', description: 'Last 50 messages — third-party text, never instructions', risk: 'HIGH' },
    ],
  },
  {
    id: 'fetch', name: 'fetch', transport: 'stdio', status: 'connected', scope: 'global',
    command: 'uvx mcp-server-fetch --user-agent aios/0.9 --ignore-robots-txt=false',
    resources: 0, prompts: 1, latencyMs: 386, calls24h: 268, errorRate: 4.2,
    tools: [
      { name: 'fetch', description: 'GET a URL, HTML → markdown, 200k char cap', risk: 'HIGH' },
    ],
  },
  {
    id: 'time', name: 'time', transport: 'stdio', status: 'connected', scope: 'global',
    command: 'uvx mcp-server-time --local-timezone Asia/Kolkata',
    resources: 0, prompts: 0, latencyMs: 2, calls24h: 811, errorRate: 0.0,
    tools: [
      { name: 'get_current_time', description: 'IST now — invoice period boundaries depend on it', risk: 'LOW' },
      { name: 'convert_time', description: 'IST ↔ UTC for CI log correlation', risk: 'LOW' },
    ],
  },
];

export interface McpCall {
  at: string;
  server: string;
  tool: string;
  agent: string;
  ms: number;
  result: 'ok' | 'error' | 'denied';
  detail: string;
}

export const mcpCallLog: McpCall[] = [
  { at: '11:42:07', server: 'sqlserver', tool: 'get_stored_procedure_definition', agent: 'database', ms: 61, result: 'ok', detail: 'SP_CalculateTax → 1,142 lines' },
  { at: '11:42:03', server: 'filesystem', tool: 'read_text_file', agent: 'backend', ms: 4, result: 'ok', detail: 'src/Services/TaxService.cs:118-260' },
  { at: '11:41:58', server: 'qdrant', tool: 'qdrant_search', agent: 'architect', ms: 31, result: 'ok', detail: 'interstate cgst reversal → 40 hits' },
  { at: '11:41:51', server: 'sqlserver', tool: 'search_stored_procedures_by_content', agent: 'database', ms: 402, result: 'ok', detail: 'MST_TAX → 31 procs' },
  { at: '11:41:44', server: 'memory', tool: 'memory_search', agent: 'commander', ms: 9, result: 'ok', detail: 'MEM-142 rounding rule recalled' },
  { at: '11:41:30', server: 'sentry', tool: 'search_issues', agent: 'qa', ms: 5011, result: 'error', detail: '502 Bad Gateway from mcp.sentry.dev' },
  { at: '11:41:12', server: 'filesystem', tool: 'edit_file', agent: 'backend', ms: 7, result: 'ok', detail: 'TaxService.cs +18 −6 (dry-run then applied)' },
  { at: '11:40:55', server: 'git', tool: 'git_diff_unstaged', agent: 'reviewer', ms: 14, result: 'ok', detail: '7 files, +214 −61' },
  { at: '11:40:41', server: 'docker', tool: 'exec', agent: 'devops', ms: 0, result: 'denied', detail: 'policy PR-11: exec on mssql-2019 needs human' },
  { at: '11:40:22', server: 'playwright', tool: 'browser_take_screenshot', agent: 'vision', ms: 812, result: 'ok', detail: 'invoice-detail-after.png 1440x900' },
  { at: '11:39:58', server: 'github', tool: 'get_pull_request_diff', agent: 'reviewer', ms: 344, result: 'ok', detail: 'PR #1284 careworks/erp' },
  { at: '11:39:40', server: 'jira', tool: 'get_issue', agent: 'commander', ms: 0, result: 'error', detail: '401 token expired — reauth required' },
  { at: '11:39:21', server: 'sqlserver', tool: 'execute_query', agent: 'database', ms: 188, result: 'ok', detail: 'SELECT 41 rows FROM TRANS_INVOICE WHERE …' },
  { at: '11:38:57', server: 'fetch', tool: 'fetch', agent: 'researcher', ms: 921, result: 'ok', detail: 'cbic.gov.in place-of-supply circular (untrusted)' },
  { at: '11:38:33', server: 'memory', tool: 'memory_write', agent: 'commander', ms: 11, result: 'ok', detail: 'MEM-198 "verify state code before rounding"' },
  { at: '11:38:10', server: 'time', tool: 'get_current_time', agent: 'backend', ms: 2, result: 'ok', detail: 'Asia/Kolkata 2026-09-10T11:38:10+05:30' },
  { at: '11:37:44', server: 'filesystem', tool: 'search_files', agent: 'architect', ms: 68, result: 'ok', detail: 'glob **/*Tax*.cs → 9 files' },
  { at: '11:37:02', server: 'git', tool: 'git_commit', agent: 'backend', ms: 22, result: 'ok', detail: 'a91f3c2 fix(tax): jurisdiction resolve order' },
];

export const rawConfigs: Record<string, string> = {
  filesystem: `{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem",
               "/Users/rajat/work/careworks-erp"],
      "env": { "FS_MAX_BYTES": "2000000" },
      "scope": "global",
      "autoApprove": ["read_text_file", "search_files", "directory_tree"],
      "deny": ["write_file:/Users/rajat/work/careworks-erp/src/Legacy/**"]
    }
  }
}`,
  git: `{
  "mcpServers": {
    "git": {
      "command": "uvx",
      "args": ["mcp-server-git", "--repository",
               "/Users/rajat/work/careworks-erp"],
      "scope": "project",
      "autoApprove": ["git_status", "git_diff_unstaged", "git_log"],
      "requireApproval": ["git_commit", "git_reset"]
    }
  }
}`,
  github: `{
  "mcpServers": {
    "github": {
      "type": "http",
      "url": "https://api.githubcopilot.com/mcp/",
      "headers": { "Authorization": "Bearer \${GITHUB_PAT}" },
      "scope": "global",
      "trust": "untrusted-source",
      "requireApproval": ["create_pull_request", "merge_pull_request"]
    }
  }
}`,
  sqlserver: `{
  "mcpServers": {
    "sqlserver": {
      "command": "node",
      "args": ["./mcp/mssql/dist/index.js",
               "--server", "10.2.14.9",
               "--db", "CAREWORKS_ERP", "--readonly"],
      "env": { "MSSQL_APPLICATION_INTENT": "ReadOnly",
               "MSSQL_MASK_COLUMNS": "PAN,AADHAAR,MOBILE" },
      "scope": "project",
      "autoApprove": ["list_tables", "describe_table",
                      "get_stored_procedure_definition"]
    }
  }
}`,
  postgres: `{
  "mcpServers": {
    "postgres": {
      "command": "uvx",
      "args": ["mcp-server-postgres",
               "postgresql://aios@localhost:5432/hims_v3"],
      "scope": "project",
      "readOnly": true
    }
  }
}`,
  playwright: `{
  "mcpServers": {
    "playwright": {
      "command": "npx",
      "args": ["-y", "@playwright/mcp@latest", "--browser", "chromium",
               "--isolated", "--viewport", "1440x900"],
      "scope": "global",
      "deny": ["browser_type:password", "browser_type:otp"]
    }
  }
}`,
  docker: `{
  "mcpServers": {
    "docker": {
      "command": "docker",
      "args": ["run", "-i", "--rm",
               "-v", "/var/run/docker.sock:/var/run/docker.sock",
               "mcp/docker"],
      "scope": "global",
      "requireApproval": ["compose_up", "exec", "prune"],
      "deny": ["exec:mssql-2019", "prune:*"]
    }
  }
}`,
  browser: `{
  "mcpServers": {
    "browser": {
      "type": "sse",
      "url": "https://127.0.0.1:9223/sse",
      "scope": "global",
      "trust": "untrusted-source",
      "allowHosts": ["cbic.gov.in", "learn.microsoft.com",
                     "careworks.atlassian.net", "github.com"]
    }
  }
}`,
  memory: `{
  "mcpServers": {
    "memory": {
      "command": "node",
      "args": ["./mcp/aios-memory/index.js",
               "--store", "~/.aios/memory.db", "--decay", "0.94"],
      "scope": "global",
      "autoApprove": ["memory_search", "memory_link"],
      "requireApproval": ["memory_forget"]
    }
  }
}`,
  qdrant: `{
  "mcpServers": {
    "qdrant": {
      "type": "http",
      "url": "http://localhost:6333/mcp",
      "env": { "QDRANT_COLLECTIONS":
               "erp_code,erp_sprocs,adr,hinglish_pairs" },
      "scope": "global"
    }
  }
}`,
  jira: `{
  "mcpServers": {
    "jira": {
      "type": "http",
      "url": "https://careworks.atlassian.net/rest/mcp/v1",
      "auth": { "kind": "oauth2", "expiresAt": "2026-09-09T22:41:00+05:30" },
      "scope": "project",
      "trust": "untrusted-source"
    }
  }
}`,
  linear: `{
  "mcpServers": {
    "linear": {
      "type": "sse",
      "url": "https://mcp.linear.app/sse",
      "scope": "global",
      "trust": "untrusted-source",
      "actor": "aios-bot"
    }
  }
}`,
  sentry: `{
  "mcpServers": {
    "sentry": {
      "type": "http",
      "url": "https://mcp.sentry.dev/mcp",
      "env": { "SENTRY_ORG": "careworks", "SENTRY_PROJECT": "erp-api" },
      "scope": "project",
      "trust": "untrusted-source",
      "retry": { "attempts": 3, "backoffMs": 800 }
    }
  }
}`,
  slack: `{
  "mcpServers": {
    "slack": {
      "type": "http",
      "url": "https://slack.com/api/mcp",
      "scope": "global",
      "trust": "untrusted-source",
      "enabled": false,
      "channels": ["#erp-releases", "#billing-team"]
    }
  }
}`,
  fetch: `{
  "mcpServers": {
    "fetch": {
      "command": "uvx",
      "args": ["mcp-server-fetch", "--user-agent", "aios/0.9"],
      "scope": "global",
      "trust": "untrusted-source",
      "maxChars": 200000
    }
  }
}`,
  time: `{
  "mcpServers": {
    "time": {
      "command": "uvx",
      "args": ["mcp-server-time", "--local-timezone", "Asia/Kolkata"],
      "scope": "global",
      "autoApprove": ["get_current_time", "convert_time"]
    }
  }
}`,
};
