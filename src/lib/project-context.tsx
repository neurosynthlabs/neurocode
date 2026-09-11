import { createContext, useContext, useState, type ReactNode } from 'react';
import { activeProjectId } from '@/mock/projects';
import { useData } from '@/lib/data';
import type { Project } from '@/types';

interface Ctx { projectId: string; setProjectId: (id: string) => void; project: Project; all: Project[] }
const C = createContext<Ctx | null>(null);

/** The active project. The list comes from the data store, so an onboarded project can be switched to. */
export function ProjectProvider({ children }: { children: ReactNode }) {
  const { projects } = useData();
  const [picked, setProjectId] = useState(activeProjectId);
  // If the picked project disappears (a reset, say), fall back to the first one everywhere at once.
  const project = projects.find((p) => p.id === picked) ?? projects[0];
  return <C.Provider value={{ projectId: project.id, setProjectId, project, all: projects }}>{children}</C.Provider>;
}

export function useProject() {
  const ctx = useContext(C);
  if (!ctx) throw new Error('useProject must be used inside <ProjectProvider>');
  return ctx;
}
