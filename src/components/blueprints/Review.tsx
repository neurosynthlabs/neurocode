import { useState } from 'react';
import { Check, Cpu, Loader2, MessageSquareText, X } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Empty, Panel, Tag } from '@/components/os';
import { ApiError } from '@/lib/api';
import { blueprintsApi, type BlueprintDoc, type Change } from '@/lib/live/blueprints';
import { ago } from '@/lib/time';
import { cn } from '@/lib/utils';

/* "Ask for a review": a model reads the answers and the architecture and proposes changes, each with the
   place it touches, what is there now, what it would become, and why. Nothing is applied until a person
   accepts it; the accepted ones are applied together against the revision the person was looking at. */

const failed = (e: unknown) => (e instanceof ApiError ? e.message : 'The API did not answer.');

/** A value as it reads in a change card: text as itself, anything else as compact JSON. */
function shown(v: unknown): string {
  if (v === null || v === undefined) return 'nothing';
  if (typeof v === 'string') return v || '(empty)';
  return JSON.stringify(v, null, 1).replace(/\n\s*/g, ' ');
}

export function ReviewPanel({ bp, canDesign, dirty, onChanged }: {
  bp: BlueprintDoc;
  canDesign: boolean;
  /** Unsaved edits: a review of them would be a review of something else. */
  dirty: boolean;
  onChanged: (bp: BlueprintDoc) => void;
}) {
  const [asking, setAsking] = useState(false);
  const [applying, setApplying] = useState(false);
  const [choice, setChoice] = useState<Record<string, 'accept' | 'reject'>>({});
  const review = bp.review;
  const locked = bp.status === 'scaffolded';

  const ask = async () => {
    setAsking(true);
    try {
      const next = await blueprintsApi.suggest(bp.id, bp.revision);
      setChoice({});
      onChanged(next);
      const n = next.review?.changes.length ?? 0;
      toast.success(n ? `${n} change${n === 1 ? '' : 's'} proposed` : 'Reviewed — no change proposed', { description: next.review?.model });
    } catch (e) {
      toast.error('No review', { description: failed(e) });
    } finally {
      setAsking(false);
    }
  };

  // A second click on the same answer takes it back.
  const decide = (id: string, v: 'accept' | 'reject') => setChoice((x) => {
    const next = { ...x };
    if (next[id] === v) delete next[id]; else next[id] = v;
    return next;
  });
  const open = review ? review.changes.filter((c) => !review.decided[c.id]) : [];
  const accepted = open.filter((c) => choice[c.id] === 'accept');
  const rejected = open.filter((c) => choice[c.id] === 'reject').map((c) => c.id);

  const apply = async () => {
    setApplying(true);
    try {
      const next = await blueprintsApi.apply(bp.id, bp.revision, accepted, rejected);
      setChoice({});
      onChanged(next);
      toast.success(accepted.length ? `${accepted.length} change${accepted.length === 1 ? '' : 's'} applied` : 'Decisions recorded',
        { description: accepted.length ? `Now revision ${next.revision}` : undefined });
    } catch (e) {
      toast.error('Not applied', { description: failed(e) });
    } finally {
      setApplying(false);
    }
  };

  const askButton = (
    <Button size="sm" variant={review ? 'outline' : 'default'} disabled={!canDesign || asking || dirty || locked} onClick={() => void ask()}
      title={!canDesign ? 'Needs the plans:compile permission' : dirty ? 'Save your changes first' : undefined}>
      {asking ? <Loader2 className="size-3.5 animate-spin" /> : <MessageSquareText className="size-3.5" />}
      {review ? 'Review again' : 'Ask for a review'}
    </Button>
  );

  if (!review) {
    return (
      <Panel>
        <Empty icon={<MessageSquareText className="size-6" />} title="No review yet"
          hint={locked ? 'Scaffolded, so kept as built.'
            : 'A model proposes changes; you accept each. Needs a model: Models → Keys.'}
          action={!locked && askButton} />
        {dirty && <p className="pb-2 text-center text-[12.5px] text-warn">Save first: the review reads the saved blueprint.</p>}
      </Panel>
    );
  }

  const stale = review.revision !== bp.revision;
  return (
    <div className="space-y-4">
      <Panel title="The review" actions={askButton}>
        <p className="text-[14px] leading-relaxed text-ink-2">{review.summary || 'The model wrote no summary.'}</p>
        <p className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12.5px] text-dim">
          <span className="inline-flex items-center gap-1.5"><Cpu className="size-3.5" />{review.model} · {(review.ms / 1000).toFixed(1)} s</span>
          <span>asked by {review.by} {ago(review.at)}</span>
          <span>against revision {review.revision}</span>
        </p>
        {stale && (
          <p className="mt-3 rounded-lg bg-warn/10 px-3 py-2 text-[12.5px] text-warn">
            The blueprint is now at revision {bp.revision}. Changes to moved values are refused; review again.
          </p>
        )}
      </Panel>

      <Panel title={`Proposed changes · ${review.changes.length}`} flush actions={open.length > 0 && canDesign && !locked && (
        <Button size="sm" disabled={applying || dirty || (accepted.length + rejected.length === 0)} onClick={() => void apply()}
          title={dirty ? 'Save your changes first' : undefined}>
          {applying && <Loader2 className="size-3.5 animate-spin" />}
          {accepted.length ? `Apply ${accepted.length} accepted` : 'Record decisions'}
        </Button>
      )}>
        {review.changes.length === 0 ? (
          <p className="px-5 py-6 text-[13px] text-dim">The model proposed nothing that fits this blueprint.</p>
        ) : (
          <div className="divide-y divide-line/60">
            {review.changes.map((c: Change) => {
              const decided = review.decided[c.id];
              const mine = choice[c.id];
              return (
                <div key={c.id} className="px-5 py-4">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <code className="min-w-0 font-mono text-[12.5px] break-all text-brand">{c.path}</code>
                    {decided ? (
                      <Tag tone={decided === 'accepted' ? 'ok' : 'neutral'}>{decided === 'accepted' ? 'Accepted' : 'Rejected'}</Tag>
                    ) : canDesign && !locked ? (
                      <div className="flex shrink-0 gap-1.5">
                        <button onClick={() => decide(c.id, 'accept')} aria-pressed={mine === 'accept'}
                          className={cn('inline-flex h-7 items-center gap-1 rounded-full border px-2.5 text-[12.5px] transition-colors',
                            mine === 'accept' ? 'border-ok/50 bg-ok/12 text-ok' : 'border-line text-soft hover:text-ink')}>
                          <Check className="size-3.5" />Accept
                        </button>
                        <button onClick={() => decide(c.id, 'reject')} aria-pressed={mine === 'reject'}
                          className={cn('inline-flex h-7 items-center gap-1 rounded-full border px-2.5 text-[12.5px] transition-colors',
                            mine === 'reject' ? 'border-danger/40 bg-danger/10 text-danger' : 'border-line text-soft hover:text-ink')}>
                          <X className="size-3.5" />Reject
                        </button>
                      </div>
                    ) : null}
                  </div>
                  <div className="mt-2 grid grid-cols-1 gap-2 text-[13px] sm:grid-cols-2">
                    <div className="min-w-0 rounded-lg bg-danger/6 px-3 py-2">
                      <span className="block text-[11.5px] text-dim">Now</span>
                      <span className="block break-words text-ink-2">{shown(c.from)}</span>
                    </div>
                    <div className="min-w-0 rounded-lg bg-ok/7 px-3 py-2">
                      <span className="block text-[11.5px] text-dim">Proposed</span>
                      <span className="block break-words text-ink">{shown(c.to)}</span>
                    </div>
                  </div>
                  {c.why && <p className="mt-2 text-[13px] leading-relaxed text-soft">{c.why}</p>}
                </div>
              );
            })}
          </div>
        )}
        {review.dropped.length > 0 && (
          <details className="border-t border-line/60 px-5 py-3 text-[12.5px] text-dim">
            <summary className="cursor-pointer">{review.dropped.length} proposal{review.dropped.length === 1 ? '' : 's'} left out: did not fit</summary>
            <ul className="mt-2 space-y-1">{review.dropped.map((d) => <li key={d} className="font-mono text-[12px] break-all">{d}</li>)}</ul>
          </details>
        )}
      </Panel>
    </div>
  );
}
