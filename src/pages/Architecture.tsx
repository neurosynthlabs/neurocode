import { useProject } from '@/lib/project-context';
import { LiveGraph } from './code/LiveGraph';
import { NoProject } from './code/shared';

/** The active project's modules and the tables they touch, as the code index measured them. */
export default function Architecture() {
  const { project } = useProject();
  if (!project) return <NoProject title="Architecture" hint="Onboard a repository, and its modules and the tables they touch appear here." />;
  return <LiveGraph project={project} />;
}
