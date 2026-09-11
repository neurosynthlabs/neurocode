import { lazy, Suspense, useEffect, useState } from 'react';
import { Route, Routes, useLocation } from 'react-router-dom';
import { TooltipProvider } from '@/components/ui/tooltip';
import { Toaster } from '@/components/ui/sonner';
import { ThemeProvider, useTheme } from '@/lib/theme';
import { ProjectProvider, useProject } from '@/lib/project-context';
import { cn } from '@/lib/utils';
import { Sidebar } from '@/components/layout/Sidebar';
import { Topbar } from '@/components/layout/Topbar';
import { CommandPalette } from '@/components/layout/CommandPalette';
import { PageSkeleton, RouteBoundary } from '@/components/layout/RouteBoundary';

// The home screen ships in the entry chunk so first paint never waits on a second request.
// Every other screen is its own chunk, fetched the first time it is opened.
import CommandCenter from '@/pages/CommandCenter';

const Login = lazy(() => import('@/pages/Login'));
const Projects = lazy(() => import('@/pages/Projects'));
const ProjectOverview = lazy(() => import('@/pages/ProjectOverview'));
const Memory = lazy(() => import('@/pages/Memory'));
const Knowledge = lazy(() => import('@/pages/Knowledge'));
const CodeIntelligence = lazy(() => import('@/pages/CodeIntelligence'));
const Architecture = lazy(() => import('@/pages/Architecture'));
const Agents = lazy(() => import('@/pages/Agents'));
const Tasks = lazy(() => import('@/pages/Tasks'));
const Plans = lazy(() => import('@/pages/Plans'));
const Runs = lazy(() => import('@/pages/Runs'));
const Workflows = lazy(() => import('@/pages/Workflows'));
const Testing = lazy(() => import('@/pages/Testing'));
const Review = lazy(() => import('@/pages/Review'));
const Git = lazy(() => import('@/pages/Git'));
const DevOps = lazy(() => import('@/pages/DevOps'));
const Skills = lazy(() => import('@/pages/Skills'));
const Commands = lazy(() => import('@/pages/Commands'));
const Hooks = lazy(() => import('@/pages/Hooks'));
const Plugins = lazy(() => import('@/pages/Plugins'));
const Mcp = lazy(() => import('@/pages/Mcp'));
const Acp = lazy(() => import('@/pages/Acp'));
const Models = lazy(() => import('@/pages/Models'));
const Brainstorm = lazy(() => import('@/pages/Brainstorm'));
const Research = lazy(() => import('@/pages/Research'));
const ActivityPage = lazy(() => import('@/pages/Activity'));
const Sessions = lazy(() => import('@/pages/Sessions'));
const Permissions = lazy(() => import('@/pages/Permissions'));
const Cost = lazy(() => import('@/pages/Cost'));
const Evals = lazy(() => import('@/pages/Evals'));
const Settings = lazy(() => import('@/pages/Settings'));

function Shell() {
  const [collapsed, setCollapsed] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const { projectId, setProjectId } = useProject();
  const { rail } = useTheme();
  const loc = useLocation();

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
          {/* Keyed by path: a crash stays on its own screen and clears the moment you navigate. */}
          <RouteBoundary key={loc.pathname}>
            <Suspense fallback={<PageSkeleton />}>
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
            </Suspense>
          </RouteBoundary>
        </main>
      </div>
      {/* The palette is an overlay — if it ever throws, drop it rather than take the app down. */}
      <RouteBoundary fallback={null}>
        <CommandPalette open={searchOpen} onOpenChange={setSearchOpen} />
      </RouteBoundary>
    </div>
  );
}

export default function App() {
  return (
    <ThemeProvider>
      <ProjectProvider>
        <TooltipProvider>
          <Routes>
            <Route
              path="/login"
              element={
                <RouteBoundary>
                  <Suspense fallback={<PageSkeleton />}><Login /></Suspense>
                </RouteBoundary>
              }
            />
            <Route path="*" element={<Shell />} />
          </Routes>
          <Toaster position="bottom-right" />
        </TooltipProvider>
      </ProjectProvider>
    </ThemeProvider>
  );
}
