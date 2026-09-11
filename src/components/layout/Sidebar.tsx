import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import { createPortal } from 'react-dom';
import { Activity, ChevronDown, Circle, GripVertical, PanelLeftOpen, Search, X } from 'lucide-react';
import { ICONS } from '@/lib/icons';
import { NAV, NAV_GROUPS } from '@/lib/nav';
import { cn } from '@/lib/utils';
import { LogoMark, Wordmark } from '@/components/os/Logo';
import { useTheme } from '@/lib/theme';

const MIN_W = 196;
const MAX_W = 340;
const DEF_W = 224;
const COLLAPSED_W = 56;
const WKEY = 'aios.sidebar.width';
const GKEY = 'aios.sidebar.groups';

type IconProps = { className?: string; style?: React.CSSProperties; strokeWidth?: number };

function Icon({ name, className, style, strokeWidth = 1.75 }: IconProps & { name: string }) {
  const C = ICONS[name] ?? Circle;
  return <C className={className} style={style} strokeWidth={strokeWidth} />;
}

const GROUP_ICON: Record<string, string> = {
  Workspace: 'LayoutDashboard',
  Intelligence: 'Brain',
  Execution: 'Cpu',
  Platform: 'Blocks',
  Thinking: 'Lightbulb',
  Governance: 'ShieldCheck',
};

export function Sidebar({ collapsed, onToggle, variant = 'rail' }: {
  collapsed: boolean;
  onToggle: () => void;
  /** 'drawer' renders inside the mobile sheet: full width, no float, no resize, no collapse. */
  variant?: 'rail' | 'drawer';
}) {
  const drawer = variant === 'drawer';
  const loc = useLocation();
  const { rail } = useTheme();
  const ref = useRef<HTMLElement>(null);
  const [q, setQ] = useState('');
  const [resizing, setResizing] = useState(false);
  const [width, setWidth] = useState(() => {
    try { return Number(localStorage.getItem(WKEY)) || DEF_W; } catch { return DEF_W; }
  });
  const [closed, setClosed] = useState<Set<string>>(() => {
    try { return new Set(JSON.parse(localStorage.getItem(GKEY) ?? '[]') as string[]); } catch { return new Set(); }
  });

  useEffect(() => { try { localStorage.setItem(WKEY, String(width)); } catch { /* private mode */ } }, [width]);
  useEffect(() => { try { localStorage.setItem(GKEY, JSON.stringify([...closed])); } catch { /* private mode */ } }, [closed]);

  /* ── drag to resize ─────────────────────────────────────────── */
  const onResize = useCallback((e: MouseEvent) => {
    if (!ref.current) return;
    const w = e.clientX - ref.current.getBoundingClientRect().left;
    if (w >= MIN_W && w <= MAX_W) setWidth(w);
  }, []);
  const stop = useCallback(() => setResizing(false), []);
  useEffect(() => {
    if (!resizing) return;
    window.addEventListener('mousemove', onResize);
    window.addEventListener('mouseup', stop);
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    return () => {
      window.removeEventListener('mousemove', onResize);
      window.removeEventListener('mouseup', stop);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
  }, [resizing, onResize, stop]);

  const groups = useMemo(() => {
    const s = q.trim().toLowerCase();
    return NAV_GROUPS.map((g) => ({
      group: g,
      items: NAV.filter((n) => n.group === g && (!s || (n.label + ' ' + n.keywords).toLowerCase().includes(s))),
    })).filter((g) => g.items.length);
  }, [q]);

  const toggleGroup = (g: string) =>
    setClosed((c) => {
      const n = new Set(c);
      n.has(g) ? n.delete(g) : n.add(g);
      return n;
    });

  return (
    <div
      className={cn('h-full shrink-0', drawer ? 'w-full' : [rail !== 'flush' && 'p-2 pr-0', !resizing && 'transition-[width] duration-300 ease-out'])}
      style={drawer ? undefined : { width: (collapsed ? COLLAPSED_W : width) + (rail === 'flush' ? 0 : 8) }}
    >
      <aside
        ref={ref}
        className={cn(
          'rail-surface relative flex h-full flex-col', !drawer && rail !== 'flush' && 'rail-slab',
          collapsed ? 'overflow-visible' : 'overflow-hidden',
          resizing ? '' : 'transition-[width] duration-300 ease-out',
        )}
        style={{
          width: drawer ? '100%' : collapsed ? COLLAPSED_W : width,
          borderRadius: drawer || rail === 'flush' ? 0 : 'calc(var(--radius) * 2)',
          borderRight: !drawer && rail === 'flush' ? '1px solid var(--rail-line)' : undefined,
        }}
      >
        <div className="rail-edge pointer-events-none absolute inset-y-0 left-0 w-px" style={{ borderRadius: 'inherit' }} />

        {/* Resize handle */}
        {!collapsed && !drawer && (
          <div
            onMouseDown={(e) => { e.preventDefault(); setResizing(true); }}
            className="group/rz absolute inset-y-0 right-0 z-10 w-1 cursor-col-resize"
            style={{ background: resizing ? 'var(--rail-accent)' : undefined }}
          >
            <GripVertical
              className="absolute top-1/2 right-0 size-4 -translate-y-1/2 opacity-0 transition-opacity group-hover/rz:opacity-60"
              style={{ color: 'var(--rail-dim)' }}
            />
          </div>
        )}

        {/* Brand — click to collapse */}
        <button
          onClick={onToggle}
          className="group/logo flex shrink-0 items-center gap-2.5 px-3 py-3 select-none"
          style={{ borderBottom: '1px solid var(--sb-border)' }}
        >
          {collapsed
            ? <span className="transition-transform duration-200 group-hover/logo:scale-105"><LogoMark size={30} /></span>
            : <Wordmark size={34} onDark className="text-left transition-transform duration-200 group-hover/logo:scale-[1.02]" />}
        </button>

        {/* Nav */}
        <nav className="sb-scroll min-h-0 flex-1 space-y-0.5 overflow-y-auto px-2 py-2.5">
          {groups.map(({ group, items }) => {
            const isOpen = !closed.has(group) || !!q;
            const groupActive = items.some((i) => i.to === loc.pathname);
            return (
              <div key={group}>
                {!collapsed ? (
                  <button
                    onClick={() => toggleGroup(group)}
                    className="group/sec flex w-full items-center justify-between px-2 pt-3.5 pb-2 first:pt-1.5"
                    style={{ color: 'var(--rail-soft)', borderBottom: '1px solid var(--rail-line)' }}
                  >
                    <span className="flex items-center gap-1.5 text-[10.5px] font-bold tracking-[0.1em] uppercase">
                      <Icon name={GROUP_ICON[group] ?? 'Circle'} className="size-3.5" strokeWidth={2.1} />
                      {group}
                    </span>
                    <span className="flex items-center gap-1">
                      {groupActive && !isOpen && <span className="size-1 rounded-full" style={{ background: 'var(--rail-accent)' }} />}
                      <ChevronDown strokeWidth={2.4} className={cn('size-3.5 opacity-0 transition-transform group-hover/sec:opacity-60', !isOpen && '-rotate-90')} />
                    </span>
                  </button>
                ) : (
                  <div className="mx-2 my-2 h-px" style={{ background: 'var(--rail-line)' }} />
                )}

                <div className={cn('grid transition-[grid-template-rows,opacity] duration-200 ease-out',
                  isOpen || collapsed ? 'grid-rows-[1fr] opacity-100' : 'grid-rows-[0fr] opacity-0')}>
                  <div className="min-h-0 space-y-0.5 overflow-hidden">
                    {items.map((item) => (
                      <Item key={item.to} to={item.to} label={item.label} icon={item.icon} collapsed={collapsed} />
                    ))}
                  </div>
                </div>
              </div>
            );
          })}
          {groups.length === 0 && (
            <p className="px-3 py-6 text-center text-[11.5px]" style={{ color: 'var(--rail-dim)' }}>
              Nothing named “{q}”.
            </p>
          )}
        </nav>

        {/* Footer */}
        <div className="shrink-0 pb-2.5" style={{ borderTop: '1px solid var(--rail-line)' }}>
          {!collapsed ? (
            <>
              <div className="px-2.5 pt-2.5 pb-1.5">
                <div
                  className="flex h-7 items-center gap-2 px-2.5"
                  style={{
                    borderRadius: 'calc(var(--radius) * 1.2)',
                    background: 'var(--rail-line)',
                    border: '1px solid var(--rail-line)',
                  }}
                >
                  <Search className="size-4 shrink-0" strokeWidth={1.9} style={{ color: 'var(--rail-ink)', opacity: 0.75 }} />
                  <input
                    value={q}
                    onChange={(e) => setQ(e.target.value)}
                    placeholder="Filter menu…"
                    className="min-w-0 flex-1 border-none bg-transparent text-[12px] outline-none placeholder:opacity-70"
                    style={{ color: 'var(--rail-ink)' }}
                  />
                  {q && (
                    <button onClick={() => setQ('')} style={{ color: 'var(--rail-ink)', opacity: 0.7 }}>
                      <X className="size-3.5" strokeWidth={2.2} />
                    </button>
                  )}
                </div>
              </div>
              <div className="px-2.5">
                <div
                  className="flex items-center gap-2 px-2 py-1.5"
                  style={{
                    borderRadius: 'calc(var(--radius) * 1.2)',
                    background: 'var(--rail-hover)',
                    border: '1px solid var(--rail-line)',
                  }}
                >
                  <span className="grid size-6 shrink-0 place-items-center rounded-full"
                    style={{ background: 'var(--rail-chip)', color: 'var(--rail-accent)' }}>
                    <Activity className="size-3.5" strokeWidth={2.1} />
                  </span>
                  <span className="min-w-0 flex-1 leading-tight">
                    <span className="block truncate text-[11.5px] font-semibold" style={{ color: 'var(--rail-ink)' }}>4 agents live</span>
                    <span className="block truncate text-[10px]" style={{ color: 'var(--rail-dim)' }}>71% served locally</span>
                  </span>
                </div>
              </div>
            </>
          ) : (
            <button onClick={onToggle} className="flex w-full justify-center py-2.5" style={{ color: 'var(--rail-ink)', opacity: 0.8 }}>
              <PanelLeftOpen className="size-4.5" strokeWidth={1.9} />
            </button>
          )}
        </div>
      </aside>
    </div>
  );
}

/* ── One nav row, with a portal flyout when the rail is collapsed ── */
function Item({ to, label, icon, collapsed }: { to: string; label: string; icon: string; collapsed: boolean }) {
  const [fly, setFly] = useState(false);
  const [pos, setPos] = useState({ top: 0, left: 0 });
  const ref = useRef<HTMLDivElement>(null);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);

  const open = () => {
    clearTimeout(timer.current);
    if (ref.current) {
      const r = ref.current.getBoundingClientRect();
      setPos({ top: r.top, left: r.right });
    }
    setFly(true);
  };
  const close = () => { timer.current = setTimeout(() => setFly(false), 120); };
  useEffect(() => () => clearTimeout(timer.current), []);

  const row = (
    <NavLink
      to={to}
      end={to === '/'}
      className={cn('group relative flex items-center gap-2.5 px-2.5 py-[7px] text-[13px] transition-colors',
        collapsed && 'justify-center px-0')}
      style={{ borderRadius: 'var(--radius)', color: 'var(--rail-ink)' }}
    >
      {({ isActive }) => (
        <>
          <span
            className="pointer-events-none absolute inset-0 transition-colors"
            style={{
              borderRadius: 'var(--radius)',
              background: isActive ? 'var(--rail-active)' : fly ? 'var(--rail-hover)' : 'transparent',
            }}
          />
          {isActive && (
            <span
              className="absolute top-1/2 left-0 -translate-y-1/2"
              style={{
                width: 2, height: '52%', borderRadius: '0 2px 2px 0',
                background: 'var(--rail-accent)',
              }}
            />
          )}
          <Icon
            name={icon}
            className="relative size-4 shrink-0 transition-colors"
            strokeWidth={isActive ? 2.15 : 1.75}
            style={{ color: isActive ? 'var(--rail-accent)' : 'var(--rail-soft)' }}
          />
          {!collapsed && (
            <span
              className={cn('relative min-w-0 flex-1 truncate tracking-[-0.005em] transition-colors',
                isActive ? 'font-semibold' : 'font-medium')}
              style={{ color: isActive ? 'var(--rail-ink)' : 'var(--rail-item)' }}
            >
              {label}
            </span>
          )}
        </>
      )}
    </NavLink>
  );

  if (!collapsed) return row;

  return (
    <>
      <div ref={ref} onMouseEnter={open} onMouseLeave={close}>{row}</div>
      {fly && createPortal(
        <div
          className="rail-flyout-enter fixed z-[9999] pl-2"
          style={{ top: pos.top - 4, left: pos.left }}
          onMouseEnter={open}
          onMouseLeave={close}
        >
          <div className="rail-flyout px-3 py-1.5" style={{ borderRadius: 'calc(var(--radius) * 1.2)' }}>
            <span className="text-[12px] font-semibold whitespace-nowrap" style={{ color: 'var(--rail-ink)' }}>{label}</span>
          </div>
        </div>,
        document.body,
      )}
    </>
  );
}
