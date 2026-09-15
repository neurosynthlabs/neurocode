import { createContext, useContext, useState, type ReactNode } from 'react';
import { activeProjectId } from '@/mock/projects';
import { useData } from '@/lib/data';
import type { Project } from '@/types';

interface Ctx {
  projectId: string;
  setProjectId: (id: string) => void;
  /** The active project. `NONE` — id `''` — until something is onboarded. Check `all.length` or
   *  `projectId` before asking the API about it; everything else can read it safely. */
  project: Project;
  all: Project[];
}
const C = createContext<Ctx | null>(null);

/* A real workspace starts empty, and every screen reads the active project's name the moment it
   renders. Rather than make thirty call sites test for undefined — which one of them would forget,
   bringing the whole app down the way `projects[0].id` already did — there is one stand-in, with an
   empty id so nothing mistakes it for something the API knows about. */
const NONE: Project = {
  id: '', name: 'No project yet', codename: '', stack: [], kind: 'platform', status: 'onboarding',
  memoryPct: 0, understoodPct: 0, lines: '0', modules: 0, dbTables: 0, storedProcs: 0, repo: '',
  lastActive: '', coverage: [], work: { tasks: 0, running: 0, review: 0, blocked: 0 },
  description: 'Onboard a repository from Projects, and this fills in.',
};

/** The active project. The list comes from the data store, so an onboarded project can be switched to. */
export function ProjectProvider({ children }: { children: ReactNode }) {
  const { projects } = useData();
  const [picked, setProjectId] = useState(activeProjectId);
  // If the picked project disappears (a reset, say), fall back to the first one everywhere at once —
  // and to none at all when there are none, which is what a freshly created workspace really is. This
  // read `projects[0].id` and brought the whole app down on the first screen after sign-up.
  const project = projects.find((p) => p.id === picked) ?? projects[0] ?? NONE;
  return (
    <C.Provider value={{ projectId: project.id, setProjectId, project, all: projects }}>
      {children}
    </C.Provider>
  );
}

export function useProject() {
  const ctx = useContext(C);
  if (!ctx) throw new Error('useProject must be used inside <ProjectProvider>');
  return ctx;
}
