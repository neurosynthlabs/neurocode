/* Who may do what. This file is the single catalogue: the web app reads it, and
   `npm run seed` exports the permissions and roles to the API, which enforces them. */

export interface Permission { id: string; label: string; group: 'Work' | 'Gates' | 'Knowledge' | 'Platform' | 'Admin'; description: string }
export interface RoleSeed { id: string; name: string; description: string; permissions: string[] }

export const permissions: Permission[] = [
  { id: 'plans:compile',    group: 'Work',      label: 'Compile requirements',     description: 'Turn a requirement into a plan and its task.' },
  { id: 'plans:decide',     group: 'Work',      label: 'Steer plans',              description: 'Answer or defer a plan’s questions, re-compile it and dispatch it.' },
  { id: 'tasks:write',      group: 'Work',      label: 'Move tasks',               description: 'Change a task’s status and tick its checklist.' },
  { id: 'runs:run',         group: 'Work',      label: 'Run agents',               description: 'Start an agent run in its own worktree, stop it, and discard its branch.' },
  { id: 'approvals:decide', group: 'Gates',     label: 'Approve gated actions',    description: 'Sign or refuse migrations, deploys and other high-risk actions.' },
  { id: 'decisions:make',   group: 'Gates',     label: 'Give final verdicts',      description: 'Accept a review or send it back, and open or cancel the production gate.' },
  { id: 'memory:write',     group: 'Knowledge', label: 'Curate memory',            description: 'Add, pin and archive facts, and settle conflicts between them.' },
  { id: 'ai:use',           group: 'Knowledge', label: 'Use AI features',          description: 'Ask memory, brainstorm, and extract facts from text.' },
  { id: 'projects:onboard', group: 'Platform',  label: 'Onboard projects',         description: 'Clone or read a repository and measure it.' },
  { id: 'mcp:manage',       group: 'Platform',  label: 'Manage tools',             description: 'Register MCP servers.' },
  { id: 'settings:write',   group: 'Platform',  label: 'Change screen settings',   description: 'Switch skills, plugins, hooks, commands and models on or off.' },
  { id: 'users:manage',     group: 'Admin',     label: 'Manage people',            description: 'Create accounts, assign roles, reset passwords and disable access.' },
  { id: 'roles:manage',     group: 'Admin',     label: 'Manage roles',             description: 'Create and edit custom roles.' },
  { id: 'teams:manage',     group: 'Admin',     label: 'Manage teams',             description: 'Create teams and choose their members.' },
  { id: 'audit:read',       group: 'Admin',     label: 'Read the audit log',       description: 'See who signed in and who changed access, keys or settings.' },
  { id: 'workspace:admin',  group: 'Admin',     label: 'Administer the workspace', description: 'The workspace name, AI providers and keys, and resetting data.' },
];

const ALL = permissions.map((p) => p.id);

export const roles: RoleSeed[] = [
  { id: 'owner', name: 'Owner', description: 'Everything, including access and keys. There is always at least one.', permissions: ALL },
  { id: 'admin', name: 'Admin', description: 'Runs the workspace: people, roles, keys and every screen.', permissions: ALL },
  {
    id: 'approver', name: 'AI Project Manager', description: 'Steers the work and signs the gates: the final approver.',
    permissions: ['plans:compile', 'plans:decide', 'tasks:write', 'runs:run', 'approvals:decide', 'decisions:make',
      'memory:write', 'ai:use', 'projects:onboard', 'mcp:manage', 'settings:write'],
  },
  { id: 'engineer', name: 'Engineer', description: 'Works the board and curates memory. Cannot sign the gates.', permissions: ['plans:compile', 'tasks:write', 'runs:run', 'memory:write', 'ai:use'] },
  { id: 'viewer', name: 'Viewer', description: 'Reads everything and changes nothing.', permissions: [] },
];

/* ── Demo mode only: the people, teams and audit trail the public demo shows. ── */

export interface DemoUser { id: string; email: string; name: string; status: 'active' | 'disabled'; roles: string[]; teams: string[]; lastLoginAt: string | null; createdAt: string }

export const demoUsers: DemoUser[] = [
  { id: 'u_rajat', email: 'rajat@neurocode.local', name: 'Rajat', status: 'active', roles: ['owner'], teams: ['t_core'], lastLoginAt: '2026-09-11T18:40:00', createdAt: '2026-09-10T09:00:00' },
  { id: 'u_priya', email: 'priya@neurocode.local', name: 'Priya Menon', status: 'active', roles: ['approver'], teams: ['t_core', 't_erp'], lastLoginAt: '2026-09-11T17:05:00', createdAt: '2026-09-10T11:20:00' },
  { id: 'u_arjun', email: 'arjun@neurocode.local', name: 'Arjun Shah', status: 'active', roles: ['engineer'], teams: ['t_erp'], lastLoginAt: '2026-09-11T15:48:00', createdAt: '2026-09-10T11:24:00' },
  { id: 'u_neha', email: 'neha@neurocode.local', name: 'Neha Kapoor', status: 'active', roles: ['viewer'], teams: [], lastLoginAt: null, createdAt: '2026-09-11T10:02:00' },
];

export const demoTeams = [
  { id: 't_core', name: 'Core', description: 'Owns the platform and the gates.', members: ['u_rajat', 'u_priya'], createdAt: '2026-09-10T09:05:00' },
  { id: 't_erp', name: 'ERP renewal', description: 'Legacy ERP modernisation, module by module.', members: ['u_priya', 'u_arjun'], createdAt: '2026-09-10T11:30:00' },
];

export const demoAudit = [
  { seq: 6, at: '2026-09-11T18:40:00', user: 'Rajat', action: 'auth.login', target: 'rajat@neurocode.local', detail: {} },
  { seq: 5, at: '2026-09-11T12:14:00', user: 'Rajat', action: 'user.update', target: 'Neha Kapoor', detail: { roles: ['viewer'] } },
  { seq: 4, at: '2026-09-11T10:02:00', user: 'Rajat', action: 'user.create', target: 'neha@neurocode.local', detail: { roles: ['engineer'] } },
  { seq: 3, at: '2026-09-10T11:30:00', user: 'Rajat', action: 'team.create', target: 'ERP renewal', detail: {} },
  { seq: 2, at: '2026-09-10T11:20:00', user: 'Rajat', action: 'user.create', target: 'priya@neurocode.local', detail: { roles: ['approver'] } },
  { seq: 1, at: '2026-09-10T09:00:00', user: 'Rajat', action: 'workspace.setup', target: 'NeuroCode', detail: {} },
];
