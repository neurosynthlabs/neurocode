import { useState } from 'react';
import { Check, Globe, Loader2, Plug, Search, ShieldQuestion, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Mono, Panel, Tag } from '@/components/os';
import type { ChatMessage } from '@/lib/api';
import type { PermitDecision } from '@/lib/live/sessions';

/* A tool call that waits for a person. Nothing was sent: the card says what the model wants to do, on
   what, and why the rules asked — and offers exactly three answers. "Allow for this session" says what
   it would cover (every page on a host, every search, one MCP tool with any arguments), so a person
   knows what they are granting. Once answered, the card stays in the transcript as a one-line record. */

const WHAT: Record<string, { icon: typeof Globe; verb: string }> = {
  web_fetch: { icon: Globe, verb: 'read a web page' },
  web_search: { icon: Search, verb: 'search the web' },
  mcp: { icon: Plug, verb: 'call an MCP tool' },
  read_file: { icon: ShieldQuestion, verb: 'read a file a rule asks about' },
};

export function PermissionCard({ message, canAnswer, onAnswer }: {
  message: ChatMessage;
  canAnswer: boolean;
  onAnswer: (decision: PermitDecision) => Promise<void>;
}) {
  const [busy, setBusy] = useState<PermitDecision | null>(null);
  const p = message.permission;
  if (!p) return null;
  const what = WHAT[p.tool] ?? { icon: ShieldQuestion, verb: `use ${p.tool}` };
  const Icon = what.icon;
  const args = message.arguments && p.tool === 'mcp' ? (message.arguments as { arguments?: unknown }).arguments : null;

  if (p.state !== 'pending') {
    const label = p.state === 'allowed' ? (p.scope === 'session' ? 'Allowed for this session' : 'Allowed once')
      : p.state === 'refused' ? 'Refused' : 'Copied, not answered here';
    return (
      <div className="flex items-center gap-2 rounded-lg border border-line/70 bg-surface-2/40 px-3 py-1.5 text-[12.5px] text-ink-2">
        <Icon className="size-3.5 shrink-0 text-dim" />
        <span className="min-w-0 flex-1 truncate">{what.verb} · <Mono>{p.subject}</Mono></span>
        <Tag tone={p.state === 'allowed' ? 'ok' : p.state === 'refused' ? 'warn' : 'neutral'}>{label}</Tag>
        {p.decidedBy && <span className="hidden text-[11.5px] text-dim sm:inline">by {p.decidedBy}</span>}
      </div>
    );
  }

  const answer = async (d: PermitDecision) => {
    setBusy(d);
    try { await onAnswer(d); } finally { setBusy(null); }
  };

  return (
    <Panel className="border-warn/50" eyebrow="Waiting · nothing sent"
      title={<span className="flex items-center gap-2"><Icon className="size-4 text-warn" />It wants to {what.verb}</span>}>
      <div className="space-y-2.5">
        <p className="text-[13px] break-all text-ink"><Mono>{p.subject}</Mono></p>
        {args !== null && args !== undefined && (
          <pre className="max-h-[140px] overflow-auto rounded-lg bg-surface-2/60 p-2 font-mono text-[12px] whitespace-pre-wrap text-ink-2">{JSON.stringify(args, null, 2)}</pre>
        )}
        {message.why && <p className="text-[12.5px] text-ink-2">Its reason: {message.why}</p>}
        <p className="text-[12px] text-dim">{p.why}</p>
        {canAnswer ? (
          <div className="flex flex-wrap items-center gap-2 pt-1">
            <Button size="sm" disabled={busy !== null} onClick={() => void answer('once')}>
              {busy === 'once' ? <Loader2 className="size-3.5 animate-spin" /> : <Check className="size-3.5" />}Allow once
            </Button>
            <Button size="sm" variant="outline" disabled={busy !== null} onClick={() => void answer('session')} title={`Covers ${p.covers}, until this session ends`}>
              {busy === 'session' ? <Loader2 className="size-3.5 animate-spin" /> : <Check className="size-3.5" />}Allow for session
            </Button>
            <Button size="sm" variant="ghost" disabled={busy !== null} onClick={() => void answer('refuse')}>
              {busy === 'refuse' ? <Loader2 className="size-3.5 animate-spin" /> : <X className="size-3.5" />}Refuse
            </Button>
            <span className="text-[11.5px] text-dim">For this session: {p.covers}</span>
          </div>
        ) : (
          <p className="text-[12.5px] text-dim">Someone who may use sessions answers this.</p>
        )}
      </div>
    </Panel>
  );
}
