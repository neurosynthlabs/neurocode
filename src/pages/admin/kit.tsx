import { Copy, Info, RotateCw, TriangleAlert } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Empty, KV } from '@/components/os';
import type { RoleDoc } from '@/lib/api';
import { cn } from '@/lib/utils';
import { initials } from './load';

/** Says the screen shows sample data, and what it takes to make it real. */
export function DemoNote({ what }: { what: string }) {
  return (
    <div className="mb-5 flex items-start gap-2.5 rounded-xl border border-line/70 bg-surface px-4 py-3 text-[13px] leading-relaxed text-soft">
      <Info className="mt-0.5 size-4 shrink-0 text-info" />
      <span>
        Sample data. {what} need the local API: run <code className="font-mono text-[12.5px] text-ink-2">npm run dev:start</code>, then sign in.
      </span>
    </div>
  );
}

export function LoadError({ error, onRetry }: { error: string; onRetry: () => void }) {
  return (
    <Empty
      icon={<TriangleAlert className="size-6" />} title="This screen did not load" hint={error}
      action={<Button size="sm" variant="outline" onClick={onRetry}><RotateCw className="size-3.5" />Try again</Button>}
    />
  );
}

export function Loading() {
  return (
    <div className="space-y-2.5" aria-busy="true" aria-label="Loading">
      {[0, 1, 2, 3].map((i) => <div key={i} className="h-14 animate-pulse rounded-xl bg-surface-2/70" />)}
    </div>
  );
}

export function Avatar({ name, size = 32 }: { name: string; size?: number }) {
  return (
    <span
      className="grid shrink-0 place-items-center rounded-full bg-brand/12 font-semibold text-brand"
      style={{ width: size, height: size, fontSize: Math.round(size * 0.36) }}
    >
      {initials(name)}
    </span>
  );
}

/** Roles as a checklist, each with what it is for. Only an Owner may hand out the Owner role. */
export function RolePicker({ roles, value, onChange, isOwner }: {
  roles: RoleDoc[]; value: string[]; onChange: (next: string[]) => void; isOwner: boolean;
}) {
  return (
    <div className="max-h-[260px] space-y-1.5 overflow-y-auto pr-0.5">
      {roles.map((r) => {
        const locked = r.id === 'owner' && !isOwner && !value.includes('owner');
        const on = value.includes(r.id);
        return (
          <label
            key={r.id}
            className={cn('flex items-start gap-3 rounded-lg border px-3 py-2.5 transition-colors',
              on ? 'border-brand/45 bg-brand/6' : 'border-line hover:bg-surface-2/60',
              locked ? 'cursor-not-allowed opacity-55' : 'cursor-pointer')}
          >
            <input
              type="checkbox" className="mt-0.5 size-4 shrink-0 accent-[var(--os-brand)]" checked={on} disabled={locked}
              onChange={(e) => onChange(e.target.checked ? [...value, r.id] : value.filter((x) => x !== r.id))}
            />
            <span className="min-w-0">
              <span className="block text-[13.5px] font-medium text-ink">{r.name}</span>
              <span className="block text-[12.5px] leading-snug text-soft">{locked ? 'Only an Owner can grant this role.' : r.description}</span>
            </span>
          </label>
        );
      })}
    </div>
  );
}

/** Sign-in details to hand over once. They are not shown again. */
export function Credentials({ email, password }: { email: string; password: string }) {
  const copy = () => {
    navigator.clipboard.writeText(`Email: ${email}\nTemporary password: ${password}`)
      .then(() => toast.success('Copied'), () => toast.error('Copying is blocked here. Select the text instead.'));
  };
  return (
    <div className="rounded-xl border border-line bg-surface-2/50 px-4 py-3">
      <KV k="Email" v={email} mono />
      <KV k="Temporary password" v={<span className="select-all">{password}</span>} mono />
      <div className="mt-3 flex items-center justify-between gap-3">
        <span className="text-[12px] text-dim">Shown once. Share it privately.</span>
        <Button type="button" size="sm" variant="outline" onClick={copy}><Copy className="size-3.5" />Copy both</Button>
      </div>
    </div>
  );
}
