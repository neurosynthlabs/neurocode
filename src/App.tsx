import { lazy, Suspense, useEffect, useState, type ReactNode } from 'react';
import { Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { ShieldAlert } from 'lucide-react';
import { TooltipProvider } from '@/components/ui/tooltip';
import { Toaster } from '@/components/ui/sonner';
import { Sheet, SheetContent, SheetTitle } from '@/components/ui/sheet';
import { ThemeProvider, useTheme } from '@/lib/theme';
import { AuthProvider, useAuth } from '@/lib/auth';
import { permissionLabel, useAccess } from '@/lib/access';
import { ProjectProvider, useProject } from '@/lib/project-context';
import { DataProvider, useData } from '@/lib/data';
import { NAV } from '@/lib/nav';
import { useNotifier } from '@/lib/notify';
import { cn } from '@/lib/utils';
import { Sidebar } from '@/components/layout/Sidebar';
import { Topbar } from '@/components/layout/Topbar';
import { CommandPalette } from '@/components/layout/CommandPalette';
import { PageSkeleton, RouteBoundary } from '@/components/layout/RouteBoundary';
import { NotConnected } from '@/components/layout/NotConnected';
import { Empty, Page, PageBody } from '@/components/os';
import { LogoSymbol } from '@/components/os/Logo';

// The home screen ships in the entry chunk so first paint never waits on a second request.
// Every other screen is its own chunk, fetched the first time it is opened.
import CommandCenter from '@/pages/CommandCenter';

const Login = lazy(() => import('@/pages/Login'));
const Setup = lazy(() => import('@/pages/Setup'));
const Projects = lazy(() => import('@/pages/Projects'));
const ProjectOverview = lazy(() => import('@/pages/ProjectOverview'));
const Memory = lazy(() => import('@/pages/Memory'));
const Knowledge = lazy(() => import('@/pages/Knowledge'));
const CodeIntelligence = lazy(() => import('@/pages/CodeIntelligence'));
const Architecture = lazy(() => import('@/pages/Architecture'));
const Agents = lazy(() => import('@/pages/Agents'));
const Tasks = lazy(() => import('@/pages/Tasks'));
const Plans = lazy(() => import('@/pages/Plans'));
const Blueprints = lazy(() => import('@/pages/Blueprints'));
const Runs = lazy(() => import('@/pages/Runs'));
const Workflows = lazy(() => import('@/pages/Workflows'));
const Routines = lazy(() => import('@/pages/Routines'));
const Testing = lazy(() => import('@/pages/Testing'));
const Review = lazy(() => import('@/pages/Review'));
const Git = lazy(() => import('@/pages/Git'));
const Workbench = lazy(() => import('@/pages/Workbench'));
const DevOps = lazy(() => import('@/pages/DevOps'));
const Skills = lazy(() => import('@/pages/Skills'));
const Commands = lazy(() => import('@/pages/Commands'));
const Hooks = lazy(() => import('@/pages/Hooks'));
const Plugins = lazy(() => import('@/pages/Plugins'));
const Mcp = lazy(() => import('@/pages/Mcp'));
const Models = lazy(() => import('@/pages/Models'));
const Brainstorm = lazy(() => import('@/pages/Brainstorm'));
const Research = lazy(() => import('@/pages/Research'));
const ActivityPage = lazy(() => import('@/pages/Activity'));
const Sessions = lazy(() => import('@/pages/Sessions'));
const Permissions = lazy(() => import('@/pages/Permissions'));
const Cost = lazy(() => import('@/pages/Cost'));
const Evals = lazy(() => import('@/pages/Evals'));
const Settings = lazy(() => import('@/pages/Settings'));
const People = lazy(() => import('@/pages/admin/People'));
const Roles = lazy(() => import('@/pages/admin/Roles'));
const Teams = lazy(() => import('@/pages/admin/Teams'));
const Audit = lazy(() => import('@/pages/admin/Audit'));
const WorkspacePage = lazy(() => import('@/pages/admin/Workspace'));
const DatabasePage = lazy(() => import('@/pages/admin/Database'));

/**
 * A screen that needs a permission opens only for roles that hold it. The API checks again
 * regardless — this is the sign, not the fence.
 *
 * Every screen NAV gives a `perm` is wrapped, not only the admin ones: one declaration (NAV), one
 * enforcer (Guard), one fence (the API). A screen that carried its own check as well was the same
 * rule written twice, and the two drifted.
 *
 * The refusal names where you are, what is missing in words rather than an id, and both places it
 * can come from — a right is granted on a role, and a role is given to a person, and most people
 * meeting this sentence do not yet know that.
 */
function Guard({ to, children }: { to: string; children: ReactNode }) {
  const { canAny, roleNames } = useAuth();
  const { catalogue } = useAccess();
  const item = NAV.find((n) => n.to === to);
  const perm = item?.perm;
  if (!perm || canAny(...perm)) return children;
  const where = [item?.section, item?.sub].filter(Boolean).join(' → ');
  return (
    <Page>
      <PageBody>
        <Empty
          icon={<ShieldAlert className="size-6" />}
          title={item ? `${item.label} is part of ${where}` : 'This screen needs a permission'}
          hint={`${roleNames || 'Your role'} does not include “${permissionLabel(catalogue, perm[0])}”. An Owner or Admin can grant it in Admin → Roles.`}
        />
      </PageBody>
    </Page>
  );
}

function Shell() {
  const [collapsed, setCollapsed] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [navOpen, setNavOpen] = useState(false);
  const { projectId, setProjectId } = useProject();
  const { mode, offlineReason, reconnect } = useData();
  const { rail } = useTheme();
  const loc = useLocation();
  // "Notify me" (Command Center): watches the stream for what needs you or finished, while you are away.
  useNotifier();

  // Picking a screen from the drawer should put you on it, not leave the menu over it.
  useEffect(() => { setNavOpen(false); }, [loc.pathname]);

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
      <div className="hidden h-full lg:flex">
        <Sidebar collapsed={collapsed} onToggle={() => setCollapsed((v) => !v)} onSearch={() => setSearchOpen(true)} />
      </div>
      <Sheet open={navOpen} onOpenChange={setNavOpen}>
        <SheetContent side="left" showCloseButton={false} className="gap-0 p-0 data-[side=left]:w-[288px] data-[side=left]:max-w-[86vw] data-[side=left]:sm:max-w-[288px]">
          <SheetTitle className="sr-only">Navigation</SheetTitle>
          <Sidebar variant="drawer" collapsed={false} onToggle={() => setNavOpen(false)} onSearch={() => { setNavOpen(false); setSearchOpen(true); }} />
        </SheetContent>
      </Sheet>
      <div className={cn('flex min-w-0 flex-1 flex-col', rail === 'flush' ? '' : 'gap-1.5 p-1.5 sm:gap-2 sm:p-2')}>
        <Topbar onOpenNav={() => setNavOpen(true)} onOpenSearch={() => setSearchOpen(true)} projectId={projectId} onProject={setProjectId} />
        <main
          className={cn('min-h-0 flex-1 overflow-hidden bg-bg', rail === 'flush' ? '' : 'elevated border border-line')}
          style={rail === 'flush' ? undefined : { borderRadius: 'calc(var(--radius) * 1.6)' }}
        >
          {/* The workspace stopped loading after sign-in: one honest panel instead of thirty empty screens. */}
          {mode === 'offline' && <NotConnected variant="panel" reason={offlineReason} onRetry={reconnect} />}
          {/* Until the first load settles every screen would read as empty, which is not yet true. */}
          {mode === 'connecting' && <PageSkeleton />}
          {/* Keyed by path: a crash stays on its own screen and clears the moment you navigate. */}
          {mode === 'live' && (
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
                  <Route path="/blueprints" element={<Blueprints />} />
                  <Route path="/blueprints/:blueprintId" element={<Blueprints />} />
                  <Route path="/plans" element={<Plans />} />
                  <Route path="/runs" element={<Runs />} />
                  <Route path="/workflows" element={<Workflows />} />
                  <Route path="/routines" element={<Routines />} />
                  <Route path="/testing" element={<Testing />} />
                  <Route path="/review" element={<Review />} />
                  <Route path="/git" element={<Git />} />
                  <Route path="/workbench" element={<Guard to="/workbench"><Workbench /></Guard>} />
                  <Route path="/devops" element={<Guard to="/devops"><DevOps /></Guard>} />
                  <Route path="/skills" element={<Skills />} />
                  <Route path="/commands" element={<Commands />} />
                  <Route path="/hooks" element={<Guard to="/hooks"><Hooks /></Guard>} />
                  <Route path="/plugins" element={<Plugins />} />
                  <Route path="/mcp" element={<Mcp />} />
                  <Route path="/models" element={<Models />} />
                  <Route path="/brainstorm" element={<Brainstorm />} />
                  <Route path="/research" element={<Research />} />
                  <Route path="/activity" element={<ActivityPage />} />
                  <Route path="/sessions" element={<Sessions />} />
                  <Route path="/permissions" element={<Permissions />} />
                  <Route path="/cost" element={<Cost />} />
                  <Route path="/evals" element={<Evals />} />
                  <Route path="/settings" element={<Settings />} />
                  <Route path="/admin/users" element={<Guard to="/admin/users"><People /></Guard>} />
                  <Route path="/admin/roles" element={<Guard to="/admin/roles"><Roles /></Guard>} />
                  <Route path="/admin/teams" element={<Guard to="/admin/teams"><Teams /></Guard>} />
                  {/* Keys moved into Models; old links still land there. */}
                  <Route path="/admin/ai" element={<Navigate to="/models?tab=keys" replace />} />
                  <Route path="/admin/audit" element={<Guard to="/admin/audit"><Audit /></Guard>} />
                  <Route path="/admin/workspace" element={<Guard to="/admin/workspace"><WorkspacePage /></Guard>} />
                  <Route path="/admin/database" element={<Guard to="/admin/database"><DatabasePage /></Guard>} />
                  <Route path="*" element={<CommandCenter />} />
                </Routes>
              </Suspense>
            </RouteBoundary>
          )}
        </main>
      </div>
      {/* The palette is an overlay — if it ever throws, drop it rather than take the app down. */}
      <RouteBoundary fallback={null}>
        <CommandPalette open={searchOpen} onOpenChange={setSearchOpen} />
      </RouteBoundary>
    </div>
  );
}

function Splash() {
  return (
    <div className="grid h-full w-full place-items-center bg-bg">
      <div className="flex flex-col items-center gap-3">
        <LogoSymbol size={40} />
        <span className="text-[13px] text-dim">Opening your workspace…</span>
      </div>
    </div>
  );
}

/** A full-screen page outside the shell: signing in, or setting the workspace up. */
function Standalone({ children }: { children: ReactNode }) {
  return (
    <RouteBoundary>
      <Suspense fallback={<Splash />}>{children}</Suspense>
    </RouteBoundary>
  );
}

/** Nothing of the workspace renders until the server says who is here. */
function Gate() {
  const { state, user, offlineReason, refresh } = useAuth();
  if (state === 'loading') return <Splash />;
  if (state === 'offline') return <NotConnected reason={offlineReason} onRetry={refresh} />;
  if (state === 'setup') return <Standalone><Setup /></Standalone>;
  if (state === 'signed-out') return <Standalone><Login /></Standalone>;
  return (
    // Keyed by the person, so signing in as someone else starts from a clean store.
    <DataProvider key={user?.id}>
      <ProjectProvider>
        <Routes>
          <Route path="/login" element={<Navigate to="/" replace />} />
          <Route path="/setup" element={<Navigate to="/" replace />} />
          <Route path="*" element={<Shell />} />
        </Routes>
      </ProjectProvider>
    </DataProvider>
  );
}

export default function App() {
  return (
    <ThemeProvider>
      <AuthProvider>
        <TooltipProvider>
          <Gate />
          <Toaster position="bottom-right" />
        </TooltipProvider>
      </AuthProvider>
    </ThemeProvider>
  );
}
