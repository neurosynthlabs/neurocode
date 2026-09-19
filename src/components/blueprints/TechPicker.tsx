import { useMemo, useState } from 'react';
import { Check, ChevronsUpDown, PenLine, Search, X } from 'lucide-react';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import type { Tech, TechCategory } from '@/lib/live/blueprints';
import { cn } from '@/lib/utils';

/* A searchable picker over the technology catalogue (server/app/data/tech.json). The layer's own category
   comes first; everything else is one search away; and anything the catalogue does not hold can be typed in
   as a custom technology — it is kept as the text you wrote and shown as custom. */

const SHOWN = 60;

export function TechPicker({ value, onChange, tech, category, placeholder = 'Choose…', allowNone, className, label }: {
  value: string | null;
  onChange: (id: string | null) => void;
  tech: Tech[];
  category?: TechCategory;
  placeholder?: string;
  /** Offers "None" to clear the choice. */
  allowNone?: boolean;
  className?: string;
  /** What the trigger is for, read by screen readers. */
  label: string;
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState('');
  const byId = useMemo(() => new Map(tech.map((t) => [t.id, t])), [tech]);
  const current = value ? byId.get(value) : undefined;
  const found = useMemo(() => {
    const words = q.trim().toLowerCase();
    const hits = tech.filter((t) => !words || `${t.name} ${t.id} ${t.category} ${t.languages.join(' ')}`.toLowerCase().includes(words));
    return hits.sort((a, b) => Number(b.category === category) - Number(a.category === category) || a.name.localeCompare(b.name));
  }, [tech, q, category]);
  const typed = q.trim();
  const exact = found.some((t) => t.name.toLowerCase() === typed.toLowerCase() || t.id === typed.toLowerCase());
  const pick = (id: string | null) => { onChange(id); setOpen(false); setQ(''); };

  return (
    <Popover open={open} onOpenChange={(o) => { setOpen(o); if (!o) setQ(''); }}>
      <PopoverTrigger
        aria-label={label}
        className={cn('flex h-9 w-full min-w-0 items-center gap-2 rounded-lg border border-line bg-surface-2/60 px-3 text-left text-[13.5px] transition-colors hover:border-line-strong',
          className)}
      >
        <span className={cn('min-w-0 flex-1 truncate', value ? 'text-ink' : 'text-dim')}>
          {current ? current.name : value || placeholder}
        </span>
        {value && !current && <span className="shrink-0 rounded-full bg-surface-3 px-2 py-0.5 text-[11px] text-soft">custom</span>}
        <ChevronsUpDown className="size-3.5 shrink-0 text-dim" />
      </PopoverTrigger>
      <PopoverContent align="start" className="w-[min(360px,calc(100vw-32px))] gap-0 p-0">
        <div className="flex items-center gap-2 border-b border-line px-3">
          <Search className="size-3.5 shrink-0 text-dim" />
          <input
            autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search the catalogue, or type your own"
            aria-label="Search technologies"
            onKeyDown={(e) => {
              if (e.key !== 'Enter') return;
              e.preventDefault();
              if (found[0] && (exact || !typed)) pick(found[0].id);
              else if (typed) pick(typed);
            }}
            className="h-10 min-w-0 flex-1 bg-transparent text-[13.5px] text-ink placeholder:text-dim focus-visible:outline-none"
          />
        </div>
        <div className="max-h-[320px] overflow-y-auto p-1">
          {allowNone && value && (
            <button onClick={() => pick(null)} className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-[13px] text-soft hover:bg-surface-2">
              <X className="size-3.5" />None
            </button>
          )}
          {typed && !exact && (
            <button onClick={() => pick(typed)} className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-[13px] text-ink-2 hover:bg-surface-2">
              <PenLine className="size-3.5 text-dim" />
              <span className="min-w-0 truncate">Use “{typed}” as a custom technology</span>
            </button>
          )}
          {found.slice(0, SHOWN).map((t) => (
            <button key={t.id} onClick={() => pick(t.id)} title={t.notes}
              className="flex w-full items-start gap-2.5 rounded-md px-2.5 py-2 text-left hover:bg-surface-2">
              <Check className={cn('mt-0.5 size-3.5 shrink-0', t.id === value ? 'text-brand' : 'invisible')} />
              <span className="min-w-0 flex-1">
                <span className="flex items-baseline gap-2">
                  <span className="truncate text-[13.5px] text-ink">{t.name}</span>
                  <span className="shrink-0 text-[11.5px] text-dim">{t.category}</span>
                </span>
                <span className="block truncate text-[12px] text-dim">{t.license} · {t.maturity}{t.languages.length ? ` · ${t.languages.join(', ')}` : ''}</span>
              </span>
            </button>
          ))}
          {found.length > SHOWN && <p className="px-2.5 py-2 text-[12px] text-dim">{found.length - SHOWN} more — search to narrow the list.</p>}
          {found.length === 0 && !typed && <p className="px-2.5 py-3 text-[12.5px] text-dim">The catalogue is empty.</p>}
        </div>
      </PopoverContent>
    </Popover>
  );
}

/** Several technologies — a layer's alternatives, the tools of a section — as removable chips and a picker. */
export function TechChips({ values, onChange, tech, category, label }: {
  values: string[];
  onChange: (ids: string[]) => void;
  tech: Tech[];
  category?: TechCategory;
  label: string;
}) {
  const byId = useMemo(() => new Map(tech.map((t) => [t.id, t])), [tech]);
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {values.map((id) => (
        <span key={id} className="inline-flex items-center gap-1 rounded-full border border-line bg-surface-2/60 py-0.5 pr-1 pl-2.5 text-[12.5px] text-ink-2">
          {byId.get(id)?.name ?? id}
          <button onClick={() => onChange(values.filter((v) => v !== id))} aria-label={`Remove ${byId.get(id)?.name ?? id}`}
            className="grid size-5 place-items-center rounded-full text-dim hover:bg-surface-3 hover:text-ink">
            <X className="size-3" />
          </button>
        </span>
      ))}
      <TechPicker value={null} onChange={(id) => { if (id && !values.includes(id)) onChange([...values, id]); }}
        tech={tech} category={category} placeholder="Add…" label={label}
        className="h-7 w-auto min-w-[96px] rounded-full px-2.5 text-[12.5px]" />
    </div>
  );
}
