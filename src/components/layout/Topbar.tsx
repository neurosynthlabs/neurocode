import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Search, ChevronDown, Check, Palette, Bell, ShieldAlert, Zap, Command, Menu } from 'lucide-react';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Kbd, Dot, Tag, BlockBar } from '@/components/os';
import { cn } from '@/lib/utils';
import { useTheme } from '@/lib/theme';
import { ThemeCustomizer } from './ThemeCustomizer';
import { projects } from '@/mock/projects';
import { useData } from '@/lib/data';

export function Topbar({
  onOpenSearch, onOpenNav, projectId, onProject,
}: { onOpenSearch: () => void; onOpenNav?: () => void; projectId: string; onProject: (id: string) => void }) {
  const { current, rail } = useTheme();
  const [themeOpen, setThemeOpen] = useState(false);
  const [projOpen, setProjOpen] = useState(false);
  const nav = useNavigate();
  const active = projects.find((p) => p.id === projectId) ?? projects[0];
  const { approvals, mode } = useData();
  const pending = approvals.filter((a) => a.status === 'pending');

  return (
    <header
      className={cn(
        'flex h-12 shrink-0 items-center gap-1.5 border border-line bg-surface px-2 sm:gap-2 sm:px-2.5',
        rail === 'flush' ? 'rounded-none border-x-0 border-t-0' : 'elevated',
      )}
      style={rail === 'flush' ? undefined : { borderRadius: 'calc(var(--radius) * 1.6)' }}
    >
      {onOpenNav && (
        <button
          onClick={onOpenNav}
          aria-label="Open navigation"
          className="press flex size-8 shrink-0 items-center justify-center border border-line bg-surface-2 text-soft transition-colors hover:text-ink lg:hidden"
          style={{ borderRadius: 'calc(var(--radius) * 1.2)' }}
        >
          <Menu className="size-4" />
        </button>
      )}

      {/* Project switcher */}
      <Popover open={projOpen} onOpenChange={setProjOpen}>
        <PopoverTrigger
          className="press flex h-8 items-center gap-2 border border-line bg-surface-2 px-2.5 text-[12px] text-ink transition-colors hover:border-line-strong hover:bg-surface-3"
          style={{ borderRadius: 'calc(var(--radius) * 1.2)' }}
        >
          <span className="grid size-5 shrink-0 place-items-center rounded-xs bg-brand/15 font-mono text-[9.5px] font-bold text-brand">
            {active.name.slice(0, 2).toUpperCase()}
          </span>
          <span className="max-w-[92px] truncate font-medium sm:max-w-[150px]">{active.name}</span>
          <Dot state={active.status} pulse={active.status === 'active'} />
          <ChevronDown className="size-3 text-dim" />
        </PopoverTrigger>
        <PopoverContent align="start" className="w-[340px] p-1.5">
          <div className="eyebrow px-2 py-1.5">Projects · context is fully isolated</div>
          <div className="space-y-0.5">
            {projects.map((p) => (
              <button
                key={p.id}
                onClick={() => { onProject(p.id); setProjOpen(false); }}
                className={cn('flex w-full items-start gap-2.5 px-2 py-2 text-left transition-colors hover:bg-surface-2',
                  p.id === projectId && 'bg-surface-2')}
                style={{ borderRadius: 'var(--radius)' }}
              >
                <Dot state={p.status} className="mt-1.5" />
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-2">
                    <span className="truncate text-[12.5px] font-medium text-ink">{p.name}</span>
                    {p.id === projectId && <Check className="size-3 text-brand" />}
                  </span>
                  <span className="mt-0.5 block truncate text-[11px] text-dim">{p.stack.slice(0, 3).join(' · ')}</span>
                  <span className="mt-1 flex items-center gap-2">
                    <BlockBar pct={p.understoodPct} width={10} />
                    <span className="tnum text-[10px] text-dim">{p.understoodPct}% understood</span>
                  </span>
                </span>
              </button>
            ))}
          </div>
          <div className="mt-1 border-t border-line pt-1">
            <button onClick={() => { nav('/projects'); setProjOpen(false); }}
              className="w-full rounded-sm px-2 py-1.5 text-left text-[12px] text-soft hover:bg-surface-2 hover:text-ink">
              Manage all projects →
            </button>
          </div>
        </PopoverContent>
      </Popover>

      {/* Global search */}
      <button
        onClick={onOpenSearch}
        className="press flex h-8 min-w-0 flex-1 items-center gap-2 border border-line bg-base px-3 text-left text-[12px] text-dim transition-colors hover:border-brand/40 hover:text-soft"
        style={{ borderRadius: 'calc(var(--radius) * 1.2)' }}
      >
        <Search className="size-3.5 shrink-0" />
        <span className="hidden truncate md:inline">Search code, memory, tasks, decisions, commits, tests…</span>
        <span className="ml-auto hidden shrink-0 items-center gap-0.5 md:flex"><Kbd><Command className="size-2.5" /></Kbd><Kbd>K</Kbd></span>
      </button>

      {/* Router health */}
      <div className="hidden h-8 items-center gap-1.5 border border-line bg-surface-2 px-2.5 xl:flex"
        style={{ borderRadius: 'calc(var(--radius) * 1.2)' }}>
        <Zap className="size-3 text-ok" />
        <span className="font-mono text-[10.5px] text-soft">local-first</span>
        <BlockBar pct={71} width={6} tone="ok" />
        <span className="tnum font-mono text-[10.5px] text-ok">71%</span>
      </div>

      {/* Approvals */}
      <button
        onClick={() => nav('/permissions')}
        className={cn('press relative flex h-8 items-center gap-1.5 border px-2.5 text-[12px] transition-colors',
          pending.length ? 'border-warn/40 bg-warn/10 text-warn hover:bg-warn/15' : 'border-line bg-surface-2 text-soft hover:text-ink')}
        style={{ borderRadius: 'calc(var(--radius) * 1.2)' }}
        title={`${pending.length} approvals waiting on you`}
        aria-label={`${pending.length} approvals waiting on you`}
      >
        <ShieldAlert className="size-3.5" />
        {pending.length > 0 && <span className="tnum text-[11px] font-semibold">{pending.length}</span>}
      </button>

      <button
        onClick={() => nav('/activity')}
        aria-label="Activity"
        title="Activity"
        className="press hidden h-8 items-center border border-line bg-surface-2 px-2.5 text-soft transition-colors hover:text-ink sm:flex"
        style={{ borderRadius: 'calc(var(--radius) * 1.2)' }}
      >
        <Bell className="size-3.5" />
      </button>

      {/* Appearance */}
      <button
        onClick={() => setThemeOpen(true)}
        className="press flex h-8 items-center gap-2 border border-line bg-surface-2 px-2.5 text-[12px] text-soft transition-colors hover:text-ink"
        style={{ borderRadius: 'calc(var(--radius) * 1.2)' }}
        title="Appearance — 248 palettes, 7 modes, radius and type"
      >
        <Palette className="size-3.5" />
        <span className="hidden lg:inline">{current.name}</span>
        <span className="hidden overflow-hidden rounded-xs border border-line-strong sm:flex">
          {current.swatch.map((c, i) => <span key={i} className="size-2.5" style={{ background: c }} />)}
        </span>
      </button>
      <ThemeCustomizer open={themeOpen} onOpenChange={setThemeOpen} />

      {/* Operator */}
      <div className="flex h-8 shrink-0 items-center gap-2 border border-line bg-surface-2 px-1 md:pr-2.5 md:pl-1.5"
        style={{ borderRadius: 'calc(var(--radius) * 1.2)' }}>
        <span className="grid size-6 place-items-center rounded-xs bg-brand text-brand-ink">
          <span className="font-mono text-[9.5px] font-bold">RR</span>
        </span>
        <span className="hidden leading-tight md:block">
          <span className="block text-[11.5px] font-medium text-ink">AI Project Manager</span>
          <span className="block text-[9.5px] text-dim">final approver</span>
        </span>
        <span
          className="hidden xl:inline-flex"
          title={mode === 'live'
            ? 'Connected to the local API: every change is saved to SQLite'
            : 'Demo data: changes last until you reload. npm run dev:start runs the local API.'}
        >
          <Tag tone={mode === 'live' ? 'ok' : 'neutral'}>
            <Dot state={mode === 'live' ? 'ok' : 'idle'} pulse={mode === 'live'} />
            {mode === 'live' ? 'saved locally' : mode === 'demo' ? 'demo data' : 'connecting'}
          </Tag>
        </span>
      </div>
    </header>
  );
}
