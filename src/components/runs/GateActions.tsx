import { useState } from 'react';
import { Check, CheckCheck, Loader2, MessageSquareReply, Play, ShieldCheck, Square, X } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { ApiError } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { useDataActions } from '@/lib/data';
import { gateKind, runtimeApi, type GateScope } from '@/lib/live/runtime';
import type { ApprovalRequest } from '@/types';

/* The answers a gate takes, in one place for the approvals inbox and the run screen. A gate is not always
   yes or no: a tool rule's ask is allowed once, for the rest of the run, or always in the project (which
   writes a project tool rule, so it needs rules:manage); an agent's question is answered in words; a
   plan dispatched step by step asks before each step. What each answer does is said on its button. */

const failed = (e: unknown) => (e instanceof ApiError ? e.message : 'The local API did not answer.');

/** What an answer did, in the words the toast uses. */
const SAID: Record<string, { ok: string; no: string }> = {
  tests: { ok: 'The command runs now, and this project will not ask again.', no: 'It will not run here; the run carries on without it.' },
  signature: { ok: 'The branch is yours to merge.', no: 'The branch and its worktree are removed.' },
  step: { ok: 'The step runs now.', no: 'The run stops here; its branch is kept.' },
  command: { ok: 'The command runs now.', no: 'The command does not run; the step is skipped.' },
  edit: { ok: 'The files are written in the run’s worktree.', no: 'None of the step’s files are written.' },
  question: { ok: 'The step goes again with your answer, kept in memory.', no: 'The step is skipped.' },
  other: { ok: 'Your signature is recorded against this action.', no: 'Your answer is recorded.' },
};
const SCOPE_SAID: Record<GateScope, string> = {
  once: 'Allowed once, for this step.',
  run: 'Allowed for the rest of this run.',
  project: 'Always allowed in this project — written as a project tool rule.',
};

export function GateActions({ approval }: { approval: ApprovalRequest }) {
  const { can } = useAuth();
  // This is a row of buttons on somebody else's screen; it reads nothing of the workspace, so it takes
  // the actions alone and does not re-render while a run streams.
  const { decide: record } = useDataActions();
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);
  const [words, setWords] = useState('');
  const kind = gateKind(approval);
  const may = can('approvals:decide');

  /** The yes-or-no gates go through the store, which shows the answer at once and takes it back on a refusal. */
  const plain = async (v: 'approve' | 'deny') => {
    setBusy(true);
    const ok = await record(approval.ref, v);
    setBusy(false);
    if (!ok) return;
    setSent(true);
    const said = SAID[kind] ?? SAID.other;
    if (v === 'approve') toast.success(`${approval.ref} approved`, { description: said.ok });
    else toast(`${approval.ref} denied`, { description: said.no });
  };
  /** The answers only the runtime's own gates take; the stream brings the decided gate back to every screen. */
  const answer = async (v: 'approve' | 'deny', opts: { scope?: GateScope; answer?: string } = {}) => {
    setBusy(true);
    try {
      await runtimeApi.decide(approval.ref, v, opts);
      setSent(true);
      const said = SAID[kind] ?? SAID.other;
      if (v === 'deny') toast(`${approval.ref} refused`, { description: said.no });
      else toast.success(kind === 'question' ? `${approval.ref} answered` : `${approval.ref} allowed`,
        { description: opts.scope ? `${SCOPE_SAID[opts.scope]} ${said.ok}` : said.ok });
    } catch (e) {
      toast.error('Not decided', { description: failed(e) });
    } finally {
      setBusy(false);
    }
  };

  if (!may) {
    return <p className="mt-3 border-t border-line pt-3 text-[12.5px] text-dim">Answering this needs the approvals:decide permission.</p>;
  }
  const off = busy || sent;
  const spin = busy ? <Loader2 className="size-3.5 animate-spin" /> : null;

  return (
    <div className="mt-3 border-t border-line pt-3">
      {kind === 'question' && (
        <label className="mb-2.5 block">
          <span className="mb-1.5 block text-[12.5px] text-dim">Your answer, kept in this project’s memory</span>
          <Textarea value={words} onChange={(e) => setWords(e.target.value)} maxLength={4000} rows={3} disabled={off}
            placeholder="Say what to do" className="text-[13.5px]" />
        </label>
      )}
      <div className="flex flex-wrap items-center gap-2">
        {(kind === 'command' || kind === 'edit') && (
          <>
            <Button size="sm" onClick={() => void answer('approve', { scope: 'once' })} disabled={off}>{spin ?? <Check className="size-3.5" />}Allow once</Button>
            <Button size="sm" variant="outline" onClick={() => void answer('approve', { scope: 'run' })} disabled={off}
              title="Later asks for the same thing in this run are allowed">
              <CheckCheck className="size-3.5" />Allow for this run
            </Button>
            {can('rules:manage') && (
              <Button size="sm" variant="outline" onClick={() => void answer('approve', { scope: 'project' })} disabled={off}
                title="Writes an audited project tool rule. Edit it in Permissions → Tool rules.">
                <ShieldCheck className="size-3.5" />Always allow in this project
              </Button>
            )}
            <Button size="sm" variant="destructive" onClick={() => void answer('deny')} disabled={off}><X className="size-3.5" />Refuse</Button>
          </>
        )}
        {kind === 'question' && (
          <>
            <Button size="sm" onClick={() => void answer('approve', { answer: words.trim() })} disabled={off || !words.trim()}>
              {spin ?? <MessageSquareReply className="size-3.5" />}Answer
            </Button>
            <Button size="sm" variant="outline" onClick={() => void answer('deny')} disabled={off}><X className="size-3.5" />Decline and skip</Button>
          </>
        )}
        {kind === 'step' && (
          <>
            <Button size="sm" onClick={() => void plain('approve')} disabled={off}>{spin ?? <Play className="size-3.5" />}Continue</Button>
            <Button size="sm" variant="destructive" onClick={() => void plain('deny')} disabled={off}><Square className="size-3.5" />Stop the run</Button>
          </>
        )}
        {kind === 'tests' && (
          <>
            <Button size="sm" onClick={() => void plain('approve')} disabled={off}>{spin ?? <Check className="size-3.5" />}Allow — remembered for this project</Button>
            <Button size="sm" variant="destructive" onClick={() => void plain('deny')} disabled={off}><X className="size-3.5" />Refuse</Button>
          </>
        )}
        {(kind === 'signature' || kind === 'other') && (
          <>
            <Button size="sm" onClick={() => void plain('approve')} disabled={off}>{spin ?? <Check className="size-3.5" />}Approve</Button>
            <Button size="sm" variant="destructive" onClick={() => void plain('deny')} disabled={off}><X className="size-3.5" />Deny</Button>
          </>
        )}
        <span className="text-[12px] text-dim sm:ml-auto">{sent ? 'Decided. The run picks it up from here.' : 'Recorded under your name, and final.'}</span>
      </div>
    </div>
  );
}
