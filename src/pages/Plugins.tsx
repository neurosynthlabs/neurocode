import { useMemo, useState } from 'react';
import { Search, Star, Download, Trash2, TriangleAlert, Blocks } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from '@/components/ui/sheet';
import {
  Page, PageHeader, PageBody, Panel, Tag, Mono, Toolbar, Field, SelectField, Segmented,
  Stat, StatGrid, KV, Empty, SectionTitle,
} from '@/components/os';
import { plugins, pluginConflicts } from '@/mock/commands';
import type { Plugin } from '@/types';

export default function Plugins() {
  const [tab, setTab] = useState<'installed' | 'market'>('installed');
  const [q, setQ] = useState('');
  const [cat, setCat] = useState('all');
  const [open, setOpen] = useState<Plugin | null>(null);
  const [inst, setInst] = useState<Record<string, boolean>>(() => Object.fromEntries(plugins.map((p) => [p.id, p.installed])));

  const cats = useMemo(() => ['all', ...Array.from(new Set(plugins.map((p) => p.category)))], []);
  const list = useMemo(() => {
    const t = q.trim().toLowerCase();
    return plugins.filter((p) =>
      (tab === 'installed' ? inst[p.id] : !inst[p.id]) &&
      (cat === 'all' || p.category === cat) &&
      (!t || (p.name + p.description + p.publisher).toLowerCase().includes(t)));
  }, [tab, q, cat, inst]);

  const totals = useMemo(() => plugins.filter((p) => inst[p.id]).reduce(
    (a, p) => ({ agents: a.agents + p.provides.agents, skills: a.skills + p.provides.skills, commands: a.commands + p.provides.commands, hooks: a.hooks + p.provides.hooks, mcp: a.mcp + p.provides.mcp }),
    { agents: 0, skills: 0, commands: 0, hooks: 0, mcp: 0 }), [inst]);

  return (
    <Page>
      <PageHeader
        title="Plugins"
        subtitle="A plugin is a bundle — agents, skills, commands, hooks and MCP servers shipped together, versioned together, removable together."
        actions={<Segmented options={[{ id: 'installed', label: `Installed (${plugins.filter((p) => inst[p.id]).length})` }, { id: 'market', label: `Marketplace (${plugins.filter((p) => !inst[p.id]).length})` }]} value={tab} onChange={setTab} />}
      >
        <Toolbar>
          <Field className="w-64" value={q} onChange={setQ} icon={<Search className="size-3.5" />} placeholder="Search plugins, publishers…" onClear={() => setQ('')} />
          <SelectField className="w-44" value={cat} onChange={setCat} options={cats.map((c) => ({ value: c, label: c === 'all' ? 'Any category' : c }))} />
          <span className="ml-auto text-[11.5px] text-dim">{list.length} shown</span>
        </Toolbar>
      </PageHeader>

      <PageBody className="space-y-4">
        <StatGrid cols={5}>
          <Stat label="Agents added" value={totals.agents} icon={<Blocks className="size-3" />} />
          <Stat label="Skills added" value={totals.skills} tone="brand" />
          <Stat label="Commands added" value={totals.commands} />
          <Stat label="Hooks added" value={totals.hooks} tone="warn" />
          <Stat label="MCP servers" value={totals.mcp} tone="ok" />
        </StatGrid>

        {pluginConflicts.length > 0 && tab === 'installed' && (
          <Panel className="border-warn/35" eyebrow="Two plugins claim the same command" title={<span className="flex items-center gap-1.5"><TriangleAlert className="size-3.5 text-warn" />Conflicts</span>} flush>
            <div className="divide-y divide-line">
              {pluginConflicts.map((c) => (
                <div key={c.command} className="px-3.5 py-2.5">
                  <div className="flex flex-wrap items-center gap-2">
                    <Mono tone="brand">{c.command}</Mono>
                    <Tag tone="neutral">{c.a}</Tag>
                    <span className="text-[11px] text-dim">vs</span>
                    <Tag tone="neutral">{c.b}</Tag>
                  </div>
                  <p className="mt-1 text-[11.5px] text-soft">{c.resolution}</p>
                </div>
              ))}
            </div>
          </Panel>
        )}

        {list.length === 0 ? (
          <Empty title={tab === 'installed' ? 'Nothing installed in this category' : 'Nothing left to install here'}
            hint="Switch tabs or widen the category filter." />
        ) : (
          <div className="grid grid-cols-1 gap-3 stagger lg:grid-cols-2 2xl:grid-cols-3">
            {list.map((p) => (
              <Panel key={p.id} className="hover-lift cursor-pointer" eyebrow={`${p.publisher} · v${p.version} · ${p.updatedAt}`}
                title={<span className="flex items-center gap-2">{p.name}<Tag tone="neutral">{p.category}</Tag></span>}
                actions={<span className="flex items-center gap-1 text-[11px] text-dim"><Star className="size-3" />{p.stars.toLocaleString()}</span>}>
                <div onClick={() => setOpen(p)}>
                  <p className="line-clamp-3 min-h-[48px] text-[11.5px] leading-relaxed text-soft">{p.description}</p>
                  <div className="mt-2.5 flex flex-wrap gap-1 border-t border-line pt-2.5">
                    {(['agents', 'skills', 'commands', 'hooks', 'mcp'] as const).map((k) => p.provides[k] > 0 && (
                      <span key={k} className="rounded-xs border border-line bg-surface-2 px-1.5 py-px text-[10.5px] text-ink-2">
                        {p.provides[k]} {k}
                      </span>
                    ))}
                  </div>
                </div>
                <div className="mt-2.5 flex items-center gap-1.5">
                  {inst[p.id] ? (
                    <Button size="xs" variant="destructive" onClick={() => { setInst((m) => ({ ...m, [p.id]: false })); toast(`${p.name} uninstalled`, { description: 'Its skills, commands and hooks were removed.' }); }}>
                      <Trash2 className="size-3" />Uninstall
                    </Button>
                  ) : (
                    <Button size="xs" onClick={() => { setInst((m) => ({ ...m, [p.id]: true })); toast.success(`${p.name} installed`, { description: `${p.provides.skills} skills, ${p.provides.commands} commands, ${p.provides.hooks} hooks registered.` }); }}>
                      <Download className="size-3" />Install
                    </Button>
                  )}
                  <Button size="xs" variant="ghost" onClick={() => setOpen(p)}>Details</Button>
                </div>
              </Panel>
            ))}
          </div>
        )}
      </PageBody>

      <Sheet open={!!open} onOpenChange={(o) => !o && setOpen(null)}>
        <SheetContent side="right" className="w-[520px] gap-0 overflow-y-auto p-0 sm:max-w-none">
          {open && (
            <>
              <SheetHeader className="border-b border-line px-5 pt-5 pb-4">
                <div className="flex flex-wrap items-center gap-2">
                  <Tag tone="neutral">{open.category}</Tag>
                  <Mono>v{open.version}</Mono>
                  {inst[open.id] && <Tag tone="ok">installed</Tag>}
                </div>
                <SheetTitle className="mt-2 text-[16px] text-ink">{open.name}</SheetTitle>
                <SheetDescription className="text-[12px] text-soft">
                  {open.publisher} · {open.stars.toLocaleString()} stars · updated {open.updatedAt}
                </SheetDescription>
              </SheetHeader>
              <div className="space-y-3 p-5">
                <p className="text-[12.5px] leading-relaxed text-ink-2">{open.description}</p>
                <Panel eyebrow="What it registers" title="Contributions" flush>
                  <div className="px-3.5 py-1.5">
                    <KV k="Agents" v={open.provides.agents || '—'} />
                    <KV k="Skills" v={open.provides.skills || '—'} />
                    <KV k="Commands" v={open.provides.commands || '—'} />
                    <KV k="Hooks" v={open.provides.hooks || '—'} />
                    <KV k="MCP servers" v={open.provides.mcp || '—'} />
                  </div>
                </Panel>
                <SectionTitle>On install</SectionTitle>
                <ul className="space-y-1 text-[11.5px] text-soft">
                  <li className="flex gap-1.5"><span className="mt-1.5 size-1 shrink-0 rounded-full bg-brand" />Skills register disabled and load only when their trigger matches.</li>
                  <li className="flex gap-1.5"><span className="mt-1.5 size-1 shrink-0 rounded-full bg-brand" />Hooks that block a tool call require your confirmation before they arm.</li>
                  <li className="flex gap-1.5"><span className="mt-1.5 size-1 shrink-0 rounded-full bg-brand" />MCP servers arrive disconnected with every tool set to ask.</li>
                  <li className="flex gap-1.5"><span className="mt-1.5 size-1 shrink-0 rounded-full bg-warn" />A command name already taken by another plugin raises a conflict instead of silently overriding.</li>
                </ul>
                <div className="flex gap-2 border-t border-line pt-3">
                  {inst[open.id]
                    ? <Button size="sm" variant="destructive" onClick={() => { setInst((m) => ({ ...m, [open.id]: false })); setOpen(null); toast(`${open.name} uninstalled`); }}><Trash2 className="size-3.5" />Uninstall</Button>
                    : <Button size="sm" onClick={() => { setInst((m) => ({ ...m, [open.id]: true })); setOpen(null); toast.success(`${open.name} installed`); }}><Download className="size-3.5" />Install</Button>}
                  <Button size="sm" variant="outline" onClick={() => setOpen(null)}>Close</Button>
                </div>
              </div>
            </>
          )}
        </SheetContent>
      </Sheet>
    </Page>
  );
}
