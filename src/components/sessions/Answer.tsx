import { useState } from 'react';
import Markdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Check, Copy } from 'lucide-react';
import { cn } from '@/lib/utils';

/* An answer as a person reads one in a chat: Markdown, with headings, lists, tables and code blocks that
   copy. Raw HTML in an answer is shown as the text it is, never run — react-markdown's default. */

function CodeBlock({ lang, code }: { lang: string; code: string }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    void navigator.clipboard?.writeText(code).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1400);
    }, () => undefined);
  };
  return (
    <div className="my-3 overflow-hidden rounded-xl border border-line bg-surface-2/60">
      <div className="flex items-center justify-between border-b border-line/70 px-3 py-1.5 text-[11.5px] text-dim">
        <span className="font-mono">{lang || 'text'}</span>
        <button type="button" onClick={copy} className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 hover:bg-surface-2 hover:text-ink">
          {copied ? <Check className="size-3" /> : <Copy className="size-3" />}{copied ? 'Copied' : 'Copy'}
        </button>
      </div>
      <pre className="overflow-x-auto px-3.5 py-3 font-mono text-[12.5px] leading-relaxed text-ink"><code>{code}</code></pre>
    </div>
  );
}

const PARTS: Components = {
  p: ({ children }) => <p className="my-2 first:mt-0 last:mb-0">{children}</p>,
  h1: ({ children }) => <h3 className="mt-4 mb-2 text-[16px] font-semibold text-ink first:mt-0">{children}</h3>,
  h2: ({ children }) => <h3 className="mt-4 mb-2 text-[15px] font-semibold text-ink first:mt-0">{children}</h3>,
  h3: ({ children }) => <h4 className="mt-3 mb-1.5 text-[14.5px] font-semibold text-ink first:mt-0">{children}</h4>,
  h4: ({ children }) => <h4 className="mt-3 mb-1.5 text-[14px] font-semibold text-ink first:mt-0">{children}</h4>,
  ul: ({ children }) => <ul className="my-2 list-disc space-y-1 pl-5 marker:text-dim">{children}</ul>,
  ol: ({ children }) => <ol className="my-2 list-decimal space-y-1 pl-5 marker:text-dim">{children}</ol>,
  li: ({ children }) => <li className="pl-0.5">{children}</li>,
  a: ({ href, children }) => <a href={href} target="_blank" rel="noreferrer" className="text-brand underline-offset-2 hover:underline">{children}</a>,
  blockquote: ({ children }) => <blockquote className="my-2 border-l-2 border-line-strong pl-3 text-soft">{children}</blockquote>,
  hr: () => <hr className="my-4 border-line" />,
  table: ({ children }) => <div className="my-3 overflow-x-auto"><table className="w-full border-collapse text-[13px]">{children}</table></div>,
  th: ({ children }) => <th className="border-b border-line px-2.5 py-1.5 text-left font-semibold text-ink">{children}</th>,
  td: ({ children }) => <td className="border-b border-line/60 px-2.5 py-1.5 align-top">{children}</td>,
  pre: ({ children }) => <>{children}</>,
  code: ({ className, children }) => {
    const text = String(children ?? '');
    const lang = /language-([\w+-]+)/.exec(className ?? '')?.[1];
    // A fenced block carries a language or spans lines; everything else is inline code.
    if (lang || text.includes('\n')) return <CodeBlock lang={lang ?? ''} code={text.replace(/\n$/, '')} />;
    return <code className="rounded-md bg-surface-2 px-1.5 py-0.5 font-mono text-[0.9em] text-ink">{children}</code>;
  },
};

export function Answer({ text, className }: { text: string; className?: string }) {
  return (
    <div className={cn('text-[14.5px] leading-[1.7] break-words text-ink', className)}>
      <Markdown remarkPlugins={[remarkGfm]} components={PARTS}>{text}</Markdown>
    </div>
  );
}
