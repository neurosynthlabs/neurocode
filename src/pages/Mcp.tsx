import { useMemo, useState } from 'react';
import { Plus, ShieldAlert, Search, Server } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import {
  Page, PageHeader, PageBody, Panel, Tag, RiskPill, Dot, Mono, ListRow, Toolbar, Field,
  SelectField, DataTable, Row, Cell, Stat, StatGrid, KV, Empty, Ascii, StatusText, Wizard,
} from '@/components/os';
import { mcpCallLog, rawConfigs, untrustedSourceIds } from '@/mock/mcp';
import { useData } from '@/lib/data';
import type { McpServer } from '@/types';
import { agentName } from '@/mock/agents';
import { cn } from '@/lib/utils';

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
  { id: 'stdio', label: 'stdio', note: 'A local process the OS launches and talks to over pipes. Fastest, and the most common.' },
  { id: 'http', label: 'Streamable HTTP', note: 'A remote server over HTTPS. Needs a URL and usually a token.' },
  { id: 'sse', label: 'SSE (legacy)', note: 'The older remote transport. Prefer HTTP unless the server only speaks SSE.' },
];

export default function Mcp() {
  const [q, setQ] = useState('');
  const [status, setStatus] = useState('all');
  const { mcp: servers, registerMcp, mode } = useData();
  const [sel, setSel] = useState(servers[0]?.id ?? '');
  const [busy, setBusy] = useState(false);
  const [add, setAdd] = useState(false);
  const [cmd, setCmd] = useState('');
  const [transport, setTransport] = useState('stdio');
  const [scope, setScope] = useState('project');
  const [effect, setEffect] = useState('ask');

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
      (status === 'all' || m.status === status) &&
      (!s || (m.name + m.command + m.tools.map((t) => t.name).join(' ')).toLowerCase().includes(s)));
  }, [servers, q, status]);

  const srv = useMemo(() => list.find((m) => m.id === sel) ?? list[0] ?? servers[0], [list, sel, servers]);
  const calls = useMemo(() => mcpCallLog.filter((c) => c.server === srv.id), [srv.id]);
  const connected = servers.filter((m) => m.status === 'connected').length;
  const tools = servers.reduce((n, m) => n + m.tools.length, 0);

  return (
    <Page>
      <PageHeader
        title="MCP & Tools"
        subtitle="Model Context Protocol servers give agents hands. Everything they return is treated as data — never as instructions."
        actions={<Button size="sm" onClick={() => setAdd(true)}><Plus className="size-3.5" />Add server</Button>}
      >
        <Toolbar>
          <Field className="w-64" value={q} onChange={setQ} icon={<Search className="size-3.5" />} placeholder="Search servers, tools, commands…" onClear={() => setQ('')} />
          <SelectField className="w-40" value={status} onChange={setStatus}
            options={[{ value: 'all', label: 'Any status' }, { value: 'connected', label: 'connected' }, { value: 'disconnected', label: 'disconnected' }, { value: 'error', label: 'error' }, { value: 'auth_required', label: 'auth required' }]} />
          <span className="ml-auto text-[11.5px] text-dim">{list.length} of {servers.length} servers · {tools} tools</span>
        </Toolbar>
      </PageHeader>

      <PageBody className="space-y-4">
        <StatGrid cols={5}>
          <Stat label="Connected" value={connected} tone="ok" sub={`${servers.length - connected} not available`} icon={<Server className="size-3" />} />
          <Stat label="Tools exposed" value={tools} sub="across every server" />
          <Stat label="Calls 24h" value={servers.reduce((n, m) => n + m.calls24h, 0).toLocaleString()} />
          <Stat label="Median latency" value={`${Math.round(servers.reduce((n, m) => n + m.latencyMs, 0) / servers.length)}ms`} />
          <Stat label="Untrusted sources" value={untrustedSourceIds.length} tone="warn" sub="output is data, not orders" icon={<ShieldAlert className="size-3" />} />
        </StatGrid>

        <Panel className="border-warn/30 accent-left" eyebrow="Non-negotiable" title="Tool output is untrusted input">
          <p className="text-[12.5px] leading-relaxed text-ink-2">
            A web page, a GitHub issue, a Jira ticket or a database row can contain text addressed to the agent. The OS
            treats every tool result as <span className="text-ink">data</span>. If a result contains an instruction, the
            agent surfaces it to you and stops — it never acts on it. Servers marked untrusted below carry that label
            into every prompt that quotes them.
          </p>
          <div className="mt-2.5 flex flex-wrap gap-1">
            {untrustedSourceIds.map((i) => <Tag key={i} tone="warn">{i}</Tag>)}
          </div>
        </Panel>

        <div className="flex min-h-[520px] flex-col gap-3 md:flex-row">
          <div className="no-scrollbar w-full shrink-0 max-h-[42vh] md:max-h-none md:w-[290px] overflow-y-auto rounded-md border border-line bg-surface">
            {list.length === 0 ? <Empty title="No server matches" /> : list.map((m) => (
              <ListRow key={m.id} active={m.id === srv.id} onClick={() => setSel(m.id)}>
                <div className="flex items-center gap-2">
                  <Dot state={m.status} pulse={m.status === 'connected'} />
                  <span className="truncate text-[12.5px] font-medium text-ink">{m.name}</span>
                  {(m.untrusted || untrustedSourceIds.includes(m.id)) && <ShieldAlert className="ml-auto size-3 text-warn" />}
                </div>
                <div className="mt-1 flex items-center gap-2 text-[10.5px] text-dim">
                  <Mono>{m.transport}</Mono>
                  <span>{m.tools.length} tools</span>
                  <span className="ml-auto tnum">{m.latencyMs}ms</span>
                </div>
              </ListRow>
            ))}
          </div>

          <div className="min-w-0 flex-1 space-y-3">
            <Panel
              eyebrow={`${srv.scope} scope`}
              title={<span className="flex items-center gap-2">{srv.name}<StatusText state={srv.status} /></span>}
              actions={(srv.untrusted || untrustedSourceIds.includes(srv.id)) ? <Tag tone="warn"><ShieldAlert className="size-3" />untrusted source</Tag> : <Tag tone="ok">trusted</Tag>}
            >
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 xl:grid-cols-4">
                <KV k="Transport" v={srv.transport} />
                <KV k="Latency" v={`${srv.latencyMs} ms`} />
                <KV k="Calls 24h" v={srv.calls24h.toLocaleString()} />
                <KV k="Error rate" v={<span className={srv.errorRate > 1 ? 'text-warn' : 'text-ok'}>{srv.errorRate}%</span>} />
                <KV k="Resources" v={srv.resources} />
                <KV k="Prompts" v={srv.prompts} />
                <KV k="Tools" v={srv.tools.length} />
                <KV k="Scope" v={srv.scope} />
              </div>
              <Mono className="mt-2.5 block truncate">{srv.command}</Mono>
            </Panel>

            <Panel eyebrow={`${srv.tools.length} tools · risk decides whether a human is asked`} title="Exposed tools" flush>
              <DataTable head={['Tool', 'What it does', 'Risk']}>
                {srv.tools.map((t) => (
                  <Row key={t.name}>
                    <Cell mono className="text-brand">{t.name}</Cell>
                    <Cell className="max-w-[520px] text-[11.5px] text-soft">{t.description}</Cell>
                    <Cell><RiskPill risk={t.risk} bare /></Cell>
                  </Row>
                ))}
              </DataTable>
            </Panel>

            <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
              <Panel eyebrow="As configured" title="Server config">
                <Ascii className="max-h-[240px] overflow-auto">{srv.config ?? rawConfigs[srv.id] ?? '// no config recorded for this server'}</Ascii>
              </Panel>
              <Panel eyebrow={`${calls.length} recent calls`} title="Call log" flush>
                {calls.length === 0 ? <Empty title="No calls recorded" hint="This server has been idle in the current window." /> : (
                  <DataTable head={['At', 'Tool', 'Agent', 'ms', 'Result']}>
                    {calls.map((c, i) => (
                      <Row key={i}>
                        <Cell mono className="text-dim">{c.at}</Cell>
                        <Cell mono>{c.tool}</Cell>
                        <Cell className="text-[11.5px]">{agentName(c.agent)}</Cell>
                        <Cell className="tnum">{c.ms}</Cell>
                        <Cell><Tag tone={c.result === 'ok' ? 'ok' : c.result === 'denied' ? 'warn' : 'danger'}>{c.result}</Tag></Cell>
                      </Row>
                    ))}
                  </DataTable>
                )}
              </Panel>
            </div>
          </div>
        </div>
      </PageBody>

      <Wizard
        open={add}
        onOpenChange={setAdd}
        title="Add an MCP server"
        description="New servers arrive disconnected, and every tool they expose starts on the default effect you pick here until you promote it in Permissions."
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
            description: mode === 'live' ? 'Saved. It stays disconnected until you connect it.' : 'Disconnected until you connect it from the list.',
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
                    <span className={cn('block font-mono text-[12.5px] font-semibold', transport === t.id ? 'text-brand' : 'text-ink')}>{t.label}</span>
                    <span className="mt-1 block text-[11px] leading-snug text-soft">{t.note}</span>
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
                  placeholder={transport === 'stdio' ? 'npx -y @modelcontextprotocol/server-postgres postgres://readonly@localhost/erp' : 'https://mcp.example.com/mcp'}
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
                  <span className="text-[12px] leading-relaxed text-warn">
                    <span className="font-semibold">Output is treated as untrusted.</span> Always on for a new server — an
                    instruction found inside a tool result is surfaced to you, never obeyed. You can mark a server trusted
                    later, one server at a time.
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
