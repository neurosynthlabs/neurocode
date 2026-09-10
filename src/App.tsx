import { useEffect, useState } from 'react';
import { Route, Routes } from 'react-router-dom';
import { TooltipProvider } from '@/components/ui/tooltip';
import { Toaster } from '@/components/ui/sonner';
import { ThemeProvider } from '@/lib/theme';
import { ProjectProvider, useProject } from '@/lib/project-context';
import { useTheme } from '@/lib/theme';
import { cn } from '@/lib/utils';
import { Sidebar } from '@/components/layout/Sidebar';
import { Topbar } from '@/components/layout/Topbar';
import { CommandPalette } from '@/components/layout/CommandPalette';

import Login from '@/pages/Login';
import CommandCenter from '@/pages/CommandCenter';
import Projects from '@/pages/Projects';
import ProjectOverview from '@/pages/ProjectOverview';
import Memory from '@/pages/Memory';
import Knowledge from '@/pages/Knowledge';
import CodeIntelligence from '@/pages/CodeIntelligence';
import Architecture from '@/pages/Architecture';
import Agents from '@/pages/Agents';
import Tasks from '@/pages/Tasks';
import Plans from '@/pages/Plans';
import Runs from '@/pages/Runs';
import Workflows from '@/pages/Workflows';
import Testing from '@/pages/Testing';
import Review from '@/pages/Review';
import Git from '@/pages/Git';
import DevOps from '@/pages/DevOps';
import Skills from '@/pages/Skills';
import Commands from '@/pages/Commands';
import Hooks from '@/pages/Hooks';
import Plugins from '@/pages/Plugins';
import Mcp from '@/pages/Mcp';
import Acp from '@/pages/Acp';
import Models from '@/pages/Models';
import Brainstorm from '@/pages/Brainstorm';
import Research from '@/pages/Research';
import ActivityPage from '@/pages/Activity';
import Sessions from '@/pages/Sessions';
import Permissions from '@/pages/Permissions';
import Cost from '@/pages/Cost';
import Evals from '@/pages/Evals';
import Settings from '@/pages/Settings';

function Shell() {
  const [collapsed, setCollapsed] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const { projectId, setProjectId } = useProject();
  const { rail } = useTheme();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setSearchOpen((v) => !v);
      }
      if ((e.metaKey || e.ctrlKey) && e.key === 'b') {
        e.preventDefault();
        setCollapsed((v) => !v);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  return (
    <div className="flex h-full w-full overflow-hidden bg-base">
      <Sidebar collapsed={collapsed} onToggle={() => setCollapsed((v) => !v)} />
      <div className={cn('flex min-w-0 flex-1 flex-col', rail === 'flush' ? '' : 'gap-2 p-2')}>
        <Topbar onOpenSearch={() => setSearchOpen(true)} projectId={projectId} onProject={setProjectId} />
        <main
          className={cn('min-h-0 flex-1 overflow-hidden bg-bg', rail === 'flush' ? '' : 'elevated border border-line')}
          style={rail === 'flush' ? undefined : { borderRadius: 'calc(var(--radius) * 1.6)' }}
        >
          <Routes>
            <Route path="/" element={<CommandCenter />} />
            <Route path="/projects" element={<Projects />} />
            <Route path="/projects/:projectId" element={<ProjectOverview />} />
            <Route path="/memory" element={<Memory />} />
            <Route path="/knowledge" element={<Knowledge />} />
            <Route path="/code" element={<CodeIntelligence />} />
            <Route path="/architecture" element={<Architecture />} />
            <Route path="/agents" element={<Agents />} />
            <Route path="/tasks" element={<Tasks />} />
            <Route path="/plans" element={<Plans />} />
            <Route path="/runs" element={<Runs />} />
            <Route path="/workflows" element={<Workflows />} />
            <Route path="/testing" element={<Testing />} />
            <Route path="/review" element={<Review />} />
            <Route path="/git" element={<Git />} />
            <Route path="/devops" element={<DevOps />} />
            <Route path="/skills" element={<Skills />} />
            <Route path="/commands" element={<Commands />} />
            <Route path="/hooks" element={<Hooks />} />
            <Route path="/plugins" element={<Plugins />} />
            <Route path="/mcp" element={<Mcp />} />
            <Route path="/acp" element={<Acp />} />
            <Route path="/models" element={<Models />} />
            <Route path="/brainstorm" element={<Brainstorm />} />
            <Route path="/research" element={<Research />} />
            <Route path="/activity" element={<ActivityPage />} />
            <Route path="/sessions" element={<Sessions />} />
            <Route path="/permissions" element={<Permissions />} />
            <Route path="/cost" element={<Cost />} />
            <Route path="/evals" element={<Evals />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="*" element={<CommandCenter />} />
          </Routes>
        </main>
      </div>
      <CommandPalette open={searchOpen} onOpenChange={setSearchOpen} />
    </div>
  );
}

export default function App() {
  return (
    <ThemeProvider>
      <ProjectProvider>
        <TooltipProvider>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route path="*" element={<Shell />} />
          </Routes>
          <Toaster position="bottom-right" />
        </TooltipProvider>
      </ProjectProvider>
    </ThemeProvider>
  );
}
