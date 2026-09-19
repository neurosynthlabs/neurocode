import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Bell, Check, ChevronDown, Menu, Palette, Search, ShieldAlert } from 'lucide-react';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { BlockBar, Dot } from '@/components/os';
import { cn } from '@/lib/utils';
import { useTheme } from '@/lib/theme';
import { useProject } from '@/lib/project-context';
import { useData } from '@/lib/data';
import { ThemeCustomizer } from './ThemeCustomizer';
import { MODES } from './theme-modes';

const ROUND = { borderRadius: 'calc(var(--radius) * 1.2)' };
const BTN = 'press flex h-9 items-center gap-2 px-2.5 text-[13px] text-soft transition-colors hover:bg-surface-2 hover:text-ink';

/** A macOS-style toolbar: the project on the left, a few quiet controls on the right. */
export function Topbar({
  onOpenSearch, onOpenNav, projectId, onProject,
}: { onOpenSearch: () => void; onOpenNav?: () => void; projectId: string | null; onProject: (id: string) => void }) {
  const { current, rail, availableColors } = useTheme();
  const [themeOpen, setThemeOpen] = useState(false);
  const [projOpen, setProjOpen] = useState(false);
  const nav = useNavigate();
  const { all: projects, project } = useProject();
  const { approvals } = useData();
  // Null while nothing is onboarded, which is how every workspace starts: the button then says so.
  const active = projects.find((p) => p.id === projectId) ?? project;
  const pending = approvals.filter((a) => a.status === 'pending');

  return (
    <header
      className={cn(
        'flex h-14 shrink-0 items-center gap-1 px-2 sm:px-2.5',
        rail === 'flush' ? 'border-b border-line bg-bg' : 'elevated border border-line/70 bg-surface',
      )}
      style={rail === 'flush' ? undefined : { borderRadius: 'calc(var(--radius) * 1.6)' }}
    >
      {onOpenNav && (
        <button onClick={onOpenNav} aria-label="Open navigation" className={cn(BTN, 'lg:hidden')} style={ROUND}>
          <Menu className="size-[18px]" />
        </button>
      )}

      {/* Project — a popup button, or the way to a first one */}
      {!active && (
        <button onClick={() => nav('/projects')} className={cn(BTN, 'min-w-0')} style={ROUND} title="Onboard a repository">
          <span className="truncate text-[14px] font-semibold text-ink">No project yet</span>
          <span className="hidden truncate text-dim sm:inline">· Onboard a repository</span>
        </button>
      )}
      {active && (
        <Popover open={projOpen} onOpenChange={setProjOpen}>
          <PopoverTrigger className={cn(BTN, 'min-w-0 text-ink')} style={ROUND}>
            <span className="grid size-6 shrink-0 place-items-center rounded-md bg-brand/15 text-[10.5px] font-bold text-brand">
              {active.name.slice(0, 2).toUpperCase()}
            </span>
            <span className="max-w-[120px] truncate text-[14px] font-semibold sm:max-w-[220px]">{active.name}</span>
            <Dot state={active.status} pulse={active.status === 'active'} />
            <ChevronDown className="size-3.5 text-dim" />
          </PopoverTrigger>
          <PopoverContent align="start" className="w-[360px] p-1.5">
            <div className="px-2.5 pt-1.5 pb-2 text-[12px] font-medium text-dim">Projects · context never leaks between them</div>
            <div className="space-y-0.5">
              {projects.map((p) => (
                <button
                  key={p.id}
                  onClick={() => { onProject(p.id); setProjOpen(false); }}
                  className={cn('flex w-full items-start gap-3 px-2.5 py-2.5 text-left transition-colors hover:bg-surface-2',
                    p.id === projectId && 'bg-surface-2')}
                  style={{ borderRadius: 'var(--radius)' }}
                >
                  <Dot state={p.status} className="mt-1.5" />
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-2">
                      <span className="truncate text-[14px] font-medium text-ink">{p.name}</span>
                      {p.id === projectId && <Check className="size-3.5 text-brand" />}
                    </span>
                    <span className="mt-0.5 block truncate text-[12.5px] text-dim">{p.stack.slice(0, 3).join(' · ') || p.status}</span>
                    <span className="mt-1.5 flex items-center gap-2">
                      {p.understoodPct === null
                        ? <span className="text-[12px] text-dim">Not indexed yet</span>
                        : (
                          <>
                            <BlockBar pct={p.understoodPct} width={12} />
                            <span className="tnum text-[12px] text-dim">{p.understoodPct}% of its files indexed</span>
                          </>
                        )}
                    </span>
                  </span>
                </button>
              ))}
            </div>
            <div className="mt-1 border-t border-line/60 pt-1">
              <button
                onClick={() => { nav('/projects'); setProjOpen(false); }}
                className="w-full px-2.5 py-2 text-left text-[13px] text-soft transition-colors hover:bg-surface-2 hover:text-ink"
                style={{ borderRadius: 'var(--radius)' }}
              >
                Manage all projects →
              </button>
            </div>
          </PopoverContent>
        </Popover>
      )}

      <div className="flex-1" />

      {/* The sidebar holds search on a desktop; below that, it lives here. */}
      <button onClick={onOpenSearch} aria-label="Search" className={cn(BTN, 'lg:hidden')} style={ROUND}>
        <Search className="size-[18px]" />
      </button>

      <button
        onClick={() => nav('/permissions')}
        title={`${pending.length} approvals waiting on you`}
        aria-label={`${pending.length} approvals waiting on you`}
        className={cn(BTN, pending.length && 'bg-warn/10 text-warn hover:bg-warn/15 hover:text-warn')}
        style={ROUND}
      >
        <ShieldAlert className="size-[18px]" />
        {pending.length > 0 && <span className="tnum font-semibold">{pending.length}</span>}
        <span className="hidden md:inline">{pending.length ? 'to approve' : 'Approvals'}</span>
      </button>

      <button onClick={() => nav('/activity')} aria-label="Activity" title="Activity" className={cn(BTN, 'hidden sm:flex')} style={ROUND}>
        <Bell className="size-[18px]" />
      </button>

      <button
        onClick={() => setThemeOpen(true)}
        className={BTN}
        style={ROUND}
        title={`Appearance — ${availableColors.length} palettes, ${MODES.length} modes, radius and type`}
      >
        <Palette className="size-[18px]" />
        <span className="hidden xl:inline">{current.name}</span>
        <span className="hidden overflow-hidden rounded-full ring-1 ring-line sm:flex">
          {current.swatch.map((c, i) => <span key={i} className="size-2.5" style={{ background: c }} />)}
        </span>
      </button>
      <ThemeCustomizer open={themeOpen} onOpenChange={setThemeOpen} />
    </header>
  );
}
