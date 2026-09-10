import type { Project } from '@/types';

export const projects: Project[] = [
  {
    id: 'erp', name: 'Legacy ERP', codename: 'CAREWORKS-ERP',
    stack: ['React 18', 'ASP.NET Core 8', 'SQL Server 2019', 'IIS'],
    kind: 'legacy', status: 'active',
    memoryPct: 94, understoodPct: 87, lines: '2.4M', modules: 47, dbTables: 382, storedProcs: 1148,
    repo: 'git@github.com:sofscript/careworks-erp.git', lastActive: '2 min ago',
    coverage: [
      { label: 'Architecture', pct: 91 }, { label: 'Legacy Knowledge', pct: 87 },
      { label: 'Database Knowledge', pct: 94 }, { label: 'Business Rules', pct: 72 },
      { label: 'Test Coverage', pct: 68 }, { label: 'DevOps', pct: 61 },
    ],
    work: { tasks: 12, running: 4, review: 5, blocked: 3 },
    description: 'Module-by-module renewal of a 14-year-old accounting + inventory ERP. Strict SOLID, class-object naming, no direct TRANS_* writes.',
  },
  {
    id: 'hims', name: 'HIMS v3', codename: 'HIMS-PMI',
    stack: ['React 19', 'ASP.NET Core 8', 'SQL Server', 'Redis'],
    kind: 'legacy', status: 'active',
    memoryPct: 81, understoodPct: 74, lines: '1.1M', modules: 31, dbTables: 214, storedProcs: 640,
    repo: 'git@github.com:sofscript/hims-v3.git', lastActive: '41 min ago',
    coverage: [
      { label: 'Architecture', pct: 84 }, { label: 'Legacy Knowledge', pct: 74 },
      { label: 'Database Knowledge', pct: 88 }, { label: 'Business Rules', pct: 66 },
      { label: 'Test Coverage', pct: 54 }, { label: 'DevOps', pct: 70 },
    ],
    work: { tasks: 9, running: 2, review: 3, blocked: 1 },
    description: 'Hospital information system — OPD/IPD/billing. Renewal in progress with a strict compatibility contract against the legacy PMI schema.',
  },
  {
    id: 'taxi', name: 'Taxi Marketplace', codename: 'RIDE-MVP',
    stack: ['Next.js 15', 'FastAPI', 'PostgreSQL', 'Redis'],
    kind: 'greenfield', status: 'active',
    memoryPct: 68, understoodPct: 92, lines: '84K', modules: 12, dbTables: 38, storedProcs: 0,
    repo: 'git@github.com:sofscript/ride-mvp.git', lastActive: '3 h ago',
    coverage: [
      { label: 'Architecture', pct: 95 }, { label: 'Legacy Knowledge', pct: 100 },
      { label: 'Database Knowledge', pct: 91 }, { label: 'Business Rules', pct: 78 },
      { label: 'Test Coverage', pct: 83 }, { label: 'DevOps', pct: 88 },
    ],
    work: { tasks: 6, running: 1, review: 2, blocked: 0 },
    description: 'Greenfield driver/rider marketplace. Built entirely through the OS — used as the reference implementation for new conventions.',
  },
  {
    id: 'fifa', name: 'FIFA Stats', codename: 'FIFA-EDGE',
    stack: ['React 19', 'Node 22', 'ClickHouse'],
    kind: 'greenfield', status: 'paused',
    memoryPct: 55, understoodPct: 79, lines: '46K', modules: 8, dbTables: 19, storedProcs: 0,
    repo: 'git@github.com:sofscript/fifa-edge.git', lastActive: '6 d ago',
    coverage: [
      { label: 'Architecture', pct: 82 }, { label: 'Legacy Knowledge', pct: 100 },
      { label: 'Database Knowledge', pct: 74 }, { label: 'Business Rules', pct: 61 },
      { label: 'Test Coverage', pct: 45 }, { label: 'DevOps', pct: 52 },
    ],
    work: { tasks: 3, running: 0, review: 0, blocked: 1 },
    description: 'Match analytics side project. Paused — memory retained, agents released.',
  },
  {
    id: 'aios', name: 'NeuroCode', codename: 'SELF',
    stack: ['React 19', 'Vite 8', 'FastAPI', 'LangGraph', 'Qdrant'],
    kind: 'platform', status: 'onboarding',
    memoryPct: 47, understoodPct: 58, lines: '31K', modules: 9, dbTables: 24, storedProcs: 0,
    repo: 'git@github.com:sofscript/neurocode.git', lastActive: 'just now',
    coverage: [
      { label: 'Architecture', pct: 72 }, { label: 'Legacy Knowledge', pct: 100 },
      { label: 'Database Knowledge', pct: 48 }, { label: 'Business Rules', pct: 39 },
      { label: 'Test Coverage', pct: 31 }, { label: 'DevOps', pct: 44 },
    ],
    work: { tasks: 5, running: 1, review: 1, blocked: 0 },
    description: 'The OS building itself. Self-improvement loop enabled — every failed task writes a lesson back into global memory.',
  },
];

export const activeProjectId = 'erp';
export const getProject = (id: string) => projects.find((p) => p.id === id) ?? projects[0];
export const projectName = (id: string) => projects.find((p) => p.id === id)?.name ?? id;
