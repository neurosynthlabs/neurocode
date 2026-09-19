import { cn } from '@/lib/utils';

/* ═══════════════════════════════════════════════════════════════
   NEUROCODE — the mark.

   One ribbon drawn without lifting the pen: up, over, down and up
   again — an N — stopping short of the top, where a node sits on its
   own. The signal and the neuron it reaches. It stands alone, in its
   own marigold, and it is the same in every theme: the palette
   recolours the app around it, not it.

   Drawn on a 100-unit grid with a stroke heavy enough to hold at
   16 px (the favicon) and round enough to feel written, not built.
   ═══════════════════════════════════════════════════════════════ */

export const MARIGOLD = '#FF9F1C';
/** The app icon's tile: a rounded square whose corners ease into the sides instead of meeting them. */
export const TILE = 'M30 0H70C87 0 100 13 100 30V70C100 87 87 100 70 100H30C13 100 0 87 0 70V30C0 13 13 0 30 0Z';
const RIBBON = 'M27 76V38c0-11 12-14 18-5l10 17c6 9 18 6 18-5V44';
const NODE = { cx: 73, cy: 23, r: 7.5 };

function Ribbon({ color }: { color: string }) {
  return (
    <>
      <path d={RIBBON} fill="none" stroke={color} strokeWidth="13" strokeLinecap="round" strokeLinejoin="round" />
      <circle {...NODE} fill={color} />
    </>
  );
}

/** The mark on its own, in marigold — the logo. */
export function LogoSymbol({ size = 32, className, title }: { size?: number; className?: string; title?: string }) {
  return (
    <svg viewBox="0 0 100 100" width={size} height={size} className={cn('shrink-0', className)}
      role={title ? 'img' : undefined} aria-label={title} aria-hidden={title ? undefined : true}>
      <Ribbon color={MARIGOLD} />
    </svg>
  );
}

/** The app icon: the mark on a graphite tile, for places that need a filled square — the favicon, a home screen. */
export function LogoMark({ size = 34, className, title }: { size?: number; className?: string; title?: string }) {
  return (
    <svg viewBox="0 0 100 100" width={size} height={size} className={cn('shrink-0', className)}
      role={title ? 'img' : undefined} aria-label={title} aria-hidden={title ? undefined : true}>
      <path d={TILE} fill="#1B1C20" />
      <path d={TILE} fill="none" stroke="#FFFFFF" strokeOpacity=".08" strokeWidth="2" />
      <g transform="translate(16 16) scale(.68)"><Ribbon color={MARIGOLD} /></g>
    </svg>
  );
}

/** The mark in the current text colour, for places that draw it in one ink. */
export function LogoGlyph({ size = 32, className }: { size?: number; className?: string }) {
  return (
    <svg viewBox="0 0 100 100" width={size} height={size} className={cn('shrink-0', className)} aria-hidden>
      <Ribbon color="currentColor" />
    </svg>
  );
}

/** Mark and name. `onDark` switches the type to the sidebar's foreground. */
export function Wordmark({
  size = 34, sub = `Version ${__APP_VERSION__}`, className, onDark, hideSub,
}: { size?: number; sub?: string; className?: string; onDark?: boolean; hideSub?: boolean }) {
  const ink = onDark ? 'var(--rail-ink)' : 'var(--os-ink)';
  const dim = onDark ? 'var(--rail-dim)' : 'var(--os-dim)';
  return (
    <span className={cn('flex items-center gap-2', className)}>
      <LogoSymbol size={size * 0.86} />
      <span className="min-w-0 leading-none">
        <span className="block truncate font-semibold tracking-[-0.022em]" style={{ fontSize: size * 0.46, color: ink }}>
          NeuroCode
        </span>
        {!hideSub && (
          <span className="mt-1 block truncate" style={{ fontSize: size * 0.3, color: dim }}>
            {sub}
          </span>
        )}
      </span>
    </span>
  );
}
