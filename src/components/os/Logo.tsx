import { cn } from '@/lib/utils';

/* ═══════════════════════════════════════════════════════════════
   NEUROCODE — the mark.

   An "N" drawn as a synapse: four nodes wired by two axons, with
   the descending stroke breaking into a signal pulse. Geometric,
   monoline, legible down to 14px. Every colour is a theme token,
   so the mark recolours with the palette instead of fighting it.
   ═══════════════════════════════════════════════════════════════ */

export function LogoGlyph({ size = 32, className }: { size?: number; className?: string }) {
  return (
    <svg viewBox="0 0 32 32" width={size} height={size} className={cn('shrink-0', className)}
      fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      {/* left axon */}
      <path d="M8 25V9" strokeWidth="2.4" />
      {/* diagonal — the synapse crossing */}
      <path d="M8 9.6 24 22.4" strokeWidth="2.4" opacity=".55" />
      {/* right axon */}
      <path d="M24 7v16" strokeWidth="2.4" />
      {/* terminal nodes */}
      <circle cx="8" cy="7.2" r="2.5" fill="currentColor" stroke="none" />
      <circle cx="24" cy="24.8" r="2.5" fill="currentColor" stroke="none" />
      {/* synapse dots along the crossing */}
      <circle cx="13.4" cy="13.9" r="1.15" fill="currentColor" stroke="none" opacity=".9" />
      <circle cx="18.6" cy="18.1" r="1.15" fill="currentColor" stroke="none" opacity=".9" />
      {/* signal pulse — the "code" half */}
      <path d="M2.6 16h2.6" strokeWidth="1.7" opacity=".65" />
      <path d="M26.8 16h2.6" strokeWidth="1.7" opacity=".65" />
    </svg>
  );
}

/** The glyph on a solid brand tile — the app icon. */
export function LogoMark({ size = 34, className }: { size?: number; className?: string }) {
  return (
    <span
      className={cn('sheen relative grid shrink-0 place-items-center overflow-hidden', className)}
      style={{
        width: size, height: size,
        borderRadius: 'calc(var(--radius) * 1.15)',
        background: 'hsl(var(--primary))',
        color: 'hsl(var(--primary-foreground))',
        boxShadow:
          'inset 0 1px 0 hsl(var(--primary-foreground) / 0.24), 0 1px 3px hsl(var(--primary) / 0.38)',
      }}
    >
      <LogoGlyph size={size * 0.68} />
    </span>
  );
}

/** Mark + name. `onDark` switches the type to the sidebar's foreground. */
export function Wordmark({
  size = 34, sub = 'v0.9.4 · self-hosted', className, onDark, hideSub,
}: { size?: number; sub?: string; className?: string; onDark?: boolean; hideSub?: boolean }) {
  const ink = onDark ? 'var(--rail-ink)' : 'var(--os-ink)';
  const accent = onDark ? 'var(--rail-accent)' : 'var(--os-brand)';
  const dim = onDark ? 'var(--rail-dim)' : 'var(--os-dim)';
  return (
    <span className={cn('flex items-center gap-2.5', className)}>
      <LogoMark size={size} />
      <span className="min-w-0 leading-none">
        <span className="block truncate tracking-[-0.022em]" style={{ fontSize: size * 0.44 }}>
          <span className="font-bold" style={{ color: ink }}>Neuro</span>
          <span className="font-semibold" style={{ color: accent }}>Code</span>
        </span>
        {!hideSub && (
          <span className="mt-1 block truncate font-mono" style={{ fontSize: size * 0.27, color: dim }}>
            {sub}
          </span>
        )}
      </span>
    </span>
  );
}
