import { useMemo, useState } from 'react';
import { Cable, ArrowRight, ArrowLeft, Check, X, Minus } from 'lucide-react';
import { toast } from 'sonner';
import {
  Page, PageHeader, PageBody, Panel, Tag, Dot, Mono, ListRow, Ascii, KV, Stat, StatGrid,
  DataTable, Row, Cell, Empty, SectionTitle, StatusText,
} from '@/components/os';
import { acpClients, acpCapabilities, capabilityMatrix, lifecycle, permissionModes, jsonRpcTail, acpEvents } from '@/mock/acp';
import { cn } from '@/lib/utils';

const DIAGRAM = `  ┌──────────────────────┐        ┌──────────────────────┐        ┌──────────────────────┐
  │   EDITOR             │        │     NEUROCODE        │        │      TOOLS           │
  │  Zed · Neovim · VS   │◀──ACP──▶│  agents · memory     │◀──MCP──▶│  fs · git · sql      │
  │  JetBrains · Emacs   │        │  orchestrator        │        │  playwright · docker │
  └──────────────────────┘        └──────────────────────┘        └──────────────────────┘
        the human's                 decides and executes             does the actual work
        editing surface

  ACP  connects an EDITOR to the agent    — who may ask, and how the answer streams back
  MCP  connects the AGENT to a tool       — what the agent is able to touch at all`;

const YES = { yes: Check, no: X, partial: Minus };
const TONE = { yes: 'text-ok', no: 'text-dim', partial: 'text-warn' };

export default function Acp() {
  const [sel, setSel] = useState(acpClients[0].id);
  const [modes, setModes] = useState<Record<string, string>>(
    () => Object.fromEntries(acpClients.map((c) => [c.id, c.permissionMode])),
  );
  const c = useMemo(() => acpClients.find((x) => x.id === sel) ?? acpClients[0], [sel]);
  const caps = capabilityMatrix[c.id] ?? {};
  const events = useMemo(() => acpEvents.filter((e) => e.client === c.id), [c.id]);
  const mode = modes[c.id];

  return (
    <Page>
      <PageHeader
        title="ACP Bridge"
        subtitle="Agent Client Protocol — drive NeuroCode from inside your editor. The counterpart to MCP: one wires the editor in, the other wires the tools out."
        actions={<Tag tone="ok"><Dot state="connected" pulse />{acpClients.filter((x) => x.status === 'connected').length} live sessions</Tag>}
      />

      <PageBody className="space-y-4">
        <Panel eyebrow="Where ACP sits" title="Two protocols, two directions">
          <Ascii>{DIAGRAM}</Ascii>
        </Panel>

        <StatGrid cols={4}>
          <Stat label="Clients" value={acpClients.length} sub={`${acpClients.filter((x) => x.status === 'connected').length} connected`} icon={<Cable className="size-3" />} />
          <Stat label="Messages" value={acpClients.reduce((n, x) => n + x.messages, 0).toLocaleString()} sub="JSON-RPC, both directions" />
          <Stat label="Capabilities" value={acpCapabilities.length} sub="negotiated per session" />
          <Stat label="Permission modes" value={permissionModes.length} tone="warn" sub="the security boundary" />
        </StatGrid>

        <div className="flex min-h-[560px] gap-3">
          <div className="no-scrollbar w-[280px] shrink-0 overflow-y-auto rounded-md border border-line bg-surface">
            {acpClients.map((x) => (
              <ListRow key={x.id} active={x.id === sel} onClick={() => setSel(x.id)}>
                <div className="flex items-center gap-2">
                  <Dot state={x.status} pulse={x.status === 'connected'} />
                  <span className="truncate text-[12.5px] font-medium text-ink">{x.name}</span>
                </div>
                <div className="mt-1 flex items-center gap-2 text-[10.5px] text-dim">
                  <span className="truncate">{x.editor} {x.version}</span>
                  <span className="ml-auto tnum">{x.messages}</span>
                </div>
                <Mono className="mt-1 block truncate">{x.sessionId}</Mono>
              </ListRow>
            ))}
          </div>

          <div className="min-w-0 flex-1 space-y-3">
            <Panel eyebrow={c.editor} title={<span className="flex items-center gap-2">{c.name}<StatusText state={c.status} /></span>}>
              <div className="grid grid-cols-2 gap-x-6 md:grid-cols-4">
                <KV k="Version" v={c.version} />
                <KV k="Session" v={c.sessionId} mono />
                <KV k="Last ping" v={c.lastPing} />
                <KV k="Messages" v={c.messages.toLocaleString()} />
              </div>
              <SectionTitle className="mt-3 mb-1.5">Permission mode</SectionTitle>
              <div className="grid grid-cols-2 gap-1.5 lg:grid-cols-4">
                {permissionModes.map((m) => (
                  <button
                    key={m.id}
                    onClick={() => { setModes((s) => ({ ...s, [c.id]: m.id })); toast(`${c.name} → ${m.label}`); }}
                    className={cn('rounded-sm border px-2 py-1.5 text-left transition-colors',
                      mode === m.id ? 'border-brand bg-brand/10' : 'border-line bg-surface-2 hover:border-line-strong')}
                  >
                    <span className={cn('block font-mono text-[11.5px]', mode === m.id ? 'font-semibold text-brand' : 'text-ink-2')}>{m.label}</span>
                  </button>
                ))}
              </div>
              {(() => {
                const m = permissionModes.find((x) => x.id === mode);
                if (!m) return null;
                return (
                  <div className="mt-2.5 space-y-1.5 border-t border-line pt-2.5">
                    <p className="flex items-start gap-1.5 text-[11.5px] text-ok"><Check className="mt-px size-3 shrink-0" />{m.allows}</p>
                    <p className="flex items-start gap-1.5 text-[11.5px] text-warn"><X className="mt-px size-3 shrink-0" />{m.blocks}</p>
                  </div>
                );
              })()}
            </Panel>

            <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
              <Panel eyebrow="Handshake to first token" title="Session lifecycle" flush>
                <div className="divide-y divide-line">
                  {lifecycle.map((s, i) => (
                    <div key={s.method} className="flex items-start gap-2.5 px-3.5 py-2">
                      <span className="tnum mt-px w-4 shrink-0 text-right font-mono text-[10.5px] text-dim">{i + 1}</span>
                      {s.from === 'client'
                        ? <ArrowRight className="mt-px size-3 shrink-0 text-info" />
                        : <ArrowLeft className="mt-px size-3 shrink-0 text-brand" />}
                      <span className="min-w-0 flex-1">
                        <Mono tone={s.from === 'client' ? 'info' : 'brand'}>{s.method}</Mono>
                        <span className="mt-0.5 block text-[11px] text-dim">{s.note}</span>
                      </span>
                      <span className="tnum shrink-0 font-mono text-[10.5px] text-soft">{s.ms}ms</span>
                    </div>
                  ))}
                </div>
              </Panel>

              <Panel eyebrow="Negotiated at session/new" title="Capabilities" flush>
                <DataTable head={['Capability', 'Side', 'This client']}>
                  {acpCapabilities.map((cap) => {
                    const state = caps[cap.name] ?? 'no';
                    const I = YES[state];
                    return (
                      <Row key={cap.name}>
                        <Cell mono>{cap.name}
                          <span className="mt-0.5 block max-w-[240px] truncate font-sans text-[10.5px] text-dim">{cap.description}</span>
                        </Cell>
                        <Cell><Tag tone={cap.side === 'client' ? 'info' : 'brand'}>{cap.side}</Tag></Cell>
                        <Cell><I className={cn('size-3.5', TONE[state])} /></Cell>
                      </Row>
                    );
                  })}
                </DataTable>
              </Panel>
            </div>

            <Panel eyebrow="Live wire" title="JSON-RPC tail">
              <Ascii className="max-h-[260px] overflow-auto">{jsonRpcTail}</Ascii>
            </Panel>

            <Panel eyebrow={`${events.length} recent`} title="Message log" flush>
              {events.length === 0 ? <Empty title="No traffic on this session" /> : (
                <DataTable head={['At', 'Dir', 'Method', 'Detail']}>
                  {events.map((e, i) => (
                    <Row key={i}>
                      <Cell mono className="text-dim">{e.at}</Cell>
                      <Cell>{e.dir === 'in' ? <ArrowRight className="size-3 text-info" /> : <ArrowLeft className="size-3 text-brand" />}</Cell>
                      <Cell mono>{e.method}</Cell>
                      <Cell className="max-w-[520px] text-[11.5px] text-soft">{e.detail}</Cell>
                    </Row>
                  ))}
                </DataTable>
              )}
            </Panel>
          </div>
        </div>
      </PageBody>
    </Page>
  );
}
