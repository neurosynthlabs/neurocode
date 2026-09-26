import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Circle } from 'lucide-react';
import { ICONS } from '@/lib/icons';
import {
  Command, CommandDialog, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList, CommandSeparator,
} from '@/components/ui/command';
import { buildIndex, codeHits, SEARCH_GROUPS } from '@/lib/search';
import { sessionHits } from '@/components/sessions/transcript';
import type { SessionDoc } from '@/lib/api';
import { Kbd } from '@/components/os';
import { ApiError, api } from '@/lib/api';
import { useData } from '@/lib/data';
import { useAuth } from '@/lib/auth';
import { useAccess } from '@/lib/access';
import { useProject } from '@/lib/project-context';
import type { SearchHit } from '@/types';

/** How long typing has to pause before the code index is asked, so a word is one request, not five. */
const PAUSE_MS = 220;

/* The groups, in order. Sessions are not in the store — no screen but Sessions needs the list — so the
   palette reads them itself when it opens, and they sit with the work they came out of. They are found
   by their reference, their title and their project; what was *said* in one is searched inside the
   session, which is the only place that holds its turns. */
const GROUPS = SEARCH_GROUPS.flatMap((group) => (group === 'Runs' ? [group, 'Sessions'] : [group]));

function Icon({ name }: { name: string }) {
  const C = ICONS[name] ?? Circle;
  return <C className="size-3.5 shrink-0 text-dim" />;
}

type Code = { pid: string; q: string; hits: SearchHit[]; error: string | null };

export function CommandPalette({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  const nav = useNavigate();
  const { projects, tasks, plans, runs, memory, brainstorms, mcp } = useData();
  const { agents } = useAccess();
  const { canAny } = useAuth();
  const { project } = useProject();
  const [query, setQuery] = useState('');
  const [code, setCode] = useState<Code>({ pid: '', q: '', hits: [], error: null });
  const [sessions, setSessions] = useState<SessionDoc[] | null>(null);
  const index = useMemo(
    () => buildIndex({ projects, tasks, plans, runs, memory, brainstorms, mcp, agents }, canAny),
    [projects, tasks, plans, runs, memory, brainstorms, mcp, agents, canAny],
  );

  // The sessions are read once each time the palette opens, so one started a minute ago is findable
  // and one someone else deleted is not. A failure leaves the group out and says so in the console.
  useEffect(() => {
    if (!open) return;
    let current = true;
    api.sessions().then(
      (all) => { if (current) setSessions(all); },
      (e: unknown) => { console.warn('[NeuroCode] the sessions were not read for ⌘K:', e); },
    );
    return () => { current = false; };
  }, [open]);

  // Symbols live in the active project's code index, which only the server can search. A project that
  // was never indexed has nothing to ask, so it is not asked.
  const indexed = project?.codeIndex ? { id: project.id, name: project.name } : null;
  const { id: pid, name: pname } = indexed ?? { id: null, name: '' };
  const q = query.trim();
  useEffect(() => {
    if (!open || !pid || q.length < 2) return;
    let current = true;
    const timer = setTimeout(() => {
      api.code.search(pid, q).then(
        (hits) => { if (current) setCode({ pid, q, hits: codeHits(pname, hits), error: null }); },
        (e: unknown) => {
          console.warn('[NeuroCode] code search failed:', e);
          if (current) setCode({ pid, q, hits: [], error: e instanceof ApiError ? e.message : 'The local API did not answer.' });
        },
      );
    }, PAUSE_MS);
    return () => { current = false; clearTimeout(timer); };
  }, [open, pid, pname, q]);

  // An answer for an earlier query, or another project, never stands in for this one.
  const fresh = pid && q.length >= 2 && code.pid === pid && code.q === q ? code : null;
  const mine = useMemo(() => sessionHits(sessions ?? []), [sessions]);
  const hits = useMemo(() => [...index, ...mine, ...(fresh ? fresh.hits : [])], [index, mine, fresh]);
  const grouped = useMemo(
    () => GROUPS.map((g) => ({ group: g, items: hits.filter((i) => i.group === g) })).filter((g) => g.items.length),
    [hits],
  );

  const change = (v: boolean) => {
    if (!v) setQuery('');
    onOpenChange(v);
  };

  return (
    <CommandDialog open={open} onOpenChange={change} className="sm:max-w-[620px]">
      <Command className="rounded-none! bg-transparent p-0">
      <CommandInput
        value={query} onValueChange={setQuery}
        placeholder={indexed ? `Search screens, work, memory and ${indexed.name} symbols…` : 'Search screens, work, sessions and memory…'}
      />
      <CommandList className="max-h-[440px]">
        <CommandEmpty>
          <div className="py-6 text-center">
            <p className="text-[14px] text-ink-2">Nothing matches yet.</p>
            <p className="mt-1 text-[12.5px] text-dim">
              Your work appears here as you create it.
              {indexed ? ` Symbols come from ${indexed.name}’s code index.` : ''}
            </p>
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
                  onSelect={() => { nav(hit.to); change(false); }}
                  className="gap-2.5"
                >
                  <Icon name={hit.icon} />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[13.5px] text-ink">{hit.title}</span>
                    <span className="block truncate text-[12px] text-dim">{hit.subtitle}</span>
                  </span>
                  {hit.meta && <span className="shrink-0 text-[11px] font-medium text-dim">{hit.meta}</span>}
                </CommandItem>
              ))}
            </CommandGroup>
          </div>
        ))}
      </CommandList>
      <div className="flex items-center justify-between gap-3 border-t border-line px-3 py-2 text-[12px] text-dim">
        <span className="flex shrink-0 items-center gap-1.5"><Kbd>↑</Kbd><Kbd>↓</Kbd> navigate <Kbd>↵</Kbd> open <Kbd>esc</Kbd> close</span>
        <span className="truncate">
          {fresh?.error ? `Code search failed: ${fresh.error}` : `${hits.length} items`}
        </span>
      </div>
      </Command>
    </CommandDialog>
  );
}
