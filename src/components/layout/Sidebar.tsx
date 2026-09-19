import {
  useCallback, useEffect, useMemo, useRef, useState, type ComponentType, type CSSProperties, type ReactNode,
} from 'react';
import { Link, NavLink, useLocation, useNavigate } from 'react-router-dom';
import { createPortal } from 'react-dom';
import {
  ChevronRight, Circle, Cpu, FileText, GripVertical, Lightbulb, ListTodo, PanelLeftClose, PanelLeftOpen,
  Plug, Puzzle, Rocket, Search, ShieldCheck, SquarePen,
} from 'lucide-react';
import { ICONS } from '@/lib/icons';
import { NAV, NAV_SECTIONS, allowed, type NavItem, type NavSection } from '@/lib/nav';
import { useAuth } from '@/lib/auth';
import { AccountMenu } from './AccountMenu';
import { cn } from '@/lib/utils';
import { LogoMark, Wordmark } from '@/components/os/Logo';
import { useTheme } from '@/lib/theme';
import { inFlight, useData } from '@/lib/data';
import type { Catalogue, RunDoc } from '@/lib/api';
import { useAccess } from '@/lib/access';

/* ═══════════════════════════════════════════════════════════════
   SIDEBAR — a macOS source list. Quick actions on top, sections
   with tinted icon tiles, sub-sections that fold behind a
   disclosure row, count badges, recent plans, and the operator
   at the bottom. Every colour comes from the rail tokens.
   ═══════════════════════════════════════════════════════════════ */

const MIN_W = 220;
const MAX_W = 360;
const DEF_W = 256;
const COLLAPSED_W = 60;
const WKEY = 'nc.sidebar.width';
const SKEY = 'nc.sidebar.sections';
const UKEY = 'nc.sidebar.subs';
const RADIUS = 'calc(var(--radius) * 1.1)';

type Glyph = ComponentType<{ className?: string; style?: CSSProperties; strokeWidth?: number }>;
/** `more`: the collection behind it reached the server's ceiling, so the count is a floor, shown as "n+". */
type Count = { n: number; alert?: boolean; more?: boolean };

/** Each section's tint for its icon tiles. Status tokens, so every theme repaints them. */
const TONE: Record<NavSection, string> = {
  Home: 'var(--os-brand)', Build: 'var(--os-info)', Knowledge: 'var(--os-violet)',
  Platform: 'var(--os-warn)', Governance: 'var(--os-ok)', Admin: 'var(--os-danger)',
};
const SUB_ICON: Record<string, Glyph> = {
  Planning: ListTodo, Execution: Cpu, Quality: ShieldCheck, Delivery: Rocket,
  Thinking: Lightbulb, Extensions: Puzzle, Connections: Plug,
};

type Block = { kind: 'item'; item: NavItem } | { kind: 'sub'; name: string; items: NavItem[] };

/* Agents at work right now, counted the way GET /agents decides an agent is running: it owns a step that
   is running, or — when several agents share a task — a whole run that is running between steps. Counted
   per agent, not per run, so one agent on two runs is one. Runs name an agent by id or by name, so each is
   folded to its roster id first — otherwise "backend" and "Backend Engineer" would count twice. */
function agentsAtWork(runs: RunDoc[], roster: Catalogue['agents']): number {
  const idOf = (agent: string) => roster.find((a) => a.id === agent || a.name === agent)?.id ?? agent;
  const busy = new Set<string>();
  for (const r of runs) {
    for (const s of r.steps) if (s.status === 'running' && s.agent) busy.add(idOf(s.agent));
    if (r.role === 'agent' && r.status === 'running' && r.agent) busy.add(idOf(r.agent));
  }
  return busy.size;
}

/** A section's items in order, only those the role may open, with one sub-section's items folded into a block. */
function blocksOf(section: NavSection, canAny: (...perms: string[]) => boolean): Block[] {
  const out: Block[] = [];
  for (const item of NAV.filter((n) => n.section === section && allowed(n, canAny))) {
    const last = out[out.length - 1];
    if (item.sub && last?.kind === 'sub' && last.name === item.sub) last.items.push(item);
    else if (item.sub) out.push({ kind: 'sub', name: item.sub, items: [item] });
    else out.push({ kind: 'item', item });
  }
  return out;
}

function load<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}
function save(key: string, value: unknown) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode */ }
}

export function Sidebar({ collapsed, onToggle, onSearch, variant = 'rail' }: {
  collapsed: boolean;
  onToggle: () => void;
  /** Opens ⌘K. */
  onSearch?: () => void;
  /** 'drawer' renders inside the mobile sheet: full width, no float, no resize, no collapse. */
  variant?: 'rail' | 'drawer';
}) {
  const drawer = variant === 'drawer';
  const mini = collapsed && !drawer;
  const loc = useLocation();
  const nav = useNavigate();
  const { rail } = useTheme();
  const { tasks, plans, runs, approvals, conflicts, capped } = useData();
  const { agents } = useAccess();
  const { canAny } = useAuth();
  const blocks = useMemo(
    () => Object.fromEntries(NAV_SECTIONS.map((s) => [s, blocksOf(s, canAny)])) as Record<NavSection, Block[]>,
    [canAny],
  );
  const ref = useRef<HTMLElement>(null);
  const [resizing, setResizing] = useState(false);
  const [width, setWidth] = useState(() => load(WKEY, DEF_W));
  const [sections, setSections] = useState<Record<string, boolean>>(() => load(SKEY, {}));
  const [subs, setSubs] = useState<Record<string, boolean>>(() => load(UKEY, {}));

  useEffect(() => save(WKEY, width), [width]);
  useEffect(() => save(SKEY, sections), [sections]);
  useEffect(() => save(UKEY, subs), [subs]);

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

  // What is waiting behind each screen, so the sidebar says where to look next.
  const counts = useMemo<Record<string, Count>>(() => ({
    '/tasks': { n: tasks.filter((t) => t.status === 'in_progress').length, more: capped.tasks },
    '/plans': { n: plans.filter((p) => !inFlight(p)).length, more: capped.plans },
    '/agents': { n: agentsAtWork(runs, agents) },
    // Every pending gate is loaded whatever its age (see the store), so this one is a real count.
    '/permissions': { n: approvals.filter((a) => a.status === 'pending').length, alert: true },
    // The store holds open conflicts only: a resolved one is dropped, even when it streams back.
    '/memory': { n: conflicts.length, alert: true, more: capped.conflicts },
  }), [tasks, plans, runs, approvals, conflicts, agents, capped]);

  const isActive = (to: string) => (to === '/' ? loc.pathname === '/' : loc.pathname === to || loc.pathname.startsWith(`${to}/`));
  const compose = () => nav('/', { state: { compose: Date.now() } });
  const w = mini ? COLLAPSED_W : width;

  return (
    <div
      className={cn('h-full shrink-0', drawer ? 'w-full' : [rail !== 'flush' && 'p-2 pr-0', !resizing && 'transition-[width] duration-300 ease-out'])}
      style={drawer ? undefined : { width: w + (rail === 'flush' ? 0 : 8) }}
    >
      <aside
        ref={ref}
        className={cn(
          'rail-surface relative flex h-full flex-col', !drawer && rail !== 'flush' && 'rail-slab',
          mini ? 'overflow-visible' : 'overflow-hidden',
          !resizing && 'transition-[width] duration-300 ease-out',
        )}
        style={{
          width: drawer ? '100%' : w,
          borderRadius: drawer || rail === 'flush' ? 0 : 'calc(var(--radius) * 2)',
          borderRight: !drawer && rail === 'flush' ? '1px solid var(--rail-line)' : undefined,
        }}
      >
        <div className="rail-edge pointer-events-none absolute inset-y-0 left-0 w-px" style={{ borderRadius: 'inherit' }} />

        {!mini && !drawer && (
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

        {/* Title row */}
        <div className={cn('flex shrink-0 items-center gap-2 pt-3.5 pb-2', mini ? 'justify-center px-2' : 'px-3.5')}>
          {mini ? (
            <button onClick={onToggle} aria-label="Expand sidebar" className="transition-transform duration-200 hover:scale-105">
              <LogoMark size={30} />
            </button>
          ) : (
            <>
              <Link to="/" className="min-w-0 flex-1"><Wordmark size={30} onDark /></Link>
              {!drawer && (
                <button
                  onClick={onToggle} aria-label="Collapse sidebar" title="Collapse sidebar (⌘B)"
                  className="rail-row grid size-8 shrink-0 place-items-center"
                  style={{ borderRadius: RADIUS, color: 'var(--rail-soft)' }}
                >
                  <PanelLeftClose className="size-[17px]" strokeWidth={1.8} />
                </button>
              )}
            </>
          )}
        </div>

        {/* Quick actions */}
        <div className={cn('shrink-0 space-y-0.5 pb-2', mini ? 'px-2' : 'px-2.5')}>
          <Action icon={SquarePen} label="New requirement" onClick={compose} mini={mini} accent />
          {onSearch && <Action icon={Search} label="Search" hint="⌘K" onClick={onSearch} mini={mini} />}
        </div>

        {/* Navigation */}
        <nav aria-label="Main" className={cn('sb-scroll min-h-0 flex-1 overflow-y-auto pb-4', mini ? 'px-2' : 'px-2.5')}>
          {NAV_SECTIONS.map((section, si) => {
            if (!blocks[section].length) return null;  // nothing in it this role may open
            if (mini) {
              return (
                <div key={section}>
                  {si > 0 && <div className="mx-2 my-2 h-px" style={{ background: 'var(--rail-line)' }} />}
                  <div className="space-y-0.5">
                    {NAV.filter((n) => n.section === section && allowed(n, canAny)).map((n) => (
                      <Flyout key={n.to} label={n.label}>
                        <NavLink
                          to={n.to} end={n.to === '/'} aria-label={n.label}
                          className={({ isActive: a }) => cn('rail-row relative grid h-9 place-items-center', a && 'is-active')}
                          style={{ borderRadius: RADIUS }}
                        >
                          <Tile icon={ICONS[n.icon] ?? Circle} tone={TONE[section]} />
                          {(counts[n.to]?.n ?? 0) > 0 && (
                            <span className="absolute top-1 right-1 size-2 rounded-full"
                              style={{ background: counts[n.to]?.alert ? 'var(--os-warn)' : 'var(--rail-accent)' }} />
                          )}
                        </NavLink>
                      </Flyout>
                    ))}
                  </div>
                </div>
              );
            }

            const open = sections[section] ?? true;
            return (
              <div key={section} className={si > 0 ? 'mt-4' : 'mt-1'}>
                {section !== 'Home' && (
                  <button
                    onClick={() => setSections((s) => ({ ...s, [section]: !open }))}
                    aria-expanded={open}
                    className="group/sec mb-0.5 flex w-full items-center justify-between px-2.5 py-1"
                  >
                    <span className="text-[12px] font-semibold tracking-[0.01em]" style={{ color: 'var(--rail-dim)' }}>{section}</span>
                    <ChevronRight
                      className={cn('size-3.5 opacity-0 transition-[transform,opacity] group-hover/sec:opacity-80', open && 'rotate-90')}
                      strokeWidth={2.2} style={{ color: 'var(--rail-dim)' }}
                    />
                  </button>
                )}
                <Fold open={open}>
                  <div className="space-y-px">
                    {blocks[section].map((b) => (b.kind === 'item'
                      ? <Row key={b.item.to} item={b.item} tone={TONE[section]} count={counts[b.item.to]} />
                      : (
                        <Sub
                          key={b.name} name={b.name} items={b.items} tone={TONE[section]} counts={counts}
                          active={b.items.some((i) => isActive(i.to))}
                          open={subs[b.name] ?? b.items.some((i) => isActive(i.to))}
                          onToggle={(v) => setSubs((s) => ({ ...s, [b.name]: v }))}
                        />
                      )))}
                  </div>
                </Fold>
              </div>
            );
          })}

          {!mini && plans.length > 0 && (
            <div className="mt-5">
              <div className="mb-0.5 px-2.5 py-1 text-[12px] font-semibold" style={{ color: 'var(--rail-dim)' }}>Recent plans</div>
              <div className="space-y-px">
                {plans.slice(0, 5).map((p) => (
                  <Link
                    key={p.ref} to={`/plans?ref=${p.ref}`} title={p.rawRequirement}
                    className="rail-row flex h-8 items-center gap-2.5 px-2" style={{ borderRadius: RADIUS }}
                  >
                    <FileText className="size-4 shrink-0" strokeWidth={1.8} style={{ color: 'var(--rail-dim)' }} />
                    <span className="min-w-0 flex-1 truncate text-[13px]" style={{ color: 'var(--rail-item)' }}>{p.rawRequirement}</span>
                  </Link>
                ))}
              </div>
            </div>
          )}
        </nav>

        {/* The operator */}
        <div className={cn('shrink-0 py-2.5', mini ? 'px-2' : 'px-2.5')} style={{ borderTop: '1px solid var(--rail-line)' }}>
          <AccountMenu mini={mini} />
          {mini && (
            <button
              onClick={onToggle} aria-label="Expand sidebar"
              className="rail-row mt-1 grid h-9 w-full place-items-center" style={{ borderRadius: RADIUS, color: 'var(--rail-soft)' }}
            >
              <PanelLeftOpen className="size-[17px]" strokeWidth={1.8} />
            </button>
          )}
        </div>
      </aside>
    </div>
  );
}

/* ── pieces ───────────────────────────────────────────────────── */

/** Folds its children away. Folded content is inert, so Tab never lands on a hidden link. */
function Fold({ open, children }: { open: boolean; children: ReactNode }) {
  return (
    <div className={cn('grid transition-[grid-template-rows,opacity] duration-200 ease-out',
      open ? 'grid-rows-[1fr] opacity-100' : 'grid-rows-[0fr] opacity-0')}>
      <div className="min-h-0 overflow-hidden" inert={!open}>{children}</div>
    </div>
  );
}

function Tile({ icon: I, tone }: { icon: Glyph; tone: string }) {
  return (
    <span
      className="nav-tile grid size-[22px] shrink-0 place-items-center"
      style={{ '--tile': tone, borderRadius: 'calc(var(--radius) * 0.85)' } as CSSProperties}
    >
      <I className="size-[13px]" strokeWidth={2.1} />
    </span>
  );
}

function Badge({ n = 0, alert, more }: { n?: number; alert?: boolean; more?: boolean }) {
  if (!n) return null;
  return (
    <span
      title={more ? 'At least this many: the list reached the server’s ceiling' : undefined}
      className="tnum min-w-5 shrink-0 rounded-full px-1.5 text-center text-[11px] leading-[18px] font-semibold"
      style={alert
        ? { background: 'color-mix(in srgb, var(--os-warn) 20%, transparent)', color: 'var(--os-warn)' }
        : { background: 'var(--rail-chip)', color: 'var(--rail-soft)' }}
    >
      {n}{more ? '+' : ''}
    </span>
  );
}

function Row({ item, tone, count }: { item: NavItem; tone: string; count?: Count }) {
  return (
    <NavLink
      to={item.to} end={item.to === '/'}
      className={({ isActive }) => cn('rail-row flex h-8 items-center gap-2.5 px-2', isActive && 'is-active')}
      style={{ borderRadius: RADIUS }}
    >
      {({ isActive }) => (
        <>
          <Tile icon={ICONS[item.icon] ?? Circle} tone={tone} />
          <span className={cn('min-w-0 flex-1 truncate text-[13.5px]', isActive ? 'font-semibold' : 'font-medium')}
            style={{ color: isActive ? 'var(--rail-ink)' : 'var(--rail-item)' }}>
            {item.label}
          </span>
          <Badge n={count?.n} alert={count?.alert} more={count?.more} />
        </>
      )}
    </NavLink>
  );
}

function Sub({ name, items, tone, counts, open, active, onToggle }: {
  name: string; items: NavItem[]; tone: string; counts: Record<string, Count>;
  open: boolean; active: boolean; onToggle: (open: boolean) => void;
}) {
  const hidden = items.reduce((n, i) => n + (counts[i.to]?.n ?? 0), 0);
  const alert = items.some((i) => counts[i.to]?.alert && counts[i.to]?.n);
  const more = items.some((i) => counts[i.to]?.more && counts[i.to]?.n);
  return (
    <div>
      <button
        onClick={() => onToggle(!open)} aria-expanded={open}
        className="rail-row flex h-8 w-full items-center gap-2.5 px-2" style={{ borderRadius: RADIUS }}
      >
        <Tile icon={SUB_ICON[name] ?? Circle} tone={tone} />
        <span className={cn('min-w-0 flex-1 truncate text-left text-[13.5px]', active ? 'font-semibold' : 'font-medium')}
          style={{ color: active ? 'var(--rail-ink)' : 'var(--rail-item)' }}>
          {name}
        </span>
        {!open && <Badge n={hidden} alert={alert} more={more} />}
        <ChevronRight className={cn('size-3.5 shrink-0 transition-transform duration-200', open && 'rotate-90')}
          strokeWidth={2.2} style={{ color: 'var(--rail-dim)' }} />
      </button>
      <Fold open={open}>
        <div className="mt-px ml-[18px] space-y-px border-l py-0.5 pl-2" style={{ borderColor: 'var(--rail-line)' }}>
          {items.map((i) => (
            <NavLink
              key={i.to} to={i.to}
              className={({ isActive }) => cn('rail-row flex h-[30px] items-center gap-2 px-2.5', isActive && 'is-active')}
              style={{ borderRadius: RADIUS }}
            >
              {({ isActive }) => (
                <>
                  <span className={cn('min-w-0 flex-1 truncate text-[13px]', isActive ? 'font-semibold' : 'font-medium')}
                    style={{ color: isActive ? 'var(--rail-ink)' : 'var(--rail-item)' }}>
                    {i.label}
                  </span>
                  <Badge n={counts[i.to]?.n} alert={counts[i.to]?.alert} more={counts[i.to]?.more} />
                </>
              )}
            </NavLink>
          ))}
        </div>
      </Fold>
    </div>
  );
}

function Action({ icon: I, label, hint, onClick, mini, accent }: {
  icon: Glyph; label: string; hint?: string; onClick: () => void; mini: boolean; accent?: boolean;
}) {
  const button = (
    <button
      onClick={onClick} aria-label={mini ? label : undefined}
      className={cn('rail-row flex h-9 w-full items-center gap-2.5', mini ? 'justify-center' : 'px-2')}
      style={{ borderRadius: RADIUS }}
    >
      <span className="grid size-[22px] shrink-0 place-items-center">
        <I className="size-[17px]" strokeWidth={1.9} style={{ color: accent ? 'var(--rail-accent)' : 'var(--rail-soft)' }} />
      </span>
      {!mini && <span className="min-w-0 flex-1 truncate text-left text-[13.5px] font-medium" style={{ color: 'var(--rail-ink)' }}>{label}</span>}
      {!mini && hint && <kbd className="text-[11.5px] font-medium" style={{ color: 'var(--rail-dim)' }}>{hint}</kbd>}
    </button>
  );
  return mini ? <Flyout label={label}>{button}</Flyout> : button;
}

/** A label that floats beside a collapsed rail — portalled so the rail never clips it. */
function Flyout({ label, children }: { label: string; children: ReactNode }) {
  const [fly, setFly] = useState(false);
  const [pos, setPos] = useState({ top: 0, left: 0 });
  const ref = useRef<HTMLDivElement>(null);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const show = () => {
    clearTimeout(timer.current);
    if (ref.current) {
      const r = ref.current.getBoundingClientRect();
      setPos({ top: r.top + r.height / 2, left: r.right });
    }
    setFly(true);
  };
  const hide = () => { timer.current = setTimeout(() => setFly(false), 100); };
  useEffect(() => () => clearTimeout(timer.current), []);
  return (
    <>
      <div ref={ref} onMouseEnter={show} onMouseLeave={hide}>{children}</div>
      {fly && createPortal(
        <div className="rail-flyout-enter pointer-events-none fixed z-[9999] -translate-y-1/2 pl-2" style={{ top: pos.top, left: pos.left }}>
          <div className="rail-flyout px-3 py-1.5" style={{ borderRadius: RADIUS }}>
            <span className="text-[12.5px] font-semibold whitespace-nowrap" style={{ color: 'var(--rail-ink)' }}>{label}</span>
          </div>
        </div>,
        document.body,
      )}
    </>
  );
}
