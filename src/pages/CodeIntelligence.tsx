import { useProject } from '@/lib/project-context';
import { LiveCode } from './code/LiveCode';
import { NoProject } from './code/shared';

/** The active project's code index: its files, symbols and dependencies, as the index measured them. */
export default function CodeIntelligence() {
  const { project } = useProject();
  if (!project) return <NoProject title="Code Intelligence" hint="Onboard a repository in Projects to read its files, symbols and dependencies." />;
  return <LiveCode project={project} />;
}
