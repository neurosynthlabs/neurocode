import type { Tone } from '@/components/os';

/** "just now", "12 min ago", "3 h ago", "2 d ago". */
export function ago(iso: string | null | undefined): string {
  if (!iso) return 'never';
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (Number.isNaN(s)) return iso;
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86_400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86_400)} d ago`;
}

export const baseName = (path: string) => path.split('/').pop() || path;

export const tokens = (n: number) => (n < 1000 ? String(n) : n < 1_000_000 ? `${(n / 1000).toFixed(1)}k` : `${(n / 1_000_000).toFixed(2)}M`);

export const bytes = (n: number) =>
  n < 1024 ? `${n} B` : n < 1_048_576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1_048_576).toFixed(1)} MB`;

/** The capsule colour for a symbol kind or an edge kind. */
export const KIND_TONE: Record<string, Tone> = {
  class: 'brand', interface: 'info', component: 'violet', function: 'ok', method: 'neutral', type: 'info', enum: 'info',
  constant: 'neutral', table: 'ok', procedure: 'warn', view: 'info', trigger: 'danger', file: 'neutral',
  imports: 'neutral', uses: 'neutral', reads: 'info', writes: 'warn', calls: 'violet',
};

/** The colour of a graph node's kind. */
export const NODE_COLOR: Record<string, string> = {
  module: 'var(--os-brand)', table: 'var(--os-ok)', procedure: 'var(--os-warn)', view: 'var(--os-info)',
  function: 'var(--os-violet)', trigger: 'var(--os-danger)',
};
