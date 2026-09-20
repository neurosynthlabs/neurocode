/**
 * The product's own map. This file is one half of a pair: the other is
 * `server/app/data/catalogue.json`, where every permission names the `module` and `sub` it belongs
 * to — these exact words. A right filed under a module the sidebar does not have is a right nobody
 * can find, so a test holds the two against each other (server/tests/test_rights_by_module.py).
 */
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
  { section: 'Home', to: '/projects',    label: 'Projects',          icon: 'FolderKanban',    keywords: 'repos repository switch onboard clone measure' },

  { section: 'Build', sub: 'Planning',   to: '/blueprints', label: 'Blueprints',   icon: 'Compass',       keywords: 'design new system architecture wizard template stack framework scaffold greenfield devops' },
  { section: 'Build', sub: 'Planning',   to: '/plans',     label: 'Plans',           icon: 'GitBranchPlus', keywords: 'requirement compiler plan steps decomposition' },
  { section: 'Build', sub: 'Planning',   to: '/tasks',     label: 'Tasks',           icon: 'ListChecks',    keywords: 'kanban backlog epic breakdown board' },
  { section: 'Build', sub: 'Execution',  to: '/workbench', label: 'Workbench',       icon: 'Code2',         perm: ['machine:access'], keywords: 'editor files folders open code terminal run debug breakpoints machine ide' },
  { section: 'Build', sub: 'Execution',  to: '/agents',    label: 'Agents',          icon: 'Bot',           keywords: 'architect frontend backend qa reviewer devops security' },
  { section: 'Build', sub: 'Execution',  to: '/runs',      label: 'Live Runs',       icon: 'Activity',      keywords: 'parallel agents terminal output progress' },
  { section: 'Build', sub: 'Execution',  to: '/workflows', label: 'Workflows',       icon: 'Workflow',      keywords: 'orchestration fan out pipeline judge panel scripts' },
  { section: 'Build', sub: 'Execution',  to: '/routines',  label: 'Routines',        icon: 'CalendarClock',     keywords: 'schedule cron cadence nightly recurring webhook trigger automation run now' },
  { section: 'Build', sub: 'Quality',    to: '/testing',   label: 'Testing',         icon: 'FlaskConical',  keywords: 'unit integration e2e playwright regression visual' },
  { section: 'Build', sub: 'Quality',    to: '/review',    label: 'Review',          icon: 'ScanEye',       keywords: 'code review solid findings approve changes' },
  { section: 'Build', sub: 'Quality',    to: '/evals',     label: 'Evals',           icon: 'Gauge',         keywords: 'benchmark score regression quality judge' },
  { section: 'Build', sub: 'Delivery',   to: '/git',       label: 'Git & Worktrees', icon: 'GitMerge',      keywords: 'branches merge pr diff commits isolation' },
  { section: 'Build', sub: 'Delivery',   to: '/devops',    label: 'DevOps',          icon: 'Rocket',        perm: ['ops:read'],      keywords: 'docker ci cd deploy environments logs secrets' },

  { section: 'Knowledge', to: '/memory',       label: 'Memory',            icon: 'Brain',   keywords: 'facts decisions recall long term human preferences extract add from text' },
  { section: 'Knowledge', to: '/knowledge',    label: 'Knowledge',         icon: 'Library', keywords: 'documents pdf meeting notes tickets ingestion rag' },
  { section: 'Knowledge', to: '/code',         label: 'Code Intelligence', icon: 'Dna',     keywords: 'ast tree-sitter lsp symbols dependencies legacy' },
  { section: 'Knowledge', to: '/architecture', label: 'Architecture',      icon: 'Network', keywords: 'graph impact analysis blast radius modules' },
  { section: 'Knowledge', sub: 'Thinking', to: '/brainstorm', label: 'Brainstorm', icon: 'Lightbulb',  keywords: 'idea devils advocate mvp roadmap product brief' },
  { section: 'Knowledge', sub: 'Thinking', to: '/research',   label: 'Research',   icon: 'Microscope', keywords: 'web github docs deep research report sources' },

  { section: 'Platform', sub: 'Extensions',  to: '/skills',   label: 'Skills',          icon: 'Sparkles',    keywords: 'capability packs procedures triggers reusable' },
  { section: 'Platform', sub: 'Extensions',  to: '/commands', label: 'Commands',        icon: 'SquareSlash', keywords: 'slash custom shortcuts macros' },
  { section: 'Platform', sub: 'Extensions',  to: '/hooks',    label: 'Hooks',           icon: 'Webhook',     perm: ['settings:write'], keywords: 'pretooluse posttooluse events automation guard' },
  { section: 'Platform', sub: 'Extensions',  to: '/plugins',  label: 'Plugins',         icon: 'Blocks',      keywords: 'marketplace install packs extensions' },
  { section: 'Platform', sub: 'Connections', to: '/mcp',      label: 'MCP & Tools',     icon: 'Plug',        keywords: 'model context protocol servers filesystem sql playwright' },
  { section: 'Platform', sub: 'Connections', to: '/models',   label: 'Models & Router', icon: 'Cpu',         keywords: 'lanes groq cerebras gemini deepseek routing keys local ollama' },

  { section: 'Governance', to: '/permissions', label: 'Permissions',  icon: 'ShieldCheck', keywords: 'approval gates allow deny risk sandbox' },
  { section: 'Governance', to: '/cost',        label: 'Cost & Usage', icon: 'Coins',       keywords: 'tokens spend ledger price agent project lane' },
  { section: 'Governance', to: '/sessions',    label: 'Sessions',     icon: 'History',     keywords: 'checkpoints rewind resume fork transcript' },
  { section: 'Governance', to: '/settings',    label: 'Settings',     icon: 'Settings',    keywords: 'general appearance notifications preferences' },

  { section: 'Admin', to: '/admin/users',     label: 'People',              icon: 'Users',        perm: ['people:read', 'users:manage', 'teams:manage'], keywords: 'users accounts invite members disable reset password' },
  { section: 'Admin', to: '/admin/roles',     label: 'Roles & permissions', icon: 'KeyRound',     perm: ['roles:manage', 'users:manage'], keywords: 'rbac access control custom role grant' },
  { section: 'Admin', to: '/admin/teams',     label: 'Teams',               icon: 'UsersRound',   perm: ['people:read', 'teams:manage', 'users:manage'], keywords: 'groups squads members' },
  { section: 'Admin', to: '/admin/ai',        label: 'AI providers',        icon: 'BrainCircuit', perm: ['workspace:admin'],              keywords: 'deepseek ollama api key model routing offline test connection' },
  { section: 'Admin', to: '/admin/audit',     label: 'Audit log',           icon: 'FileClock',    perm: ['audit:read'],                   keywords: 'security sign in history who changed access' },
  { section: 'Admin', to: '/admin/workspace', label: 'Workspace',           icon: 'Building2',    perm: ['workspace:admin'],              keywords: 'name organisation reset data' },
  { section: 'Admin', to: '/admin/database',  label: 'Database',            icon: 'Database',     perm: ['workspace:admin'],              keywords: 'postgres backup integrity check migrations optimize tables' },
];

export const NAV_SECTIONS: NavSection[] = ['Home', 'Build', 'Knowledge', 'Platform', 'Governance', 'Admin'];

/** Whether someone may open a screen. Pass `useAuth().canAny`. */
export const allowed = (item: Pick<NavItem, 'perm'>, canAny: (...perms: string[]) => boolean) =>
  !item.perm || canAny(...item.perm);
