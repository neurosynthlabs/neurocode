import { useState } from 'react';
import { Copy, RotateCcw, Unplug } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { LogoSymbol } from '@/components/os/Logo';
import { API_BASE } from '@/lib/api';
import { cn } from '@/lib/utils';

const START = 'npm run dev:start';

/* Where the API was looked for, spelled out: "/api" alone does not tell anyone which host or port. */
const where = () => (API_BASE.startsWith('/') ? `${window.location.origin}${API_BASE}` : API_BASE);

/**
 * The one screen for an API that cannot be reached. Nothing else can honestly be shown then — the
 * workspace lives behind the API — so it says what happened, how to start the API on this machine, and
 * offers to try again. `screen` stands alone before anyone is signed in; `panel` fills the shell's main
 * area when the workspace stopped loading after sign-in.
 */
export function NotConnected({ reason, onRetry, variant = 'screen' }: {
  /** What went wrong, in words, e.g. "Nothing answered at /api/auth/status." */
  reason: string | null;
  onRetry: () => void | Promise<void>;
  variant?: 'screen' | 'panel';
}) {
  const [busy, setBusy] = useState(false);

  const retry = async () => {
    setBusy(true);
    try {
      await onRetry();
    } finally {
      setBusy(false);
    }
  };

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(START);
      toast.success('Copied', { description: START });
    } catch (e) {
      console.warn('[NeuroCode] the clipboard refused the start command:', e);
      toast.error('Could not copy', { description: `Type it instead: ${START}` });
    }
  };

  return (
    <div role="alert" className={cn('grid w-full place-items-center overflow-y-auto p-6', variant === 'screen' ? 'h-full bg-bg' : 'h-full')}>
      <div className="w-full max-w-[440px]">
        <div className="flex items-center gap-3">
          {variant === 'screen'
            ? <LogoSymbol size={36} />
            : <span className="grid size-9 place-items-center rounded-lg bg-surface-2 text-dim"><Unplug className="size-[18px]" /></span>}
          <div className="min-w-0">
            <h1 className="text-[17px] font-semibold text-ink">Not connected</h1>
            <p className="truncate text-[12.5px] text-dim">{where()}</p>
          </div>
        </div>

        <p className="mt-4 text-[13.5px] leading-relaxed text-soft">
          The API did not answer, and the workspace lives behind it.
        </p>
        {reason && <p className="mt-2 font-mono text-[12px] leading-relaxed break-words text-dim">{reason}</p>}

        <div className="mt-5 text-[12.5px] font-medium text-ink-2">Start it from the project folder</div>
        <div className="mt-1.5 flex items-center gap-2 rounded-lg border border-line bg-surface-2 py-1.5 pr-1.5 pl-3">
          <code className="min-w-0 flex-1 truncate font-mono text-[13px] text-ink">{START}</code>
          <Button size="xs" variant="ghost" onClick={() => void copy()} aria-label="Copy the command"><Copy className="size-3" />Copy</Button>
        </div>
        <p className="mt-1.5 text-[12px] leading-relaxed text-dim">
          Starts the API and this app; Postgres must be running first. If the API is up,{' '}
          <code className="font-mono">./scripts/dev.sh logs api</code> says why it is silent.
        </p>

        <Button className="mt-5" onClick={() => void retry()} disabled={busy}>
          <RotateCcw className={cn('size-3.5', busy && 'animate-spin')} />{busy ? 'Trying…' : 'Retry'}
        </Button>
      </div>
    </div>
  );
}
