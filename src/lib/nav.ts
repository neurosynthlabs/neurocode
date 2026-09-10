export interface NavItem { to: string; label: string; icon: string; group: string; keywords: string }

/** Single source of truth: drives the sidebar, the router and ⌘K. */
export const NAV: NavItem[] = [
  { group: 'Workspace',    to: '/',            label: 'Command Center',    icon: 'LayoutDashboard', keywords: 'home requirement prompt start plan active work' },
  { group: 'Workspace',    to: '/projects',    label: 'Projects',          icon: 'FolderKanban',    keywords: 'repos erp taxi fifa switch onboard' },

  { group: 'Intelligence', to: '/memory',      label: 'Memory',            icon: 'Brain',           keywords: 'facts decisions recall long term human preferences' },
  { group: 'Intelligence', to: '/knowledge',   label: 'Knowledge',         icon: 'Library',         keywords: 'documents pdf meeting notes tickets ingestion rag' },
  { group: 'Intelligence', to: '/code',        label: 'Code Intelligence', icon: 'Dna',             keywords: 'ast tree-sitter lsp symbols dependencies legacy' },
  { group: 'Intelligence', to: '/architecture',label: 'Architecture',      icon: 'Network',         keywords: 'graph impact analysis blast radius modules' },

  { group: 'Execution',    to: '/agents',      label: 'Agents',            icon: 'Bot',             keywords: 'architect frontend backend qa reviewer devops security' },
  { group: 'Execution',    to: '/tasks',       label: 'Tasks',             icon: 'ListChecks',      keywords: 'kanban backlog epic breakdown board' },
  { group: 'Execution',    to: '/plans',       label: 'Plans',             icon: 'GitBranchPlus',   keywords: 'requirement compiler plan steps decomposition' },
  { group: 'Execution',    to: '/runs',        label: 'Live Runs',         icon: 'Activity',        keywords: 'parallel agents terminal output progress' },
  { group: 'Execution',    to: '/workflows',   label: 'Workflows',         icon: 'Workflow',        keywords: 'orchestration fan out pipeline judge panel scripts' },
  { group: 'Execution',    to: '/testing',     label: 'Testing',           icon: 'FlaskConical',    keywords: 'unit integration e2e playwright regression visual' },
  { group: 'Execution',    to: '/review',      label: 'Review',            icon: 'ScanEye',         keywords: 'code review solid findings approve changes' },
  { group: 'Execution',    to: '/git',         label: 'Git & Worktrees',   icon: 'GitMerge',        keywords: 'branches merge pr diff commits isolation' },
  { group: 'Execution',    to: '/devops',      label: 'DevOps',            icon: 'Rocket',          keywords: 'docker ci cd deploy environments logs secrets' },

  { group: 'Platform',     to: '/skills',      label: 'Skills',            icon: 'Sparkles',        keywords: 'capability packs procedures triggers reusable' },
  { group: 'Platform',     to: '/commands',    label: 'Commands',          icon: 'SquareSlash',     keywords: 'slash custom shortcuts macros' },
  { group: 'Platform',     to: '/hooks',       label: 'Hooks',             icon: 'Webhook',         keywords: 'pretooluse posttooluse events automation guard' },
  { group: 'Platform',     to: '/plugins',     label: 'Plugins',           icon: 'Blocks',          keywords: 'marketplace install packs extensions' },
  { group: 'Platform',     to: '/mcp',         label: 'MCP & Tools',       icon: 'Plug',            keywords: 'model context protocol servers filesystem sql playwright' },
  { group: 'Platform',     to: '/acp',         label: 'ACP Bridge',        icon: 'Cable',           keywords: 'agent client protocol editor zed neovim ide' },
  { group: 'Platform',     to: '/models',      label: 'Models & Router',   icon: 'Cpu',             keywords: 'qwen kimi deepseek routing fallback local ollama' },

  { group: 'Thinking',     to: '/brainstorm',  label: 'Brainstorm',        icon: 'Lightbulb',       keywords: 'idea devils advocate mvp roadmap product' },
  { group: 'Thinking',     to: '/research',    label: 'Research',          icon: 'Microscope',      keywords: 'web github docs deep research report sources' },

  { group: 'Governance',   to: '/activity',    label: 'Activity',          icon: 'ScrollText',      keywords: 'brain log timeline events audit' },
  { group: 'Governance',   to: '/sessions',    label: 'Sessions',          icon: 'History',         keywords: 'checkpoints rewind resume fork transcript' },
  { group: 'Governance',   to: '/permissions', label: 'Permissions',       icon: 'ShieldCheck',     keywords: 'approval gates allow deny risk sandbox' },
  { group: 'Governance',   to: '/cost',        label: 'Cost & Usage',      icon: 'Coins',           keywords: 'tokens spend budget savings local remote' },
  { group: 'Governance',   to: '/evals',       label: 'Evals',             icon: 'Gauge',           keywords: 'benchmark score regression quality judge' },
  { group: 'Governance',   to: '/settings',    label: 'Settings',          icon: 'Settings',        keywords: 'general api keys appearance notifications' },
];

export const NAV_GROUPS = ['Workspace', 'Intelligence', 'Execution', 'Platform', 'Thinking', 'Governance'] as const;
