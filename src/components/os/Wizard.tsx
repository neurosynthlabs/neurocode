import { useEffect, useRef, useState, type ReactNode } from 'react';
import { ArrowLeft, ArrowRight, Check, Loader2 } from 'lucide-react';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

/* ═══════════════════════════════════════════════════════════════
   WIZARD — a multi-step modal for anything that should not be a
   single form: onboarding a repository, registering a server.
   Steps are data; the wizard owns navigation, the stepper and the
   footer. A step blocks Next until `valid` is true, and completed
   steps stay clickable so the operator can go back and change them.
   ═══════════════════════════════════════════════════════════════ */

export interface WizardStep {
  id: string;
  title: string;
  /** One line under the title in the stepper. */
  hint?: string;
  content: ReactNode;
  /** `false` disables Next / Finish until the step is complete. */
  valid?: boolean;
  /** Shown beside the Next button while the step is not valid. */
  blocker?: string;
}

export function Wizard({
  open, onOpenChange, title, description, steps, onFinish, finishLabel = 'Finish', busy, className,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: string;
  steps: WizardStep[];
  onFinish: () => void;
  finishLabel?: string;
  busy?: boolean;
  className?: string;
}) {
  const [i, setI] = useState(0);
  const bodyRef = useRef<HTMLDivElement>(null);

  // Every time the wizard opens it starts from the first step.
  useEffect(() => { if (open) setI(0); }, [open]);

  const idx = Math.min(i, steps.length - 1);
  const step = steps[idx];
  const last = idx === steps.length - 1;
  const canNext = step.valid !== false;
  const go = (n: number) => setI(Math.max(0, Math.min(steps.length - 1, n)));

  // Put the caret in the step's first field — or on the step itself — every time the step changes.
  // Deferred a frame so it lands after Base UI's own open-focus instead of being overridden by it.
  useEffect(() => {
    if (!open) return;
    const id = requestAnimationFrame(() => {
      const body = bodyRef.current;
      if (!body) return;
      const field = body.querySelector<HTMLElement>(
        'input:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([disabled]), textarea:not([disabled]), select:not([disabled])',
      );
      (field ?? body).focus({ preventScroll: true });
    });
    return () => cancelAnimationFrame(id);
  }, [open, idx]);

  // Enter in a single-line field behaves like a form: next step, or finish on the last one.
  const onKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    const t = e.target as HTMLElement;
    if (e.key !== 'Enter' || e.shiftKey || t.tagName !== 'INPUT') return;
    e.preventDefault();
    if (!canNext) return;
    if (last) onFinish(); else go(idx + 1);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className={cn('gap-0 overflow-hidden p-0 sm:max-w-3xl', className)}>
        <DialogHeader className="border-b border-line px-5 pt-5 pb-4">
          <DialogTitle className="text-[15px] font-semibold tracking-[-0.01em] text-ink">{title}</DialogTitle>
          {description && <DialogDescription className="text-[12px] leading-relaxed text-soft">{description}</DialogDescription>}
        </DialogHeader>

        {/* Stepper */}
        <nav aria-label="Progress" className="border-b border-line bg-base px-5 py-3">
          <ol className="flex items-start">
            {steps.map((s, n) => {
              const done = n < idx;
              const current = n === idx;
              return (
                <li key={s.id} className="flex min-w-0 flex-1 items-start last:flex-none">
                  <button
                    type="button"
                    disabled={n > idx}
                    onClick={() => go(n)}
                    aria-current={current ? 'step' : undefined}
                    aria-label={`Step ${n + 1} of ${steps.length}: ${s.title}${done ? ' (completed)' : ''}`}
                    className="group flex min-w-0 items-start gap-2 text-left disabled:cursor-default"
                  >
                    <span
                      className={cn(
                        'grid size-6 shrink-0 place-items-center rounded-full border text-[11px] font-semibold transition-colors',
                        done && 'border-brand bg-brand text-brand-ink',
                        current && 'border-brand bg-brand/12 text-brand ring-2 ring-brand/15',
                        !done && !current && 'border-line-strong bg-surface text-dim',
                      )}
                    >
                      {done ? <Check className="size-3.5" strokeWidth={2.6} /> : n + 1}
                    </span>
                    <span className="hidden min-w-0 pt-0.5 sm:block">
                      <span className={cn('block truncate text-[12px] font-medium',
                        current ? 'text-ink' : done ? 'text-ink-2 group-hover:text-ink' : 'text-dim')}>
                        {s.title}
                      </span>
                      {s.hint && <span className="block truncate text-[10.5px] text-dim">{s.hint}</span>}
                    </span>
                  </button>
                  {n < steps.length - 1 && (
                    <span aria-hidden className={cn('mx-3 mt-3 h-px min-w-4 flex-1 transition-colors', done ? 'bg-brand' : 'bg-line')} />
                  )}
                </li>
              );
            })}
          </ol>
        </nav>

        {/* Body — keyed so each step animates in */}
        <p className="sr-only" aria-live="polite" aria-atomic="true">{`Step ${idx + 1} of ${steps.length}: ${step.title}`}</p>
        <div
          key={step.id}
          ref={bodyRef}
          tabIndex={-1}
          role="group"
          aria-label={step.title}
          onKeyDown={onKeyDown}
          className="animate-slide-up max-h-[58vh] min-h-[220px] overflow-y-auto px-5 py-4 focus-visible:outline-none"
        >
          {step.content}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between gap-3 border-t border-line bg-base px-5 py-3">
          <span className="truncate text-[11px] text-dim">Step {idx + 1} of {steps.length} · {step.title}</span>
          <div className="flex shrink-0 items-center gap-2">
            {!canNext && step.blocker && <span className="text-[11px] text-warn">{step.blocker}</span>}
            {idx === 0 ? (
              <Button size="sm" variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
            ) : (
              <Button size="sm" variant="outline" onClick={() => go(idx - 1)}><ArrowLeft className="size-3.5" />Back</Button>
            )}
            {last ? (
              <Button size="sm" disabled={!canNext || busy} onClick={onFinish}>
                {busy ? <Loader2 className="size-3.5 animate-spin" /> : <Check className="size-3.5" />}
                {finishLabel}
              </Button>
            ) : (
              <Button size="sm" disabled={!canNext} onClick={() => go(idx + 1)}>Next<ArrowRight className="size-3.5" /></Button>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
