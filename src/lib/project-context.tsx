import { createContext, useCallback, useContext, useState, type ReactNode } from 'react';
import { useData } from '@/lib/data';
import type { Project } from '@/types';

interface Ctx {
  /** The active project's id; null while the workspace holds no project. */
  projectId: string | null;
  setProjectId: (id: string) => void;
  /** The active project; null while the workspace holds no project, which is how every workspace starts. */
  project: Project | null;
  all: Project[];
}
const C = createContext<Ctx | null>(null);

/* The pick is remembered per browser rather than as a workspace pref: prefs are shared by everyone in
   the workspace and need settings:write, so one person switching project would move everybody else. */
const KEY = 'nc.project';

function remembered(): string | null {
  try {
    return localStorage.getItem(KEY);
  } catch (e) {
    console.warn('[NeuroCode] the active project could not be read from this browser:', e);
    return null;
  }
}

/** The active project. The list comes from the data store, so an onboarded project can be switched to at once. */
export function ProjectProvider({ children }: { children: ReactNode }) {
  const { projects } = useData();
  const [picked, setPicked] = useState(remembered);
  const setProjectId = useCallback((id: string) => {
    setPicked(id);
    try {
      localStorage.setItem(KEY, id);
    } catch (e) {
      console.warn('[NeuroCode] the active project could not be remembered in this browser:', e);
    }
  }, []);
  // A picked project that is gone (emptied, or never in this workspace) falls back to the first one,
  // and to none at all when there are none. Screens check for null rather than read a stand-in.
  const project = projects.find((p) => p.id === picked) ?? projects[0] ?? null;
  return (
    <C.Provider value={{ projectId: project?.id ?? null, setProjectId, project, all: projects }}>
      {children}
    </C.Provider>
  );
}

export function useProject() {
  const ctx = useContext(C);
  if (!ctx) throw new Error('useProject must be used inside <ProjectProvider>');
  return ctx;
}
