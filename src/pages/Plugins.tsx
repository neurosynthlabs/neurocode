import { useMemo, useState } from 'react';
import { Search, Star, Download, Trash2, TriangleAlert, Blocks, Copy, Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import { useData, usePref } from '@/lib/data';
import { useAuth } from '@/lib/auth';
import { useProject } from '@/lib/project-context';
import { useRemote } from '@/lib/remote';
import { extensions, type LivePlugin } from '@/lib/live/extensions';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/switch';
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from '@/components/ui/sheet';
import {
  Page, PageHeader, PageBody, Panel, Tag, Mono, Toolbar, Field, SelectField, Segmented,
  Stat, StatGrid, KV, Empty, SectionTitle,
} from '@/components/os';
import { plugins, pluginConflicts } from '@/mock/commands';
import type { Plugin } from '@/types';
import { ago } from './code/format';

const PLUGINS_INSTALLED: Record<string, boolean> = Object.fromEntries(plugins.map((p) => [p.id, p.installed]));

/** With the local API, the plugins Claude Code really installed; in the demo, the worked example. */
export default function Plugins() {
  const { mode } = useData();
  return mode === 'live' ? <LivePlugins /> : <SamplePlugins />;
}

function SamplePlugins() {
  const [tab, setTab] = useState<'installed' | 'market'>('installed');
  const [q, setQ] = useState('');
  const [cat, setCat] = useState('all');
  const [open, setOpen] = useState<Plugin | null>(null);
  const [inst, setInst] = usePref('plugins.installed', PLUGINS_INSTALLED);

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
          <span className="ml-auto text-[12.5px] text-dim">{list.length} shown</span>
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
                    <span className="text-[12px] text-dim">vs</span>
                    <Tag tone="neutral">{c.b}</Tag>
                  </div>
                  <p className="mt-1 text-[12.5px] text-soft">{c.resolution}</p>
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
                actions={<span className="flex items-center gap-1 text-[12px] text-dim"><Star className="size-3" />{p.stars.toLocaleString()}</span>}>
                <div onClick={() => setOpen(p)}>
                  <p className="line-clamp-3 min-h-[48px] text-[12.5px] leading-relaxed text-soft">{p.description}</p>
                  <div className="mt-2.5 flex flex-wrap gap-1 border-t border-line pt-2.5">
                    {(['agents', 'skills', 'commands', 'hooks', 'mcp'] as const).map((k) => p.provides[k] > 0 && (
                      <span key={k} className="rounded-xs border border-line bg-surface-2 px-1.5 py-px text-[11.5px] text-ink-2">
                        {p.provides[k]} {k}
                      </span>
                    ))}
                  </div>
                </div>
                <div className="mt-2.5 flex items-center gap-1.5">
                  {inst[p.id] ? (
                    <Button size="xs" variant="destructive" onClick={() => { setInst({ ...inst, [p.id]: false }, `Plugin ${p.name} uninstalled`); toast(`${p.name} uninstalled`, { description: 'Its skills, commands and hooks were removed.' }); }}>
                      <Trash2 className="size-3" />Uninstall
                    </Button>
                  ) : (
                    <Button size="xs" onClick={() => { setInst({ ...inst, [p.id]: true }, `Plugin ${p.name} installed`); toast.success(`${p.name} installed`, { description: `${p.provides.skills} skills, ${p.provides.commands} commands, ${p.provides.hooks} hooks registered.` }); }}>
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
                <SheetDescription className="text-[13px] text-soft">
                  {open.publisher} · {open.stars.toLocaleString()} stars · updated {open.updatedAt}
                </SheetDescription>
              </SheetHeader>
              <div className="space-y-3 p-5">
                <p className="text-[13.5px] leading-relaxed text-ink-2">{open.description}</p>
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
                <ul className="space-y-1 text-[12.5px] text-soft">
                  <li className="flex gap-1.5"><span className="mt-1.5 size-1 shrink-0 rounded-full bg-brand" />Skills register disabled and load only when their trigger matches.</li>
                  <li className="flex gap-1.5"><span className="mt-1.5 size-1 shrink-0 rounded-full bg-brand" />Hooks that block a tool call require your confirmation before they arm.</li>
                  <li className="flex gap-1.5"><span className="mt-1.5 size-1 shrink-0 rounded-full bg-brand" />MCP servers arrive disconnected with every tool set to ask.</li>
                  <li className="flex gap-1.5"><span className="mt-1.5 size-1 shrink-0 rounded-full bg-warn" />A command name already taken by another plugin raises a conflict instead of silently overriding.</li>
                </ul>
                <div className="flex gap-2 border-t border-line pt-3">
                  {inst[open.id]
                    ? <Button size="sm" variant="destructive" onClick={() => { setInst({ ...inst, [open.id]: false }, `Plugin ${open.name} uninstalled`); setOpen(null); toast(`${open.name} uninstalled`); }}><Trash2 className="size-3.5" />Uninstall</Button>
                    : <Button size="sm" onClick={() => { setInst({ ...inst, [open.id]: true }, `Plugin ${open.name} installed`); setOpen(null); toast.success(`${open.name} installed`); }}><Download className="size-3.5" />Install</Button>}
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

/* Live: the plugins Claude Code installed on this machine and the marketplaces it knows. NeuroCode does not
   install, fetch or remove plugins — that would mean pulling third-party code and arming its hooks inside
   Claude Code's own configuration — so Install and Uninstall become a command to copy. What it does own is
   whether an installed plugin's skills and commands appear in NeuroCode sessions. */

const NONE_OFF: Record<string, boolean> = {};
const KINDS = ['agents', 'skills', 'commands', 'hooks', 'mcp'] as const;

async function copyCommand(p: LivePlugin) {
  try {
    await navigator.clipboard.writeText(p.installCommand);
    toast('Copied', { description: `Run ${p.installCommand} in Claude Code.` });
  } catch {
    toast(p.installCommand, { description: 'The clipboard was not available. Run this in Claude Code.' });
  }
}

function LivePlugins() {
  const { can } = useAuth();
  const { project } = useProject();
  const [tab, setTab] = useState<'installed' | 'market'>('installed');
  const [q, setQ] = useState('');
  const [cat, setCat] = useState('all');
  const [openId, setOpenId] = useState<string | null>(null);
  const [use, setUse] = usePref('plugins.installed', NONE_OFF);
  const r = useRemote(`plugins:${project.id}`, () => extensions.plugins(project.id));

  const installed = useMemo(() => r.data?.installed ?? [], [r.data]);
  const market = useMemo(() => r.data?.marketplace ?? [], [r.data]);
  const shown = tab === 'installed' ? installed : market;
  const cats = useMemo(() => ['all', ...Array.from(new Set(shown.map((p) => p.category))).sort()], [shown]);
  const list = useMemo(() => {
    const t = q.trim().toLowerCase();
    return shown.filter((p) => (cat === 'all' || p.category === cat) && (!t || (p.name + p.description + p.publisher).toLowerCase().includes(t)));
  }, [shown, q, cat]);
  const totals = useMemo(() => installed.filter((p) => p.enabledInClaude).reduce(
    (a, p) => ({ agents: a.agents + p.provides.agents, skills: a.skills + p.provides.skills, commands: a.commands + p.provides.commands, hooks: a.hooks + p.provides.hooks, mcp: a.mcp + p.provides.mcp }),
    { agents: 0, skills: 0, commands: 0, hooks: 0, mcp: 0 }), [installed]);
  const open = [...installed, ...market].find((p) => p.id === openId) ?? null;
  const fetched = r.data?.countsFetchedAt;
  const isUsed = (id: string) => use[id] !== false;
  const setUsed = (p: LivePlugin, v: boolean) => {
    setUse({ ...use, [p.id]: v }, `Plugin ${p.name} ${v ? 'used' : 'kept out of'} NeuroCode sessions`);
    toast(`${p.name} ${v ? 'used in' : 'kept out of'} NeuroCode sessions`, { description: 'Claude Code is not affected.' });
  };

  return (
    <Page>
      <PageHeader
        title="Plugins"
        subtitle="The plugins Claude Code installed on this machine, and what its marketplaces offer. NeuroCode reads them and never installs, updates or runs one — its skills and commands reach sessions only while you let them."
        actions={<Segmented options={[{ id: 'installed', label: `Installed (${installed.length})` }, { id: 'market', label: `Marketplace (${market.length})` }]} value={tab} onChange={(v) => { setTab(v); setCat('all'); }} />}
      >
        <Toolbar>
          <Field className="w-64" value={q} onChange={setQ} icon={<Search className="size-3.5" />} placeholder="Search plugins, publishers…" onClear={() => setQ('')} />
          <SelectField className="w-44" value={cat} onChange={setCat} options={cats.map((c) => ({ value: c, label: c === 'all' ? 'Any category' : c }))} />
          <span className="ml-auto text-[12.5px] text-dim">{list.length} shown</span>
        </Toolbar>
      </PageHeader>

      {r.error ? (
        <PageBody><Empty title="The plugins did not load" hint={r.error} action={<Button size="sm" variant="outline" onClick={r.reload}>Try again</Button>} /></PageBody>
      ) : !r.data ? (
        <PageBody><Empty icon={<Loader2 className="size-5 animate-spin" />} title="Reading Claude Code's plugins…" /></PageBody>
      ) : (
        <PageBody className="space-y-4">
          <StatGrid cols={5}>
            <Stat label="Agents added" value={totals.agents} icon={<Blocks className="size-3" />} sub="by plugins enabled in Claude Code" />
            <Stat label="Skills added" value={totals.skills} tone="brand" />
            <Stat label="Commands added" value={totals.commands} />
            <Stat label="Hooks added" value={totals.hooks} tone="warn" sub="never run by NeuroCode" />
            <Stat label="MCP servers" value={totals.mcp} tone="ok" />
          </StatGrid>

          {r.data.conflicts.length > 0 && tab === 'installed' && (
            <Panel className="border-warn/35" eyebrow="The same command name in more than one place" title={<span className="flex items-center gap-1.5"><TriangleAlert className="size-3.5 text-warn" />Conflicts</span>} flush>
              <div className="divide-y divide-line">
                {r.data.conflicts.map((c) => (
                  <div key={c.command} className="px-3.5 py-2.5">
                    <div className="flex flex-wrap items-center gap-2">
                      <Mono tone="brand">{c.command}</Mono><Tag tone="neutral">{c.a}</Tag><span className="text-[12px] text-dim">over</span><Tag tone="neutral">{c.b}</Tag>
                    </div>
                    <p className="mt-1 text-[12.5px] text-soft">{c.resolution}</p>
                  </div>
                ))}
              </div>
            </Panel>
          )}

          {list.length === 0 ? (
            <Empty title={tab === 'installed' ? (installed.length ? 'Nothing installed in this category' : 'Claude Code has no plugins installed') : (market.length ? 'Nothing in this category' : 'No marketplace is known on this machine')}
              hint={tab === 'installed' && !installed.length ? 'Install one in Claude Code with /plugin install, and it shows up here.' : 'Switch tabs or widen the category filter.'} />
          ) : (
            <div className="grid grid-cols-1 gap-3 stagger lg:grid-cols-2 2xl:grid-cols-3">
              {list.map((p) => (
                <Panel key={p.id} className="hover-lift cursor-pointer" eyebrow={`${p.publisher}${p.version ? ` · v${p.version}` : ''}${p.updatedAt ? ` · ${ago(p.updatedAt)}` : ''}`}
                  title={<span className="flex items-center gap-2">{p.name}<Tag tone="neutral">{p.category}</Tag></span>}
                  actions={p.stars > 0 && <span className="flex items-center gap-1 text-[12px] text-dim"><Download className="size-3" />{p.stars.toLocaleString()} installs</span>}>
                  <div onClick={() => setOpenId(p.id)}>
                    <p className="line-clamp-3 min-h-[48px] text-[12.5px] leading-relaxed text-soft">{p.description || 'No description in its marketplace entry.'}</p>
                    <div className="mt-2.5 flex flex-wrap gap-1 border-t border-line pt-2.5">
                      {!p.providesKnown ? <span className="text-[11.5px] text-dim">contents not on this machine</span>
                        : KINDS.every((k) => p.provides[k] === 0) ? <span className="text-[11.5px] text-dim">no agents, skills, commands, hooks or MCP servers</span>
                          : KINDS.map((k) => p.provides[k] > 0 && (
                            <span key={k} className="rounded-xs border border-line bg-surface-2 px-1.5 py-px text-[11.5px] text-ink-2">{p.provides[k]} {k}</span>
                          ))}
                    </div>
                  </div>
                  <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
                    {p.installed && (
                      <label className="mr-1 flex items-center gap-1.5 text-[12px] text-soft">
                        <Switch size="sm" checked={isUsed(p.id)} disabled={!can('settings:write') || !p.enabledInClaude} onCheckedChange={(v) => setUsed(p, v)} />
                        {p.enabledInClaude ? 'Use in NeuroCode' : 'Disabled in Claude Code'}
                      </label>
                    )}
                    <Button size="xs" variant="outline" onClick={() => void copyCommand(p)}><Copy className="size-3" />{p.installed ? 'Uninstall command' : 'Install command'}</Button>
                    <Button size="xs" variant="ghost" onClick={() => setOpenId(p.id)}>Details</Button>
                  </div>
                </Panel>
              ))}
            </div>
          )}
        </PageBody>
      )}

      <Sheet open={!!open} onOpenChange={(o) => !o && setOpenId(null)}>
        <SheetContent side="right" className="w-[520px] gap-0 overflow-y-auto p-0 sm:max-w-none">
          {open && (
            <>
              <SheetHeader className="border-b border-line px-5 pt-5 pb-4">
                <div className="flex flex-wrap items-center gap-2">
                  <Tag tone="neutral">{open.category}</Tag>
                  {open.version && <Mono>v{open.version}</Mono>}
                  {open.installed && <Tag tone="ok">installed</Tag>}
                  {open.installed && <Tag tone={open.enabledInClaude ? 'ok' : 'neutral'}>{open.enabledInClaude ? 'enabled in Claude Code' : 'disabled in Claude Code'}</Tag>}
                </div>
                <SheetTitle className="mt-2 text-[16px] text-ink">{open.name}</SheetTitle>
                <SheetDescription className="text-[13px] text-soft">
                  {open.publisher}
                  {open.stars > 0 && ` · ${open.stars.toLocaleString()} installs${fetched ? ` as of ${new Date(fetched).toLocaleDateString()}` : ''}`}
                  {open.updatedAt && ` · updated ${ago(open.updatedAt)}`}
                </SheetDescription>
              </SheetHeader>
              <div className="space-y-3 p-5">
                <p className="text-[13.5px] leading-relaxed text-ink-2">{open.description || 'No description in its marketplace entry.'}</p>
                <Panel eyebrow={open.providesKnown ? 'Counted from its files' : 'Its files are not on this machine'} title="Contributions" flush>
                  <div className="px-3.5 py-1.5">
                    <KV k="Agents" v={open.providesKnown ? open.provides.agents || '—' : '?'} />
                    <KV k="Skills" v={open.providesKnown ? open.provides.skills || '—' : '?'} />
                    <KV k="Commands" v={open.providesKnown ? open.provides.commands || '—' : '?'} />
                    <KV k="Hooks" v={open.providesKnown ? open.provides.hooks || '—' : '?'} />
                    <KV k="MCP servers" v={open.providesKnown ? open.provides.mcp || '—' : '?'} />
                  </div>
                </Panel>
                <SectionTitle>In NeuroCode</SectionTitle>
                <ul className="space-y-1 text-[12.5px] text-soft">
                  {open.path && <li className="flex gap-1.5"><span className="mt-1.5 size-1 shrink-0 rounded-full bg-brand" /><span>Installed at <Mono>{open.path}</Mono></span></li>}
                  <li className="flex gap-1.5"><span className="mt-1.5 size-1 shrink-0 rounded-full bg-brand" />Its hooks are never run by NeuroCode.</li>
                  <li className="flex gap-1.5"><span className="mt-1.5 size-1 shrink-0 rounded-full bg-brand" />
                    {open.installed
                      ? `Its skills and commands appear in NeuroCode sessions only while it is enabled in Claude Code and Use in NeuroCode is on${open.enabledInClaude ? (isUsed(open.id) ? ' — it is.' : ' — it is off.') : ' — it is disabled in Claude Code.'}`
                      : 'Installing happens in Claude Code. Once it is installed and enabled there, it shows up here.'}
                  </li>
                </ul>
                <div className="flex flex-wrap gap-2 border-t border-line pt-3">
                  <Button size="sm" onClick={() => void copyCommand(open)}><Copy className="size-3.5" />Copy {open.installed ? 'uninstall' : 'install'} command</Button>
                  <Button size="sm" variant="outline" onClick={() => setOpenId(null)}>Close</Button>
                </div>
              </div>
            </>
          )}
        </SheetContent>
      </Sheet>
    </Page>
  );
}
