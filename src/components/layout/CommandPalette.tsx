import { useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { Circle } from 'lucide-react';
import { ICONS } from '@/lib/icons';
import {
  Command, CommandDialog, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList, CommandSeparator,
} from '@/components/ui/command';
import { buildIndex, SEARCH_GROUPS } from '@/lib/search';
import { Kbd } from '@/components/os';
import { useData } from '@/lib/data';

function Icon({ name }: { name: string }) {
  const C = ICONS[name] ?? Circle;
  return <C className="size-3.5 shrink-0 text-dim" />;
}

export function CommandPalette({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  const nav = useNavigate();
  const { projects, tasks, plans, memory, mcp } = useData();
  const index = useMemo(() => buildIndex({ projects, tasks, plans, memory, mcp }), [projects, tasks, plans, memory, mcp]);
  const grouped = useMemo(
    () => SEARCH_GROUPS.map((g) => ({ group: g, items: index.filter((i) => i.group === g) })).filter((g) => g.items.length),
    [index],
  );

  return (
    <CommandDialog open={open} onOpenChange={onOpenChange} className="sm:max-w-[620px]">
      <Command className="rounded-none! bg-transparent p-0">
      <CommandInput placeholder="Search everything — code, memory, tasks, decisions, commits, tests…" />
      <CommandList className="max-h-[440px]">
        <CommandEmpty>
          <div className="py-6 text-center">
            <p className="text-[13px] text-ink-2">Nothing indexed under that term.</p>
            <p className="mt-1 text-[11.5px] text-dim">Try a module, a stored procedure, a decision ref, or a bug id.</p>
          </div>
        </CommandEmpty>
        {grouped.map((g, gi) => (
          <div key={g.group}>
            {gi > 0 && <CommandSeparator />}
            <CommandGroup heading={g.group}>
              {g.items.map((hit) => (
                <CommandItem
                  key={hit.id}
                  value={`${hit.group} ${hit.title} ${hit.subtitle} ${hit.meta ?? ''}`}
                  onSelect={() => { nav(hit.to); onOpenChange(false); }}
                  className="gap-2.5"
                >
                  <Icon name={hit.icon} />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[12.5px] text-ink">{hit.title}</span>
                    <span className="block truncate text-[11px] text-dim">{hit.subtitle}</span>
                  </span>
                  {hit.meta && <span className="eyebrow shrink-0">{hit.meta}</span>}
                </CommandItem>
              ))}
            </CommandGroup>
          </div>
        ))}
      </CommandList>
      <div className="flex items-center justify-between border-t border-line px-3 py-2 text-[11px] text-dim">
        <span className="flex items-center gap-1.5"><Kbd>↑</Kbd><Kbd>↓</Kbd> navigate <Kbd>↵</Kbd> open <Kbd>esc</Kbd> close</span>
        <span>{index.length} items · tasks, plans, memory and projects are live</span>
      </div>
      </Command>
    </CommandDialog>
  );
}
