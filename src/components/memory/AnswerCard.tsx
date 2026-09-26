import { Link } from 'react-router-dom';
import { Sparkles, X } from 'lucide-react';
import type { AskAnswer } from '@/lib/api';

/** Memory's answer to a question: the words, the facts it cites (each a link to the fact), and what wrote it. */
export function AnswerCard({ answer, onClose }: { answer: AskAnswer & { q: string }; onClose?: () => void }) {
  return (
    <div className="animate-slide-up rounded-2xl border border-line bg-surface p-5">
      <div className="flex items-start gap-3">
        <span className="grid size-8 shrink-0 place-items-center rounded-full bg-brand/12 text-brand"><Sparkles className="size-4" /></span>
        <div className="min-w-0 flex-1">
          <p className="text-[12.5px] text-dim">{answer.q}</p>
          <p className="mt-1.5 text-[14.5px] leading-relaxed whitespace-pre-line text-ink">{answer.answer}</p>
          {answer.citations.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-1.5">
              {answer.citations.map((c) => (
                <Link
                  key={c.ref} to={`/memory?ref=${c.ref}`} title={c.title}
                  className="inline-flex max-w-full items-center gap-1.5 rounded-full border border-line bg-surface-2/60 px-2.5 py-1 text-[12.5px] text-ink-2 transition-colors hover:bg-surface-2 hover:text-ink"
                >
                  <span className="font-mono text-[11.5px] text-brand">{c.ref}</span>
                  <span className="truncate">{c.title}</span>
                </Link>
              ))}
            </div>
          )}
          <p className="mt-3 text-[12px] text-dim">
            {answer.provider === 'rules'
              ? 'No model answered: the matching facts, quoted.'
              : `${answer.model} · ${(answer.ms / 1000).toFixed(1)} s`}
          </p>
        </div>
        {onClose && (
          <button onClick={onClose} aria-label="Close the answer"
            className="grid size-7 shrink-0 place-items-center rounded-full text-dim transition-colors hover:bg-surface-2 hover:text-ink">
            <X className="size-4" />
          </button>
        )}
      </div>
    </div>
  );
}
