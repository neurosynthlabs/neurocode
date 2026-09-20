import { useMemo, useState } from 'react';
import { Search, Download, TriangleAlert, Blocks, Copy, Loader2, Trash2, FolderDown, Plus } from 'lucide-react';
import { toast } from 'sonner';
import { usePref } from '@/lib/data';
import { useAuth } from '@/lib/auth';
import { useProject } from '@/lib/project-context';
import { useRemote } from '@/lib/remote';
import {
  extensionsAdmin, EXTENSIONS_PERMISSION, type PluginCard, type RegistryEntry,
} from '@/lib/live/work';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/switch';
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from '@/components/ui/sheet';
import {
  Page, PageHeader, PageBody, Panel, Tag, Mono, Toolbar, Field, SelectField, Segmented,
  Stat, StatGrid, KV, Empty, SectionTitle,
} from '@/components/os';
import { ago } from './code/format';

/** The plugins Claude Code really installed on this machine, and the marketplaces it knows. */
export default function Plugins() {
  return <LivePlugins />;
}

/* Three lists, and the difference between them is who owns the folder.

   Claude Code's own installs are read and never written: installing into its cache would arm somebody
   else's hooks inside its configuration, so for those, Install and Uninstall stay a command to copy.

   This workspace's own plugin folder is different — NeuroCode put those there, from an https git URL or a
   folder on this machine, and it can list what each one brought and remove it again. Their skills and
   commands reach NeuroCode sessions; their hooks are refused like every other hook until a rule allows one.

   Offered is a registry: a JSON file on this machine listing plugins somebody wrote down as worth having.
   Nothing is fetched from anywhere and there is no index to browse — the screen says so in those words,
   because calling a file a marketplace would be the one thing this product does not do. */

const NONE_OFF: Record<string, boolean> = {};
const KINDS = ['agents', 'skills', 'commands', 'hooks', 'mcp'] as const;

async function copyCommand(p: PluginCard) {
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
  const pid = project?.id ?? null;
  const [tab, setTab] = useState<'installed' | 'market' | 'registry'>('installed');
  const [q, setQ] = useState('');
  const [cat, setCat] = useState('all');
  const [openId, setOpenId] = useState<string | null>(null);
  const [use, setUse] = usePref('plugins.installed', NONE_OFF);
  const [source, setSource] = useState('');
  const [name, setName] = useState('');
  const [busy, setBusy] = useState('');
  const r = useRemote(`plugins:${pid ?? ''}`, () => extensionsAdmin.plugins(pid));

  const installed = useMemo(() => r.data?.installed ?? [], [r.data]);
  const market = useMemo(() => r.data?.marketplace ?? [], [r.data]);
  const registry = r.data?.registry ?? null;
  // The search bar filters this list too; the category filter does not apply to it, so it is hidden there.
  const offers = useMemo(() => {
    const text = q.trim().toLowerCase();
    return (registry?.plugins ?? []).filter(
      (o) => !text || (o.name + o.description + o.publisher).toLowerCase().includes(text));
  }, [registry, q]);
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
  const mine = installed.filter((p) => p.workspace);

  /** Bring one in. The server decides what a source may be; this only says what happened. */
  const install = async (from: string, called: string) => {
    setBusy(from);
    try {
      const made = await extensionsAdmin.installPlugin(from.trim(), called.trim().toLowerCase());
      const brought = Object.entries(made.provides).filter(([, n]) => n > 0)
        .map(([k, n]) => `${n} ${k}`).join(', ');
      toast(`${made.name} installed`, { description: brought ? `It brought ${brought}.` : 'It brought nothing yet.' });
      setSource(''); setName('');
      r.reload();
    } catch (e) {
      toast('It was not installed', { description: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy('');
    }
  };

  const remove = async (p: PluginCard) => {
    if (!window.confirm(`Remove ${p.name}? Its folder and everything in it is deleted from this machine.`)) return;
    setBusy(p.id);
    try {
      await extensionsAdmin.removePlugin(p.name);
      toast(`${p.name} removed`, { description: 'Its skills and commands are gone from sessions.' });
      setOpenId(null);
      r.reload();
    } catch (e) {
      toast('It was not removed', { description: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy('');
    }
  };
  const fetched = r.data?.countsFetchedAt;
  const isUsed = (id: string) => use[id] !== false;
  const setUsed = (p: PluginCard, v: boolean) => {
    setUse({ ...use, [p.id]: v }, `Plugin ${p.name} ${v ? 'used' : 'kept out of'} NeuroCode sessions`);
    toast(`${p.name} ${v ? 'used in' : 'kept out of'} NeuroCode sessions`, { description: 'Claude Code is not affected.' });
  };

  return (
    <Page>
      <PageHeader
        title="Plugins"
        subtitle="The plugins on this machine: Claude Code's own, which are read and never written, and this workspace's, which NeuroCode installed from a git URL or a folder and can remove again. A plugin's hooks stay refused until a rule allows one."
        actions={<Segmented options={[{ id: 'installed', label: `Installed (${installed.length})` }, { id: 'market', label: `Marketplaces (${market.length})` }, { id: 'registry', label: `Offered (${registry?.plugins.length ?? 0})` }]} value={tab} onChange={(v) => { setTab(v); setCat('all'); }} />}
      >
        <Toolbar>
          <Field className="w-64" value={q} onChange={setQ} icon={<Search className="size-3.5" />} placeholder="Search plugins, publishers…" onClear={() => setQ('')} />
          {tab !== 'registry' && (
            <SelectField className="w-44" value={cat} onChange={setCat} options={cats.map((c) => ({ value: c, label: c === 'all' ? 'Any category' : c }))} />
          )}
          <span className="ml-auto text-[12.5px] text-dim">{tab === 'registry' ? offers.length : list.length} shown</span>
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

          {tab === 'installed' && (
            <Panel className="accent-left" eyebrow={`This workspace's own folder · ${r.data.workspaceRoot}`}
              title={mine.length ? `${mine.length} installed by NeuroCode` : 'Install one into this workspace'}>
              <p className="max-w-4xl text-[13.5px] leading-relaxed text-ink-2">
                An https git URL is cloned; a folder on this machine is copied. Either way it lands in this
                workspace's own folder — never in Claude Code's cache — and its skills and commands reach
                NeuroCode sessions. Its hooks are refused until you write a rule for one, like every hook.
              </p>
              <div className="mt-3 flex flex-wrap items-end gap-2 border-t border-line pt-3">
                <Field className="min-w-[280px] flex-1" value={source} onChange={setSource}
                  placeholder="https://github.com/acme/toolbox.git, or /Users/you/plugins/toolbox"
                  onClear={() => setSource('')} />
                <Field className="w-44" value={name} onChange={setName} placeholder="folder name"
                  onClear={() => setName('')} />
                <Button size="sm" disabled={!source.trim() || !name.trim() || !!busy || !can(EXTENSIONS_PERMISSION)}
                  onClick={() => void install(source, name)}>
                  {busy === source ? <Loader2 className="size-3.5 animate-spin" /> : <FolderDown className="size-3.5" />}
                  Install
                </Button>
              </div>
              {!can(EXTENSIONS_PERMISSION) && (
                <p className="mt-2 text-[12px] text-dim">Installing needs {EXTENSIONS_PERMISSION}.</p>
              )}
            </Panel>
          )}

          {tab === 'registry' ? (
            <Panel flush eyebrow={registry?.exists ? registry.path : `No registry file at ${registry?.path ?? 'the plugins folder'}`}
              title={registry?.exists ? registry.name : 'Nothing is offered yet'}>
              <p className="px-5 pt-3 text-[13px] leading-relaxed text-ink-2">
                This is a file on this machine, not an index anybody fetches from: whoever keeps this
                workspace writes <Mono>registry.json</Mono> in the plugins folder, and what they wrote is
                what this list offers. That is the whole of the marketplace, and there is no other.
              </p>
              {offers.length === 0 ? (
                <div className="px-5 pb-4">
                  <Empty icon={<Blocks className="size-6" />} title="No registry file"
                    hint={`Put a registry.json at ${registry?.path ?? 'the plugins folder'} with a "plugins" list of {name, source, description}, and its entries appear here with an Install button.`} />
                </div>
              ) : (
                <div className="divide-y divide-line/60">
                  {offers.map((o: RegistryEntry) => {
                    const already = installed.some((p) => p.name === o.name && p.workspace);
                    return (
                      <div key={`${o.name}:${o.source}`} className="flex flex-wrap items-center gap-2.5 px-5 py-3">
                        <div className="min-w-0 flex-1">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="text-[13.5px] text-ink">{o.name}</span>
                            <Tag tone="neutral">{o.category}</Tag>
                            {o.publisher && <span className="text-[12px] text-dim">{o.publisher}</span>}
                            {already && <Tag tone="ok">installed</Tag>}
                          </div>
                          <p className="mt-0.5 text-[12.5px] text-soft">{o.description || 'No description in the registry file.'}</p>
                          <Mono className="mt-0.5 block truncate text-[11.5px]">{o.source}</Mono>
                        </div>
                        <Button size="xs" variant="outline" disabled={already || !!busy || !can(EXTENSIONS_PERMISSION)}
                          onClick={() => void install(o.source, o.name)}>
                          {busy === o.source ? <Loader2 className="size-3" /> : <Plus className="size-3" />}
                          {already ? 'Installed' : 'Install'}
                        </Button>
                      </div>
                    );
                  })}
                </div>
              )}
            </Panel>
          ) : list.length === 0 ? (
            <Empty title={tab === 'installed' ? (installed.length ? 'Nothing installed in this category' : 'No plugin is installed on this machine') : (market.length ? 'Nothing in this category' : 'No marketplace is known on this machine')}
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
                    {p.workspace ? (
                      <Button size="xs" variant="outline" disabled={!!busy || !can(EXTENSIONS_PERMISSION)} onClick={() => void remove(p)}>
                        {busy === p.id ? <Loader2 className="size-3 animate-spin" /> : <Trash2 className="size-3" />}Remove
                      </Button>
                    ) : (
                      <Button size="xs" variant="outline" onClick={() => void copyCommand(p)}><Copy className="size-3" />{p.installed ? 'Uninstall command' : 'Install command'}</Button>
                    )}
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
                  {open.installed && (open.workspace
                    ? <Tag tone="ok">this workspace's own</Tag>
                    : <Tag tone={open.enabledInClaude ? 'ok' : 'neutral'}>{open.enabledInClaude ? 'enabled in Claude Code' : 'disabled in Claude Code'}</Tag>)}
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
                    <KV k="Agents" v={open.providesKnown ? open.provides.agents : 'not known'} />
                    <KV k="Skills" v={open.providesKnown ? open.provides.skills : 'not known'} />
                    <KV k="Commands" v={open.providesKnown ? open.provides.commands : 'not known'} />
                    <KV k="Hooks" v={open.providesKnown ? open.provides.hooks : 'not known'} />
                    <KV k="MCP servers" v={open.providesKnown ? open.provides.mcp : 'not known'} />
                  </div>
                </Panel>
                <SectionTitle>In NeuroCode</SectionTitle>
                <ul className="space-y-1 text-[12.5px] text-soft">
                  {open.path && <li className="flex gap-1.5"><span className="mt-1.5 size-1 shrink-0 rounded-full bg-brand" /><span>Installed at <Mono>{open.path}</Mono></span></li>}
                  {open.workspace && open.source && (
                    <li className="flex gap-1.5"><span className="mt-1.5 size-1 shrink-0 rounded-full bg-brand" />
                      <span>{open.sourceKind === 'git' ? 'Cloned from' : 'Copied from'} <Mono className="break-all">{open.source}</Mono></span>
                    </li>
                  )}
                  <li className="flex gap-1.5"><span className="mt-1.5 size-1 shrink-0 rounded-full bg-brand" />
                    {open.provides.hooks > 0
                      ? `Its ${open.provides.hooks} hook${open.provides.hooks === 1 ? '' : 's'} appear on the Hooks screen and stay refused until a tool rule allows one by name.`
                      : 'It brings no hooks.'}
                  </li>
                  <li className="flex gap-1.5"><span className="mt-1.5 size-1 shrink-0 rounded-full bg-brand" />
                    {!open.installed
                      ? 'Installing happens in Claude Code. Once it is installed and enabled there, it shows up here.'
                      : open.workspace
                        ? `NeuroCode put this here and can remove it. Its skills and commands reach sessions while Use in NeuroCode is on${isUsed(open.id) ? ' — it is.' : ' — it is off.'}`
                        : `Its skills and commands appear in NeuroCode sessions only while it is enabled in Claude Code and Use in NeuroCode is on${open.enabledInClaude ? (isUsed(open.id) ? ' — it is.' : ' — it is off.') : ' — it is disabled in Claude Code.'}`}
                  </li>
                </ul>
                <div className="flex flex-wrap gap-2 border-t border-line pt-3">
                  {open.workspace ? (
                    <Button size="sm" variant="outline" disabled={!!busy || !can(EXTENSIONS_PERMISSION)} onClick={() => void remove(open)}>
                      {busy === open.id ? <Loader2 className="size-3.5 animate-spin" /> : <Trash2 className="size-3.5" />}Remove from this machine
                    </Button>
                  ) : (
                    <Button size="sm" onClick={() => void copyCommand(open)}><Copy className="size-3.5" />Copy {open.installed ? 'uninstall' : 'install'} command</Button>
                  )}
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
