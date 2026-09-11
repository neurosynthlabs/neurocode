export type NavSection = 'Home' | 'Build' | 'Knowledge' | 'Platform' | 'Governance' | 'Admin';

export interface NavItem {
  to: string;
  label: string;
  icon: string;
  section: NavSection;
  /** Consecutive items with the same `sub` fold into one sub-section in the sidebar. */
  sub?: string;
  keywords: string;
  /** Shown only to people holding at least one of these permissions. The API enforces them regardless. */
  perm?: string[];
}

/** Single source of truth: drives the sidebar, the router and ⌘K. */
export const NAV: NavItem[] = [
  { section: 'Home', to: '/',            label: 'Command Center',    icon: 'LayoutDashboard', keywords: 'home requirement prompt start plan active work ask' },
  { section: 'Home', to: '/activity',    label: 'Activity',          icon: 'ScrollText',      keywords: 'brain log timeline events' },
  { section: 'Home', to: '/projects',    label: 'Projects',          icon: 'FolderKanban',    keywords: 'repos erp taxi fifa switch onboard' },

  { section: 'Build', sub: 'Planning',   to: '/plans',     label: 'Plans',           icon: 'GitBranchPlus', keywords: 'requirement compiler plan steps decomposition' },
  { section: 'Build', sub: 'Planning',   to: '/tasks',     label: 'Tasks',           icon: 'ListChecks',    keywords: 'kanban backlog epic breakdown board' },
  { section: 'Build', sub: 'Execution',  to: '/agents',    label: 'Agents',          icon: 'Bot',           keywords: 'architect frontend backend qa reviewer devops security' },
  { section: 'Build', sub: 'Execution',  to: '/runs',      label: 'Live Runs',       icon: 'Activity',      keywords: 'parallel agents terminal output progress' },
  { section: 'Build', sub: 'Execution',  to: '/workflows', label: 'Workflows',       icon: 'Workflow',      keywords: 'orchestration fan out pipeline judge panel scripts' },
  { section: 'Build', sub: 'Quality',    to: '/testing',   label: 'Testing',         icon: 'FlaskConical',  keywords: 'unit integration e2e playwright regression visual' },
  { section: 'Build', sub: 'Quality',    to: '/review',    label: 'Review',          icon: 'ScanEye',       keywords: 'code review solid findings approve changes' },
  { section: 'Build', sub: 'Quality',    to: '/evals',     label: 'Evals',           icon: 'Gauge',         keywords: 'benchmark score regression quality judge' },
  { section: 'Build', sub: 'Delivery',   to: '/git',       label: 'Git & Worktrees', icon: 'GitMerge',      keywords: 'branches merge pr diff commits isolation' },
  { section: 'Build', sub: 'Delivery',   to: '/devops',    label: 'DevOps',          icon: 'Rocket',        keywords: 'docker ci cd deploy environments logs secrets' },

  { section: 'Knowledge', to: '/memory',       label: 'Memory',            icon: 'Brain',   keywords: 'facts decisions recall long term human preferences extract add from text' },
  { section: 'Knowledge', to: '/knowledge',    label: 'Knowledge',         icon: 'Library', keywords: 'documents pdf meeting notes tickets ingestion rag' },
  { section: 'Knowledge', to: '/code',         label: 'Code Intelligence', icon: 'Dna',     keywords: 'ast tree-sitter lsp symbols dependencies legacy' },
  { section: 'Knowledge', to: '/architecture', label: 'Architecture',      icon: 'Network', keywords: 'graph impact analysis blast radius modules' },
  { section: 'Knowledge', sub: 'Thinking', to: '/brainstorm', label: 'Brainstorm', icon: 'Lightbulb',  keywords: 'idea devils advocate mvp roadmap product brief' },
  { section: 'Knowledge', sub: 'Thinking', to: '/research',   label: 'Research',   icon: 'Microscope', keywords: 'web github docs deep research report sources' },

  { section: 'Platform', sub: 'Extensions',  to: '/skills',   label: 'Skills',          icon: 'Sparkles',    keywords: 'capability packs procedures triggers reusable' },
  { section: 'Platform', sub: 'Extensions',  to: '/commands', label: 'Commands',        icon: 'SquareSlash', keywords: 'slash custom shortcuts macros' },
  { section: 'Platform', sub: 'Extensions',  to: '/hooks',    label: 'Hooks',           icon: 'Webhook',     keywords: 'pretooluse posttooluse events automation guard' },
  { section: 'Platform', sub: 'Extensions',  to: '/plugins',  label: 'Plugins',         icon: 'Blocks',      keywords: 'marketplace install packs extensions' },
  { section: 'Platform', sub: 'Connections', to: '/mcp',      label: 'MCP & Tools',     icon: 'Plug',        keywords: 'model context protocol servers filesystem sql playwright' },
  { section: 'Platform', sub: 'Connections', to: '/acp',      label: 'ACP Bridge',      icon: 'Cable',       keywords: 'agent client protocol editor zed neovim ide' },
  { section: 'Platform', sub: 'Connections', to: '/models',   label: 'Models & Router', icon: 'Cpu',         keywords: 'qwen kimi deepseek routing fallback local ollama' },

  { section: 'Governance', to: '/permissions', label: 'Permissions',  icon: 'ShieldCheck', keywords: 'approval gates allow deny risk sandbox' },
  { section: 'Governance', to: '/cost',        label: 'Cost & Usage', icon: 'Coins',       keywords: 'tokens spend budget savings local remote' },
  { section: 'Governance', to: '/sessions',    label: 'Sessions',     icon: 'History',     keywords: 'checkpoints rewind resume fork transcript' },
  { section: 'Governance', to: '/settings',    label: 'Settings',     icon: 'Settings',    keywords: 'general appearance notifications preferences' },

  { section: 'Admin', to: '/admin/users',     label: 'People',              icon: 'Users',        perm: ['users:manage', 'teams:manage'], keywords: 'users accounts invite members disable reset password' },
  { section: 'Admin', to: '/admin/roles',     label: 'Roles & permissions', icon: 'KeyRound',     perm: ['roles:manage', 'users:manage'], keywords: 'rbac access control custom role grant' },
  { section: 'Admin', to: '/admin/teams',     label: 'Teams',               icon: 'UsersRound',   perm: ['teams:manage', 'users:manage'], keywords: 'groups squads members' },
  { section: 'Admin', to: '/admin/ai',        label: 'AI providers',        icon: 'BrainCircuit', perm: ['workspace:admin'],              keywords: 'deepseek ollama api key model routing offline test connection' },
  { section: 'Admin', to: '/admin/audit',     label: 'Audit log',           icon: 'FileClock',    perm: ['audit:read'],                   keywords: 'security sign in history who changed access' },
  { section: 'Admin', to: '/admin/workspace', label: 'Workspace',           icon: 'Building2',    perm: ['workspace:admin'],              keywords: 'name organisation reset data database' },
];

export const NAV_SECTIONS: NavSection[] = ['Home', 'Build', 'Knowledge', 'Platform', 'Governance', 'Admin'];

/** Whether someone may open a screen. Pass `useAuth().canAny`. */
export const allowed = (item: Pick<NavItem, 'perm'>, canAny: (...perms: string[]) => boolean) =>
  !item.perm || canAny(...item.perm);
