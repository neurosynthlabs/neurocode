/* ═══════════════════════════════════════════════════════════════
   OS KIT — the primitives every NeuroCode screen is built from.
   macOS-calm by default: grouped cards with hairlines instead of
   boxes in boxes, capsule tags, slim meters, sentence-case labels.
   Built on the shadcn token contract so every theme repaints them.
   ═══════════════════════════════════════════════════════════════ */
import type { CSSProperties, ReactNode } from 'react';
import { cn } from '@/lib/utils';
import type { Risk } from '@/types';

export const cx = cn;

export { LogoMark, Wordmark } from './Logo';
export { Wizard } from './Wizard';
export type { WizardStep } from './Wizard';

/* ── Page scaffolding ─────────────────────────────────────────── */
export function Page({ children }: { children: ReactNode }) {
  return <div className="flex h-full min-h-0 flex-col">{children}</div>;
}

export function PageHeader({
  title, subtitle, actions, children, icon,
}: { title: string; subtitle?: string; actions?: ReactNode; children?: ReactNode; icon?: ReactNode }) {
  return (
    <header className="relative shrink-0 bg-bg px-5 pt-6 sm:px-8 sm:pt-7">
      <span className="title-rule pointer-events-none absolute inset-x-0 bottom-0 h-px" />
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between sm:gap-6">
        <div className="flex min-w-0 items-start gap-3">
          {icon && <span className="mt-1.5 text-brand">{icon}</span>}
          <div className="min-w-0">
            <h1 className="text-[24px] leading-tight font-bold tracking-[-0.025em] text-ink sm:text-[28px]">{title}</h1>
            {subtitle && <p className="mt-1.5 max-w-3xl text-[14px] leading-relaxed text-soft">{subtitle}</p>}
          </div>
        </div>
        {actions && <div className="flex shrink-0 flex-wrap items-center gap-2 sm:pt-1">{actions}</div>}
      </div>
      <div className={children ? 'mt-5' : 'mt-6'}>{children}</div>
    </header>
  );
}

export function PageBody({ children, className }: { children: ReactNode; className?: string }) {
  // A page that sets its own padding (p-0 for edge-to-edge master/detail) must win outright —
  // tailwind-merge would otherwise keep the sm: variant of the default alongside it.
  const custom = /(^|\s)(p|px|py)-/.test(className ?? '');
  return (
    <div className={cn('animate-page-enter min-h-0 flex-1 overflow-y-auto', !custom && 'px-5 py-5 sm:px-8 sm:py-7', className)}>
      {children}
    </div>
  );
}

/* ── Panel: a grouped card. The header is text, not a box. ────── */
export function Panel({
  title, eyebrow, actions, children, className, bodyClass, flush,
}: {
  title?: ReactNode; eyebrow?: string; actions?: ReactNode;
  children: ReactNode; className?: string; bodyClass?: string; flush?: boolean;
}) {
  const head = !!(title || actions || eyebrow);
  return (
    <section className={cn('panel flex min-h-0 flex-col overflow-clip rounded-xl border border-line/70 bg-surface', className)}>
      {head && (
        <div className={cn('flex shrink-0 flex-wrap items-center justify-between gap-x-3 gap-y-1.5 px-5 pt-4',
          flush ? 'border-b border-line/60 pb-3.5' : 'pb-1')}>
          <div className="min-w-0">
            {eyebrow && <div className="mb-0.5 text-[12px] font-medium text-dim [overflow-wrap:anywhere] first-letter:uppercase">{eyebrow}</div>}
            {title && <h2 className="text-[15px] font-semibold tracking-[-0.01em] text-ink [overflow-wrap:anywhere] [&>span]:flex-wrap">{title}</h2>}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </div>
      )}
      <div className={cn('min-h-0 flex-1', !flush && (head ? 'px-5 pt-2.5 pb-5' : 'p-5'), bodyClass)}>{children}</div>
    </section>
  );
}

export function SectionTitle({ children, right, className }: { children: ReactNode; right?: ReactNode; className?: string }) {
  return (
    <div className={cn('mb-2.5 flex items-center justify-between gap-3', className)}>
      <div className="text-[13px] font-semibold text-ink-2">{children}</div>
      {right}
    </div>
  );
}

export function Divider({ className }: { className?: string }) {
  return <div className={cn('h-px w-full bg-line/70', className)} />;
}

/* ── Tone system ──────────────────────────────────────────────── */
export type Tone = 'ok' | 'warn' | 'danger' | 'info' | 'brand' | 'violet' | 'neutral';

const TAG_TONE: Record<Tone, string> = {
  ok:      'bg-ok/12 text-ok',
  warn:    'bg-warn/12 text-warn',
  danger:  'bg-danger/12 text-danger',
  info:    'bg-info/12 text-info',
  brand:   'bg-brand/12 text-brand',
  violet:  'bg-violet/12 text-violet',
  neutral: 'bg-surface-3/80 text-soft',
};

const BG_TONE: Record<Tone, string> = {
  ok: 'bg-ok', warn: 'bg-warn', danger: 'bg-danger', info: 'bg-info',
  brand: 'bg-brand', violet: 'bg-violet', neutral: 'bg-line-strong',
};

export const TEXT_TONE: Record<Tone, string> = {
  ok: 'text-ok', warn: 'text-warn', danger: 'text-danger', info: 'text-info',
  brand: 'text-brand', violet: 'text-violet', neutral: 'text-soft',
};

// Status words arrive from data in capitals; a capsule reads better in sentence case.
const WORD: Record<string, string> = {
  LOW: 'Low', MEDIUM: 'Medium', HIGH: 'High', CRITICAL: 'Critical', URGENT: 'Urgent', NORMAL: 'Normal',
};

/** A soft capsule — the product's status badge. */
export function Tag({ tone = 'neutral', children, className }: { tone?: Tone; children: ReactNode; className?: string }) {
  return (
    <span className={cn(
      'inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11.5px] leading-4 font-medium whitespace-nowrap',
      TAG_TONE[tone], className,
    )}>
      {typeof children === 'string' ? WORD[children] ?? children : children}
    </span>
  );
}

const RISK_TONE: Record<Risk, Tone> = { LOW: 'ok', MEDIUM: 'warn', HIGH: 'danger', CRITICAL: 'danger' };
export function RiskPill({ risk, bare }: { risk: Risk; bare?: boolean }) {
  return <Tag tone={RISK_TONE[risk]}>{bare || risk === 'CRITICAL' ? WORD[risk] : `${WORD[risk]} risk`}</Tag>;
}

/* ── Status dots ──────────────────────────────────────────────── */
const DOT: Record<string, string> = {
  ok: 'bg-ok', running: 'bg-ok', connected: 'bg-ok', pass: 'bg-ok', success: 'bg-ok', up: 'bg-ok',
  active: 'bg-ok', done: 'bg-ok', approved: 'bg-ok', enabled: 'bg-ok', ready: 'bg-ok', complete: 'bg-ok', merged: 'bg-ok', clean: 'bg-ok',
  warn: 'bg-warn', waiting: 'bg-warn', review: 'bg-warn', pending: 'bg-warn', partial: 'bg-warn',
  restarting: 'bg-warn', dirty: 'bg-warn', planning: 'bg-warn', auth_required: 'bg-warn', awaiting_approval: 'bg-warn', ahead: 'bg-warn',
  danger: 'bg-danger', error: 'bg-danger', fail: 'bg-danger', failed: 'bg-danger', blocked: 'bg-danger',
  down: 'bg-danger', denied: 'bg-danger', conflict: 'bg-danger', rolled_back: 'bg-danger',
  info: 'bg-info', loading: 'bg-info', in_progress: 'bg-info', queued: 'bg-info', forked: 'bg-info',
  idle: 'bg-dim', disconnected: 'bg-dim', offline: 'bg-dim', skipped: 'bg-dim', disabled: 'bg-dim',
  backlog: 'bg-dim', ended: 'bg-dim', archived: 'bg-dim', paused: 'bg-dim', todo: 'bg-dim',
};

export function Dot({ state, pulse, className }: { state: string; pulse?: boolean; className?: string }) {
  const c = DOT[state] ?? 'bg-dim';
  return (
    <span className={cn('relative inline-flex size-2 shrink-0 rounded-full', c, className)}>
      {pulse && <span className={cn('absolute -inset-1 animate-ping rounded-full opacity-40', c)} />}
      {pulse && <span className={cn('absolute inset-0 breathe rounded-full', c)} />}
    </span>
  );
}

const STATE_TEXT: Record<string, string> = {
  ok: 'text-ok', running: 'text-ok', connected: 'text-ok', pass: 'text-ok', success: 'text-ok',
  active: 'text-ok', up: 'text-ok', done: 'text-ok', approved: 'text-ok', complete: 'text-ok', merged: 'text-ok', clean: 'text-ok', ready: 'text-ok',
  warn: 'text-warn', waiting: 'text-warn', pending: 'text-warn', review: 'text-warn', partial: 'text-warn',
  dirty: 'text-warn', planning: 'text-warn', auth_required: 'text-warn', awaiting_approval: 'text-warn',
  danger: 'text-danger', error: 'text-danger', fail: 'text-danger', failed: 'text-danger',
  blocked: 'text-danger', down: 'text-danger', denied: 'text-danger', conflict: 'text-danger',
  info: 'text-info', in_progress: 'text-info', queued: 'text-info',
};

export function StatusText({ state, label, className }: { state: string; label?: string; className?: string }) {
  return (
    <span className={cn('inline-flex items-center gap-1.5 text-[13px] capitalize', STATE_TEXT[state] ?? 'text-soft', className)}>
      <Dot state={state} pulse={state === 'running'} />
      {label ?? state.replace(/_/g, ' ')}
    </span>
  );
}

/* ── Bars & meters ────────────────────────────────────────────── */
export function Bar({ pct, tone, className, height = 'h-1.5' }: { pct: number; tone?: Tone; className?: string; height?: string }) {
  const auto = pct >= 80 ? 'bg-ok' : pct >= 55 ? 'bg-brand' : pct >= 30 ? 'bg-warn' : 'bg-danger';
  return (
    <div className={cn('w-full overflow-hidden rounded-full bg-surface-3', height, className)}>
      <div className={cn('progress-animate h-full rounded-full transition-[width] duration-500', tone ? BG_TONE[tone] : auto)}
           style={{ width: `${Math.max(0, Math.min(100, pct))}%` }} />
    </div>
  );
}

export function MeterRow({ label, pct, right, tone, hint }: {
  label: string; pct: number; right?: ReactNode; tone?: Tone; hint?: string;
}) {
  return (
    <div className="py-2">
      <div className="mb-1.5 flex items-baseline justify-between gap-3">
        <span className="truncate text-[13px] text-ink-2">{label}</span>
        <span className="tnum shrink-0 text-[13px] font-medium text-ink">{right ?? `${pct}%`}</span>
      </div>
      <Bar pct={pct} tone={tone} />
      {hint && <p className="mt-1.5 text-[12px] text-dim">{hint}</p>}
    </div>
  );
}

/** A slim capsule meter. `width` is the old block count and still sets the length. */
export function BlockBar({ pct, width = 10, tone }: { pct: number; width?: number; tone?: Tone }) {
  const p = Math.max(0, Math.min(100, pct));
  const fill = tone ? BG_TONE[tone] : p >= 80 ? 'bg-ok' : p >= 40 ? 'bg-brand' : 'bg-warn';
  return (
    <span
      role="progressbar" aria-valuenow={Math.round(p)} aria-valuemin={0} aria-valuemax={100}
      className="inline-flex h-1.5 shrink-0 overflow-hidden rounded-full bg-surface-3 align-middle"
      style={{ width: Math.max(28, width * 6) }}
    >
      <span className={cn('h-full rounded-full transition-[width] duration-500', fill)} style={{ width: `${p}%` }} />
    </span>
  );
}

/* ── Stats ────────────────────────────────────────────────────── */
export function Stat({ label, value, sub, tone, icon, onClick }: {
  label: string; value: ReactNode; sub?: ReactNode; tone?: Tone; icon?: ReactNode; onClick?: () => void;
}) {
  const Comp = onClick ? 'button' : 'div';
  return (
    <Comp
      onClick={onClick}
      className={cn(
        'panel rounded-xl border border-line/70 bg-surface px-5 py-4 text-left',
        onClick && 'cursor-pointer transition-colors hover:bg-surface-2',
      )}
    >
      <div className="flex items-center gap-2 text-[13px] font-medium text-soft">
        {icon && <span className="text-dim">{icon}</span>}
        {label}
      </div>
      <div className={cn('figure animate-count-up mt-2.5 text-[28px] leading-none font-semibold tracking-[-0.02em]', tone ? TEXT_TONE[tone] : 'text-ink')}>
        {value}
      </div>
      {sub && <div className="mt-2 text-[12.5px] text-dim">{sub}</div>}
    </Comp>
  );
}

export function StatGrid({ children, cols = 4, className }: { children: ReactNode; cols?: number; className?: string }) {
  return (
    <div
      className={cn('stagger grid grid-cols-2 gap-4', cols > 2 && 'sm:grid-cols-3 md:grid-cols-[repeat(var(--cols),minmax(0,1fr))]', className)}
      style={{ '--cols': cols } as CSSProperties}
    >
      {children}
    </div>
  );
}

/* ── Key/value ────────────────────────────────────────────────── */
export function KV({ k, v, mono }: { k: string; v: ReactNode; mono?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-line/50 py-2 last:border-0">
      <span className="shrink-0 text-[13px] text-dim">{k}</span>
      <span className={cn('min-w-0 truncate text-right text-[13px] text-ink-2', mono && 'font-mono text-[12.5px]')}>{v}</span>
    </div>
  );
}

/* ── Table: hairlines and sentence-case headers ───────────────── */
export function DataTable({ head, children, className }: { head: (string | ReactNode)[]; children: ReactNode; className?: string }) {
  return (
    <div className={cn('w-full overflow-x-auto', className)}>
      <table className="w-full text-left">
        <thead>
          <tr className="border-b border-line/60">
            {head.map((h, i) => (
              <th key={i} className="px-4 py-2.5 text-[12.5px] font-medium whitespace-nowrap text-dim first:pl-5 last:pr-5">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

export function Row({ children, onClick, active, className }: {
  children: ReactNode; onClick?: () => void; active?: boolean; className?: string;
}) {
  return (
    <tr
      onClick={onClick}
      className={cn(
        'row-hover border-b border-line/50 last:border-0',
        onClick && 'cursor-pointer',
        active && 'bg-surface-2',
        className,
      )}
    >
      {children}
    </tr>
  );
}

export function Cell({ children, className, mono, colSpan }: {
  children?: ReactNode; className?: string; mono?: boolean; colSpan?: number;
}) {
  return (
    <td colSpan={colSpan}
        className={cn('px-4 py-3 align-middle text-[13.5px] text-ink-2 first:pl-5 last:pr-5', mono && 'font-mono text-[12.5px]', className)}>
      {children}
    </td>
  );
}

/* ── Segmented control, Apple style: a thumb that sits on a track ─ */
export function Segmented<T extends string>({ options, value, onChange, className }: {
  options: { id: T; label: string }[]; value: T; onChange: (v: T) => void; className?: string;
}) {
  return (
    <div
      className={cn('no-scrollbar inline-flex max-w-full overflow-x-auto bg-surface-2 p-[3px] ring-1 ring-line/60 ring-inset', className)}
      style={{ borderRadius: 'calc(var(--radius) * 1.3)' }}
    >
      {options.map((o) => (
        <button
          key={o.id}
          onClick={() => onChange(o.id)}
          aria-pressed={value === o.id}
          className={cn('shrink-0 px-3 py-1.5 text-[13px] whitespace-nowrap transition-[color,background-color,box-shadow] duration-150',
            value === o.id ? 'seg-thumb font-medium text-ink' : 'text-soft hover:text-ink-2')}
          style={{ borderRadius: 'var(--radius)' }}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/* ── List row (master pane) ───────────────────────────────────── */
export function ListRow({ active, onClick, children, className }: {
  active?: boolean; onClick?: () => void; children: ReactNode; className?: string;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        'w-full border-l-2 px-4 py-3.5 text-left transition-[background-color,border-color] duration-150',
        active ? 'border-brand bg-brand/7' : 'border-transparent hover:bg-surface-2/70',
        className,
      )}
    >
      {children}
    </button>
  );
}

/* ── Layout helpers ───────────────────────────────────────────── */
export function Split({ left, right, leftWidth = 300, className }: {
  left: ReactNode; right: ReactNode; leftWidth?: number; className?: string;
}) {
  return (
    <div className={cn('flex h-full min-h-0 flex-col gap-4 md:flex-row', className)}>
      <div className="flex min-h-0 w-full shrink-0 flex-col md:w-[var(--split-w)]" style={{ '--split-w': `${leftWidth}px` } as CSSProperties}>{left}</div>
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">{right}</div>
    </div>
  );
}

export function Empty({ icon, title, hint, action }: { icon?: ReactNode; title: string; hint?: string; action?: ReactNode }) {
  return (
    <div className="flex h-full min-h-[200px] flex-col items-center justify-center gap-2 p-10 text-center">
      {icon && <div className="mb-1 text-dim">{icon}</div>}
      <p className="text-[14.5px] font-medium text-ink-2">{title}</p>
      {hint && <p className="max-w-sm text-[13px] leading-relaxed text-dim">{hint}</p>}
      {action && <div className="mt-3">{action}</div>}
    </div>
  );
}

/* ── Monospace surfaces ───────────────────────────────────────── */
export function Ascii({ children, className }: { children: string; className?: string }) {
  return <pre className={cn('ascii overflow-x-auto rounded-lg bg-base p-4 ring-1 ring-line/60 ring-inset', className)}>{children}</pre>;
}

export function Mono({ children, tone, className }: { children: ReactNode; tone?: Tone; className?: string }) {
  return (
    <code className={cn('rounded-md bg-surface-2 px-1.5 py-0.5 font-mono text-[12px]', tone ? TEXT_TONE[tone] : 'text-ink-2', className)}>
      {children}
    </code>
  );
}

/* ── Misc ─────────────────────────────────────────────────────── */
export function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="inline-flex h-5 min-w-5 items-center justify-center rounded-md border border-line bg-surface-2 px-1 font-mono text-[11px] text-soft">
      {children}
    </kbd>
  );
}

export function Avatar2({ label, tone = 'brand' }: { label: string; tone?: Tone }) {
  const bg = { ok: 'bg-ok/15 text-ok', warn: 'bg-warn/15 text-warn', danger: 'bg-danger/15 text-danger',
    info: 'bg-info/15 text-info', brand: 'bg-brand/15 text-brand', violet: 'bg-violet/15 text-violet',
    neutral: 'bg-surface-3 text-soft' }[tone];
  return (
    <span className={cn('inline-flex size-6 shrink-0 items-center justify-center rounded-full text-[10.5px] font-semibold ring-2 ring-surface', bg)}>
      {label.slice(0, 2).toUpperCase()}
    </span>
  );
}

/* ═══════════════════════════════════════════════════════════════
   Rich primitives — fields, meters and inline charts.
   Every radius is derived from --radius, so the whole product
   sharpens or softens with one control.
   ═══════════════════════════════════════════════════════════════ */

/** Labelled input. Focus ring follows the brand colour. */
export function Field({
  value, onChange, placeholder, icon, label, className, mono, type = 'text', onClear,
}: {
  value: string; onChange: (v: string) => void; placeholder?: string; icon?: ReactNode;
  label?: string; className?: string; mono?: boolean; type?: string; onClear?: () => void;
}) {
  return (
    <label className={cn('block', className)}>
      {label && <span className="mb-1.5 block text-[12.5px] font-medium text-soft">{label}</span>}
      <span className="focus-brand flex h-9 items-center gap-2 rounded-lg border border-line bg-surface-2/60 px-3 transition-colors">
        {icon && <span className="shrink-0 text-dim">{icon}</span>}
        <input
          type={type}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          className={cn('min-w-0 flex-1 bg-transparent text-[13.5px] text-ink placeholder:text-dim focus-visible:outline-none',
            mono && 'font-mono text-[13px]')}
        />
        {onClear && value && (
          <button onClick={onClear} className="shrink-0 text-[12px] text-dim hover:text-ink">Clear</button>
        )}
      </span>
    </label>
  );
}

/** Native select styled to the token system — reliable across every browser. */
export function SelectField({ value, onChange, options, label, className }: {
  value: string; onChange: (v: string) => void;
  options: { value: string; label: string }[] | string[];
  label?: string; className?: string;
}) {
  const opts = options.map((o) => (typeof o === 'string' ? { value: o, label: o } : o));
  return (
    <label className={cn('block', className)}>
      {label && <span className="mb-1.5 block text-[12.5px] font-medium text-soft">{label}</span>}
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="h-9 w-full rounded-lg border border-line bg-surface-2/60 px-2.5 text-[13.5px] text-ink-2 transition-colors hover:text-ink focus-visible:border-brand"
      >
        {opts.map((o) => <option key={o.value} value={o.value} className="bg-surface">{o.label}</option>)}
      </select>
    </label>
  );
}

/** The filter strip that sits under a page header. */
export function Toolbar({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn('flex flex-wrap items-center gap-2.5 pb-4', className)}>{children}</div>;
}

/** Signed delta with the right colour and arrow. */
export function Trend({ value, suffix = '', invert }: { value: number; suffix?: string; invert?: boolean }) {
  const up = value > 0;
  const good = invert ? !up : up;
  if (value === 0) return <span className="tnum text-[12.5px] text-dim">—</span>;
  return (
    <span className={cn('tnum inline-flex items-center gap-0.5 text-[12.5px]', good ? 'text-ok' : 'text-danger')}>
      {up ? '▲' : '▼'}{Math.abs(value)}{suffix}
    </span>
  );
}

/** Donut meter — good for a single headline percentage. */
export function Ring({ pct, size = 44, label, tone }: { pct: number; size?: number; label?: string; tone?: Tone }) {
  const r = (size - 6) / 2;
  const c = 2 * Math.PI * r;
  const stroke = tone
    ? { ok: 'var(--os-ok)', warn: 'var(--os-warn)', danger: 'var(--os-danger)', info: 'var(--os-info)', brand: 'var(--os-brand)', violet: 'var(--os-violet)', neutral: 'var(--os-line-strong)' }[tone]
    : pct >= 80 ? 'var(--os-ok)' : pct >= 50 ? 'var(--os-brand)' : 'var(--os-warn)';
  return (
    <span className="relative inline-grid shrink-0 place-items-center" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--os-surface-3)" strokeWidth="3" />
        <circle
          cx={size / 2} cy={size / 2} r={r} fill="none" stroke={stroke} strokeWidth="3" strokeLinecap="round"
          strokeDasharray={c} strokeDashoffset={c - (Math.max(0, Math.min(100, pct)) / 100) * c}
          style={{ transition: 'stroke-dashoffset .6s cubic-bezier(.16,1,.3,1)' }}
        />
      </svg>
      <span className="absolute grid place-items-center leading-none">
        <span className="tnum font-semibold text-ink" style={{ fontSize: size * 0.26 }}>{label ?? `${pct}%`}</span>
      </span>
    </span>
  );
}

/** Inline sparkline — no chart library, follows the theme. */
export function Sparkline({ points, width = 72, height = 20, tone = 'brand' }: {
  points: number[]; width?: number; height?: number; tone?: Tone;
}) {
  if (points.length < 2) return null;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min || 1;
  const d = points
    .map((p, i) => `${(i / (points.length - 1)) * width},${height - ((p - min) / span) * (height - 2) - 1}`)
    .join(' L ');
  const stroke = { ok: 'var(--os-ok)', warn: 'var(--os-warn)', danger: 'var(--os-danger)', info: 'var(--os-info)', brand: 'var(--os-brand)', violet: 'var(--os-violet)', neutral: 'var(--os-line-strong)' }[tone];
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" className="max-w-full shrink-0 overflow-visible">
      <path d={`M ${d}`} fill="none" stroke={stroke} strokeWidth="1.25" strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

/** Vertical bar chart drawn inline — used on Cost and Evals. */
export function MiniBars({ values, width = 180, height = 40, tone = 'brand', onHover }: {
  values: number[]; width?: number; height?: number; tone?: Tone; onHover?: (i: number | null) => void;
}) {
  const max = Math.max(...values, 1);
  const gap = 2;
  const bw = (width - gap * (values.length - 1)) / values.length;
  const fill = { ok: 'var(--os-ok)', warn: 'var(--os-warn)', danger: 'var(--os-danger)', info: 'var(--os-info)', brand: 'var(--os-brand)', violet: 'var(--os-violet)', neutral: 'var(--os-line-strong)' }[tone];
  return (
    <svg width={width} height={height} onMouseLeave={() => onHover?.(null)}>
      {values.map((v, i) => {
        const h = Math.max(1, (v / max) * height);
        return (
          <rect
            key={i}
            x={i * (bw + gap)} y={height - h} width={bw} height={h}
            rx={1.5} fill={fill} opacity={0.85}
            onMouseEnter={() => onHover?.(i)}
          />
        );
      })}
    </svg>
  );
}
