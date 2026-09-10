import { useMemo, useState } from 'react';
import { Plus, ShieldAlert, Search, Server } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from '@/components/ui/dialog';
import {
  Page, PageHeader, PageBody, Panel, Tag, RiskPill, Dot, Mono, ListRow, Toolbar, Field,
  SelectField, DataTable, Row, Cell, Stat, StatGrid, KV, Empty, Ascii, StatusText,
} from '@/components/os';
import { mcpServers, mcpCallLog, rawConfigs, untrustedSourceIds } from '@/mock/mcp';
import { agentName } from '@/mock/agents';
import { cn } from '@/lib/utils';

export default function Mcp() {
  const [q, setQ] = useState('');
  const [status, setStatus] = useState('all');
  const [sel, setSel] = useState(mcpServers[0].id);
  const [add, setAdd] = useState(false);
  const [cmd, setCmd] = useState('');
  const [transport, setTransport] = useState('stdio');

  const list = useMemo(() => {
    const s = q.trim().toLowerCase();
    return mcpServers.filter((m) =>
      (status === 'all' || m.status === status) &&
      (!s || (m.name + m.command + m.tools.map((t) => t.name).join(' ')).toLowerCase().includes(s)));
  }, [q, status]);

  const srv = useMemo(() => list.find((m) => m.id === sel) ?? list[0] ?? mcpServers[0], [list, sel]);
  const calls = useMemo(() => mcpCallLog.filter((c) => c.server === srv.id), [srv.id]);
  const connected = mcpServers.filter((m) => m.status === 'connected').length;
  const tools = mcpServers.reduce((n, m) => n + m.tools.length, 0);

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
          <span className="ml-auto text-[11.5px] text-dim">{list.length} of {mcpServers.length} servers · {tools} tools</span>
        </Toolbar>
      </PageHeader>

      <PageBody className="space-y-4">
        <StatGrid cols={5}>
          <Stat label="Connected" value={connected} tone="ok" sub={`${mcpServers.length - connected} not available`} icon={<Server className="size-3" />} />
          <Stat label="Tools exposed" value={tools} sub="across every server" />
          <Stat label="Calls 24h" value={mcpServers.reduce((n, m) => n + m.calls24h, 0).toLocaleString()} />
          <Stat label="Median latency" value={`${Math.round(mcpServers.reduce((n, m) => n + m.latencyMs, 0) / mcpServers.length)}ms`} />
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

        <div className="flex min-h-[520px] gap-3">
          <div className="no-scrollbar w-[290px] shrink-0 overflow-y-auto rounded-md border border-line bg-surface">
            {list.length === 0 ? <Empty title="No server matches" /> : list.map((m) => (
              <ListRow key={m.id} active={m.id === srv.id} onClick={() => setSel(m.id)}>
                <div className="flex items-center gap-2">
                  <Dot state={m.status} pulse={m.status === 'connected'} />
                  <span className="truncate text-[12.5px] font-medium text-ink">{m.name}</span>
                  {untrustedSourceIds.includes(m.id) && <ShieldAlert className="ml-auto size-3 text-warn" />}
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
              actions={untrustedSourceIds.includes(srv.id) ? <Tag tone="warn"><ShieldAlert className="size-3" />untrusted source</Tag> : <Tag tone="ok">trusted</Tag>}
            >
              <div className="grid grid-cols-2 gap-x-6 md:grid-cols-4">
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
                <Ascii className="max-h-[240px] overflow-auto">{rawConfigs[srv.id] ?? '// no config recorded for this server'}</Ascii>
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

      <Dialog open={add} onOpenChange={setAdd}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>Add an MCP server</DialogTitle>
            <DialogDescription>
              New servers start disconnected and every tool they expose defaults to <span className="text-ink">ask</span>
              until you promote it in Permissions.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <SelectField label="Transport" value={transport} onChange={setTransport} options={['stdio', 'http', 'sse']} />
            <Field label="Launch command or URL" value={cmd} onChange={setCmd} mono
              placeholder="npx -y @modelcontextprotocol/server-postgres postgres://…" />
            <div className={cn('rounded-sm border border-warn/30 bg-warn/8 px-3 py-2 text-[11.5px] text-warn')}>
              This server's output will be treated as untrusted data until you mark it otherwise.
            </div>
          </div>
          <DialogFooter>
            <Button size="sm" variant="outline" onClick={() => setAdd(false)}>Cancel</Button>
            <Button size="sm" disabled={!cmd.trim()} onClick={() => { setAdd(false); setCmd(''); toast.success('Server registered', { description: 'Connect it from the list when you are ready.' }); }}>
              Register
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Page>
  );
}
