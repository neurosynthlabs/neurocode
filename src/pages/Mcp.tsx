import { useMemo, useState } from 'react';
import { Loader2, Play, Plus, PlugZap, Search, Server, ShieldAlert, ShieldCheck, X } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import {
  Page, PageHeader, PageBody, Panel, Tag, RiskPill, Dot, Mono, ListRow, Toolbar, Field,
  SelectField, DataTable, Row, Cell, Stat, StatGrid, KV, Empty, Ascii, StatusText, Wizard, More,
} from '@/components/os';
import { useAuth } from '@/lib/auth';
import { useData } from '@/lib/data';
import { ApiError } from '@/lib/api';
import { LAUNCH_PERMISSION, mcpApi, type ToolCall } from '@/lib/live/mcp';
import type { McpServer } from '@/types';
import { cn } from '@/lib/utils';
import { ago } from './code/format';

const GENERIC_LABELS = new Set(['mcp', 'api', 'www', 'app', 'server', 'localhost']);

/** A readable server name from what the operator typed — never the URL's userinfo (it may hold a token). */
function deriveName(transport: string, raw: string) {
  const v = raw.trim();
  if (!v) return 'new-server';
  let name: string;
  if (transport !== 'stdio') {
    try {
      const labels = new URL(v).hostname.split('.');
      name = labels.find((l) => l && !GENERIC_LABELS.has(l.toLowerCase())) ?? labels[0] ?? '';
    } catch {
      name = '';
    }
  } else {
    // the package is the first argument that is not a flag, a URL or a path
    const tokens = v.split(/\s+/);
    const pkg = tokens.slice(1).find((a) => !a.startsWith('-') && !a.includes('://') && !/^[./~]/.test(a)) ?? tokens[0];
    const scoped = pkg.match(/^@([^/]+)\/(.+)$/);
    const base = (scoped ? scoped[2] : pkg).replace(/@[\w.-]+$/, '');
    name = base.match(/^(?:mcp-)?server-(.+)$/)?.[1] ?? base.replace(/-mcp$|^mcp-/, '');
    // `@playwright/mcp` names nothing on its own — the scope is the real name
    if ((!name || GENERIC_LABELS.has(name)) && scoped) name = scoped[1];
  }
  return name.toLowerCase().replace(/[^a-z0-9-]+/g, '-').replace(/^-+|-+$/g, '') || 'new-server';
}

/** What is wrong with the connection step for this transport, or null when it is usable. */
function connectionProblem(transport: string, raw: string): string | null {
  const v = raw.trim();
  if (!v) return 'Enter a command or URL';
  if (transport === 'stdio') return /^[a-z][\w+.-]*:\/\//i.test(v) ? 'That is a URL — choose HTTP or SSE, or enter a launch command' : null;
  try {
    return /^https?:$/.test(new URL(v).protocol) ? null : 'Use an http:// or https:// URL';
  } catch {
    return 'Not a valid URL — e.g. https://mcp.example.com/mcp';
  }
}

const TRANSPORTS = [
  { id: 'stdio', label: 'stdio', note: 'A local process, talked to over pipes. The most common.' },
  { id: 'http', label: 'Streamable HTTP', note: 'A remote server over HTTPS. Needs a URL and usually a token.' },
  { id: 'sse', label: 'SSE (legacy)', note: 'The older remote transport. A check speaks streamable HTTP, so an SSE-only server says why it refused.' },
];

const STATUSES: McpServer['status'][] = ['connected', 'disconnected', 'error', 'auth_required'];

/** A server nobody has checked has no status of its own yet; "disconnected" would read as a measurement. */
const statusLabel = (m: McpServer) => (m.checkedAt ? m.status.replace('_', ' ') : 'not checked');

export default function Mcp() {
  const [q, setQ] = useState('');
  const [status, setStatus] = useState('all');
  const { mcp: servers, registerMcp, checkMcp, trustMcp } = useData();
  const { can } = useAuth();
  const [sel, setSel] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [checking, setChecking] = useState<string | null>(null);
  const [add, setAdd] = useState(false);
  const [cmd, setCmd] = useState('');
  const [transport, setTransport] = useState('stdio');
  const [scope, setScope] = useState('project');
  const [effect, setEffect] = useState('ask');
  const [trying, setTrying] = useState<{ server: string; tool: string } | null>(null);

  // The config the wizard will write, derived live from its inputs.
  const parts = cmd.trim().split(/\s+/).filter(Boolean);
  const draftName = deriveName(transport, cmd);
  const draftConfig = JSON.stringify({
    servers: {
      [draftName]: transport === 'stdio'
        ? { command: parts[0] ?? '', args: parts.slice(1), scope, untrusted: true, defaultEffect: effect }
        : { url: cmd.trim(), transport, scope, untrusted: true, defaultEffect: effect },
    },
  }, null, 2);

  const list = useMemo(() => {
    const s = q.trim().toLowerCase();
    return servers.filter((m) =>
      (status === 'all' || (m.checkedAt !== null && m.status === status) || (status === 'unchecked' && m.checkedAt === null)) &&
      (!s || (m.name + m.command + m.tools.map((t) => t.name).join(' ')).toLowerCase().includes(s)));
  }, [servers, q, status]);

  const srv = list.find((m) => m.id === sel) ?? list[0] ?? null;
  const checked = servers.filter((m) => m.checkedAt !== null);
  const connected = checked.filter((m) => m.status === 'connected').length;
  const tools = servers.reduce((n, m) => n + m.tools.length, 0);
  const untrusted = servers.filter((m) => m.untrusted);

  const mayLaunch = can(LAUNCH_PERMISSION);
  /** Why this server cannot be checked by this person right now, or null when it can. */
  const checkBlocker = (m: McpServer) =>
    !can('mcp:manage') ? 'Your role cannot manage MCP servers (mcp:manage).'
      : m.transport !== 'stdio' ? null
        : m.untrusted ? 'Untrusted: its command is never launched. Trust it first.'
          : !mayLaunch ? `Launching a command needs ${LAUNCH_PERMISSION}.` : null;
  /** Why this person cannot try this server's tools right now, or null when they can. */
  const tryBlocker = (m: McpServer) =>
    !can('mcp:manage') ? 'Your role cannot call MCP tools (mcp:manage).'
      : m.untrusted ? 'Untrusted: none of its tools is called. Trust it first.'
        : m.transport === 'stdio' && !mayLaunch ? `Calling a stdio server's tool launches it, which needs ${LAUNCH_PERMISSION}.`
          : !m.checkedAt || m.status !== 'connected' ? 'It was not connected at its last check. Check it first.' : null;

  const check = async (m: McpServer) => {
    setChecking(m.id);
    const doc = await checkMcp(m.id);
    setChecking(null);
    if (!doc) return;
    if (doc.status === 'connected') {
      toast.success(`${doc.name} connected`, { description: `${doc.tools.length} ${doc.tools.length === 1 ? 'tool' : 'tools'} listed in ${doc.latencyMs} ms.` });
    } else {
      toast.warning(`${doc.name}: ${statusLabel(doc)}`, { description: doc.lastError });
    }
  };
  const trust = async (m: McpServer) => {
    const doc = await trustMcp(m.id, !!m.untrusted);
    if (doc) toast.success(doc.untrusted ? `${doc.name} is untrusted again` : `${doc.name} is trusted`,
      { description: doc.untrusted ? 'Its command will not be launched.' : 'Its command may now be launched to check it.' });
  };

  return (
    <Page>
      <PageHeader
        title="MCP & Tools"
        subtitle="Model Context Protocol servers the workspace knows, and their tools."
        about={<>
          <p>A check connects once and records what a server offers.</p>
          <p>Tool output is untrusted input. An untrusted server is never launched, and none of its tools is called.</p>
          <p>A checked server's tool runs only under the tool rules. What it answers is shown to you, never stored.</p>
        </>}
        actions={<Button size="sm" onClick={() => setAdd(true)}><Plus className="size-3.5" />Add server</Button>}
      >
        {servers.length > 0 && (
          <Toolbar>
            <Field className="w-64" value={q} onChange={setQ} icon={<Search className="size-3.5" />} placeholder="Search servers, tools, commands…" onClear={() => setQ('')} />
            <SelectField className="w-40" value={status} onChange={setStatus}
              options={[{ value: 'all', label: 'Any status' }, { value: 'unchecked', label: 'not checked' },
                ...STATUSES.map((x) => ({ value: x, label: x.replace('_', ' ') }))]} />
            <span className="ml-auto text-[12.5px] text-dim">{list.length} of {servers.length} servers · {tools} tools listed</span>
          </Toolbar>
        )}
      </PageHeader>

      <PageBody className="space-y-4">
        {servers.length === 0 ? (
          <Empty icon={<Server className="size-6" />} title="No MCP servers registered"
            hint="Record how it is launched or reached, then check its tools."
            action={<Button size="sm" variant="outline" onClick={() => setAdd(true)}>Register a server</Button>} />
        ) : (
          <>
            <StatGrid cols={4}>
              <Stat label="Registered" value={servers.length} sub={`${checked.length} checked`} icon={<Server className="size-3" />} />
              <Stat label="Connected" value={connected} tone={connected ? 'ok' : 'neutral'} sub="at their last check" />
              <Stat label="Tools listed" value={tools} sub="by the servers themselves" />
              <Stat label="Untrusted" value={untrusted.length} tone={untrusted.length ? 'warn' : 'neutral'} sub="never launched" icon={<ShieldAlert className="size-3" />} />
            </StatGrid>

            {untrusted.length > 0 && (
              <div className="flex flex-wrap items-center gap-1.5 text-[12.5px] text-dim">
                <ShieldAlert className="size-3.5 text-warn" />Untrusted, never launched:
                {untrusted.map((m) => <Tag key={m.id} tone="warn">{m.name}</Tag>)}
              </div>
            )}

            <div className="flex min-h-[520px] flex-col gap-3 md:flex-row">
              <div className="no-scrollbar w-full shrink-0 max-h-[42vh] md:max-h-none md:w-[290px] overflow-y-auto rounded-md border border-line bg-surface">
                {list.length === 0 ? <Empty title="No server matches" hint="Clear the filters." /> : list.map((m) => (
                  <ListRow key={m.id} active={m.id === srv?.id} onClick={() => setSel(m.id)}>
                    <div className="flex items-center gap-2">
                      <Dot state={m.checkedAt ? m.status : 'idle'} pulse={m.status === 'connected' && !!m.checkedAt} />
                      <span className="truncate text-[13.5px] font-medium text-ink">{m.name}</span>
                      {m.untrusted && <ShieldAlert className="ml-auto size-3 text-warn" />}
                    </div>
                    <div className="mt-1 flex items-center gap-2 text-[11.5px] text-dim">
                      <Mono>{m.transport}</Mono>
                      <span>{m.checkedAt ? `${m.tools.length} tools` : 'not checked'}</span>
                      {m.latencyMs !== null && <span className="ml-auto tnum">{m.latencyMs} ms</span>}
                    </div>
                  </ListRow>
                ))}
              </div>

              {srv && (
                <div className="min-w-0 flex-1 space-y-3">
                  <Panel
                    eyebrow={`${srv.scope} scope · ${srv.checkedAt ? `checked ${ago(srv.checkedAt)}` : 'never checked'}`}
                    title={<span className="flex items-center gap-2">{srv.name}<StatusText state={srv.checkedAt ? srv.status : 'idle'} label={statusLabel(srv)} /></span>}
                    actions={
                      <div className="flex items-center gap-1.5">
                        {srv.untrusted ? <Tag tone="warn"><ShieldAlert className="size-3" />untrusted</Tag> : <Tag tone="ok"><ShieldCheck className="size-3" />trusted</Tag>}
                        {can('mcp:manage') && mayLaunch && (
                          <Button size="xs" variant="outline" onClick={() => void trust(srv)}>{srv.untrusted ? 'Trust' : 'Untrust'}</Button>
                        )}
                        <Button size="xs" disabled={!!checkBlocker(srv) || checking === srv.id} title={checkBlocker(srv) ?? undefined} onClick={() => void check(srv)}>
                          {checking === srv.id ? <Loader2 className="size-3 animate-spin" /> : <PlugZap className="size-3" />}{checking === srv.id ? 'Checking…' : 'Check'}
                        </Button>
                      </div>
                    }
                  >
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 xl:grid-cols-4">
                      <KV k="Transport" v={srv.transport} />
                      <KV k="Default effect" v={srv.defaultEffect ?? 'ask'} />
                      {srv.latencyMs !== null && <KV k="Listing took" v={`${srv.latencyMs} ms`} />}
                      {srv.checkedAt && srv.status === 'connected' && <KV k="Tools" v={srv.tools.length} />}
                      {srv.resources !== null && <KV k="Resources" v={srv.resources} />}
                      {srv.prompts !== null && <KV k="Prompts" v={srv.prompts} />}
                    </div>
                    <Mono className="mt-2.5 block truncate">{srv.command}</Mono>
                    {srv.lastError && (
                      <p className="mt-2.5 rounded-sm border border-warn/30 bg-warn/8 px-3 py-2 text-[12.5px] leading-relaxed text-warn">{srv.lastError}</p>
                    )}
                    {srv.transport === 'stdio' && srv.untrusted && (
                      <p className="mt-2.5 text-[12.5px] text-dim">
                        Checking launches this command here, so someone with {LAUNCH_PERMISSION} must trust it first.
                      </p>
                    )}
                  </Panel>

                  <Panel eyebrow={srv.checkedAt ? `${srv.tools.length} at the last check` : 'not checked yet'} title="Tools" flush
                    about="Risk comes from the tool's own hints.">
                    {srv.tools.length === 0 ? (
                      <Empty title={srv.checkedAt ? 'The server listed no tools' : 'Not checked yet'}
                        hint={srv.checkedAt ? (srv.status === 'connected' ? 'It connected and offers none.' : 'The last check did not connect.') : 'Check it to list its tools.'} />
                    ) : (
                      <DataTable head={['Tool', 'What it does', 'Risk', '']}>
                        {srv.tools.map((t) => (
                          <Row key={t.name} active={trying?.server === srv.id && trying.tool === t.name}>
                            <Cell mono className="text-brand">{t.name}</Cell>
                            <Cell className="max-w-[520px] text-[12.5px] text-soft">{t.description}</Cell>
                            <Cell><RiskPill risk={t.risk} bare /></Cell>
                            <Cell>
                              <Button size="xs" variant="outline" disabled={!!tryBlocker(srv)} title={tryBlocker(srv) ?? undefined}
                                onClick={() => setTrying({ server: srv.id, tool: t.name })}>
                                <Play className="size-3" />Try
                              </Button>
                            </Cell>
                          </Row>
                        ))}
                      </DataTable>
                    )}
                  </Panel>

                  {trying?.server === srv.id && srv.tools.some((t) => t.name === trying.tool) && (
                    <TryTool key={`${srv.id}/${trying.tool}`} server={srv} tool={trying.tool} onClose={() => setTrying(null)} />
                  )}

                  <More label="Server config">
                    <Ascii className="max-h-[240px] overflow-auto">{srv.config ?? '// no config recorded for this server'}</Ascii>
                  </More>
                </div>
              )}
            </div>
          </>
        )}
      </PageBody>

      <Wizard
        open={add}
        onOpenChange={setAdd}
        title="Add an MCP server"
        description="It starts untrusted and unchecked."
        finishLabel="Register server"
        busy={busy}
        onFinish={async () => {
          setBusy(true);
          const doc = await registerMcp({
            name: draftName, command: cmd.trim(), config: draftConfig,
            transport: transport as McpServer['transport'], scope: scope as McpServer['scope'],
            defaultEffect: effect as NonNullable<McpServer['defaultEffect']>,
          });
          setBusy(false);
          if (!doc) return;
          setAdd(false);
          setSel(doc.id);
          toast.success(`${doc.name} registered`, {
            description: doc.transport === 'stdio' ? 'Trust it, then check it to list its tools.' : 'Check it to list its tools.',
          });
          setCmd('');
          setTransport('stdio');
          setScope('project');
          setEffect('ask');
        }}
        steps={[
          {
            id: 'transport', title: 'Transport', hint: 'how it talks',
            content: (
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
                {TRANSPORTS.map((t) => (
                  <button key={t.id} type="button" onClick={() => setTransport(t.id)} aria-pressed={transport === t.id}
                    className={cn('rounded-md border p-3 text-left transition-colors',
                      transport === t.id ? 'border-brand bg-brand/8' : 'border-line bg-surface hover:border-line-strong')}>
                    <span className={cn('block font-mono text-[13.5px] font-semibold', transport === t.id ? 'text-brand' : 'text-ink')}>{t.label}</span>
                    <span className="mt-1 block text-[12px] leading-snug text-soft">{t.note}</span>
                  </button>
                ))}
              </div>
            ),
          },
          {
            id: 'connection', title: 'Connection', hint: transport === 'stdio' ? 'launch command' : 'endpoint',
            valid: connectionProblem(transport, cmd) === null, blocker: connectionProblem(transport, cmd) ?? undefined,
            content: (
              <div className="space-y-3">
                <Field
                  label={transport === 'stdio' ? 'Launch command' : 'Server URL'}
                  value={cmd} onChange={setCmd} mono
                  placeholder={transport === 'stdio' ? 'npx -y @modelcontextprotocol/server-filesystem ~/code' : 'https://mcp.example.com/mcp'}
                />
                <SelectField label="Scope" value={scope} onChange={setScope}
                  options={[
                    { value: 'project', label: 'project — this project only' },
                    { value: 'global', label: 'global — every project' },
                    { value: 'local', label: 'local — this machine, never synced' },
                  ]} />
              </div>
            ),
          },
          {
            id: 'trust', title: 'Trust', hint: 'default effect',
            content: (
              <div className="space-y-3">
                <div className="flex items-start gap-2.5 rounded-sm border border-warn/30 bg-warn/8 px-3 py-2.5">
                  <ShieldAlert className="mt-px size-3.5 shrink-0 text-warn" />
                  <span className="text-[13px] leading-relaxed text-warn">
                    <span className="font-semibold">A new server starts untrusted.</span> Its command is never launched
                    until someone trusts it. The effect below is recorded for when agents call its tools.
                  </span>
                </div>
                <SelectField label="Default effect for its tools" value={effect} onChange={setEffect}
                  options={[
                    { value: 'ask', label: 'ask — every call is confirmed' },
                    { value: 'allow-read', label: 'allow read-only tools, ask for the rest' },
                    { value: 'deny', label: 'deny — nothing runs until reviewed' },
                  ]} />
              </div>
            ),
          },
          {
            id: 'review', title: 'Review', hint: 'generated config',
            content: (
              <div className="space-y-3">
                <div className="rounded-sm border border-line bg-base px-3 py-1.5">
                  <KV k="Name" v={draftName} mono />
                  <KV k="Transport" v={transport} />
                  <KV k="Scope" v={scope} />
                  <KV k="Default effect" v={effect} />
                </div>
                <Ascii className="max-h-[220px] overflow-auto">{draftConfig}</Ascii>
              </div>
            ),
          },
        ]}
      />
    </Page>
  );
}

/** One tool, called for real with the arguments typed here. The answer is shown, never stored. */
function TryTool({ server, tool, onClose }: { server: McpServer; tool: string; onClose: () => void }) {
  const { projects } = useData();
  const [args, setArgs] = useState('{}');
  const [project, setProject] = useState('workspace');
  const [busy, setBusy] = useState(false);
  const [answer, setAnswer] = useState<ToolCall | null>(null);
  const risk = server.tools.find((t) => t.name === tool)?.risk;

  let parsed: Record<string, unknown> | null = null;
  let problem = '';
  try {
    const value: unknown = JSON.parse(args || '{}');
    if (value && typeof value === 'object' && !Array.isArray(value)) parsed = value as Record<string, unknown>;
    else problem = 'The arguments are a JSON object, like {"query": "…"}.';
  } catch {
    problem = 'Not valid JSON yet.';
  }

  const call = async () => {
    if (!parsed) return;
    setBusy(true);
    try {
      setAnswer(await mcpApi.call(server.id, tool, parsed, project === 'workspace' ? null : project));
    } catch (e) {
      setAnswer(null);
      toast.error(`${tool} was not called`, { description: e instanceof ApiError ? e.message : 'The local API did not answer.' });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel className="accent-left" eyebrow={`${server.name} · ${risk ? `${risk.toLowerCase()} risk` : 'tool'} · the tool rules apply`}
      title={<span className="flex items-center gap-2"><Play className="size-3.5 text-brand" /><Mono>{tool}</Mono></span>}
      actions={<Button size="xs" variant="ghost" aria-label="Close" onClick={onClose}><X className="size-3" /></Button>}>
      <label className="block">
        <span className="mb-1.5 block text-[12.5px] font-medium text-soft">Arguments (JSON)</span>
        <textarea value={args} onChange={(e) => setArgs(e.target.value)} rows={5} spellCheck={false}
          className="focus-brand w-full rounded-lg border border-line bg-surface-2/60 px-3 py-2 font-mono text-[12.5px] text-ink focus-visible:outline-none" />
      </label>
      <div className="mt-2 flex flex-wrap items-end gap-2">
        <SelectField className="w-56" label="Rules of" value={project} onChange={setProject}
          options={[{ value: 'workspace', label: 'the workspace' }, ...projects.map((p) => ({ value: p.id, label: p.name }))]} />
        <Button size="sm" disabled={!parsed || busy} title={problem || undefined} onClick={() => void call()}>
          {busy ? <Loader2 className="size-3.5 animate-spin" /> : <Play className="size-3.5" />}{busy ? 'Calling…' : 'Call'}
        </Button>
        {problem && <span className="text-[12px] text-warn">{problem}</span>}
        {risk === 'HIGH' && !problem && <span className="text-[12px] text-dim">The server does not say this tool only reads. It may change things.</span>}
      </div>
      {answer && (
        <div className="mt-3 border-t border-line pt-3">
          <div className="flex flex-wrap items-center gap-2">
            <Tag tone={!answer.ok ? 'danger' : answer.isError ? 'warn' : 'ok'}>
              {!answer.ok ? 'call failed' : answer.isError ? 'the tool reported an error' : 'answered'}
            </Tag>
            {answer.ms !== null && <span className="tnum text-[12px] text-dim">{answer.ms} ms</span>}
            {answer.truncated && <span className="text-[12px] text-dim">truncated to 20,000 characters</span>}
            <span className="text-[12px] text-dim">{answer.decision.why}</span>
          </div>
          {answer.ok ? (
            <Ascii className="mt-2 max-h-[320px] overflow-auto whitespace-pre-wrap">{answer.text || '(the tool answered with no text)'}</Ascii>
          ) : (
            <p className="mt-2 text-[12.5px] text-danger">{answer.error}</p>
          )}
        </div>
      )}
    </Panel>
  );
}
