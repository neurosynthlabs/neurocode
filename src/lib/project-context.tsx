import { createContext, useContext, useState, type ReactNode } from 'react';
import { projects, activeProjectId } from '@/mock/projects';
import type { Project } from '@/types';

interface Ctx { projectId: string; setProjectId: (id: string) => void; project: Project; all: Project[] }
const C = createContext<Ctx | null>(null);

export function ProjectProvider({ children }: { children: ReactNode }) {
  const [projectId, setProjectId] = useState(activeProjectId);
  const project = projects.find((p) => p.id === projectId) ?? projects[0];
  return <C.Provider value={{ projectId, setProjectId, project, all: projects }}>{children}</C.Provider>;
}

export function useProject() {
  const ctx = useContext(C);
  if (!ctx) throw new Error('useProject must be used inside <ProjectProvider>');
  return ctx;
}
