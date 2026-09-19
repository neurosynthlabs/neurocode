import { useState, type SyntheticEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { ChevronsUpDown, KeyRound, LogOut, Settings, type LucideIcon } from 'lucide-react';
import { toast } from 'sonner';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Field, Tag } from '@/components/os';
import { ApiError, api } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { roleName, useAccess } from '@/lib/access';
import { useData } from '@/lib/data';
import { cn } from '@/lib/utils';

const RADIUS = 'calc(var(--radius) * 1.1)';
const initials = (name: string) => name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join('').toUpperCase() || '?';

/** The person at the foot of the sidebar: who is signed in, what they hold, and the way out. */
export function AccountMenu({ mini }: { mini: boolean }) {
  const nav = useNavigate();
  const { user, workspace, logout } = useAuth();
  const { mode, reconnecting, health } = useData();
  const { catalogue } = useAccess();
  const [open, setOpen] = useState(false);
  const [changing, setChanging] = useState(false);
  const name = user?.name ?? 'You';
  // Where the workspace really is, as the API reports it: Postgres may well be on another host.
  // Reconnecting: the live stream dropped, so what is on screen may be stale until it is back and reloaded.
  const status = mode === 'live' ? (reconnecting ? 'Reconnecting…' : health?.db ? `Connected · ${health.db}` : 'Connected')
    : mode === 'offline' ? 'Not connected' : 'Connecting…';
  const dot = mode === 'live' ? (reconnecting ? 'var(--os-warn)' : 'var(--os-ok)') : 'var(--rail-dim)';

  return (
    <>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger
          aria-label={`Account: ${name}`}
          className={cn('rail-row flex w-full items-center gap-2.5 py-1.5 text-left', mini ? 'justify-center' : 'px-2')}
          style={{ borderRadius: RADIUS }}
        >
          <span className="grid size-8 shrink-0 place-items-center rounded-full text-[12px] font-semibold"
            style={{ background: 'var(--rail-accent)', color: 'var(--os-brand-ink)' }}>{initials(name)}</span>
          {!mini && (
            <>
              <span className="min-w-0 flex-1 leading-tight">
                <span className="block truncate text-[13.5px] font-semibold" style={{ color: 'var(--rail-ink)' }}>{name}</span>
                <span className="mt-0.5 flex items-center gap-1.5 text-[12px]" style={{ color: 'var(--rail-dim)' }}>
                  <span className="size-1.5 shrink-0 rounded-full" style={{ background: dot }} />
                  <span className="truncate">{status}</span>
                </span>
              </span>
              <ChevronsUpDown className="size-4 shrink-0" strokeWidth={1.8} style={{ color: 'var(--rail-dim)' }} />
            </>
          )}
        </PopoverTrigger>
        <PopoverContent side="top" align="start" sideOffset={8} className="w-[268px] gap-0 p-1.5">
          <div className="px-2.5 pt-2 pb-3">
            <div className="truncate text-[14px] font-semibold text-ink">{name}</div>
            <div className="truncate text-[12.5px] text-dim">{user?.email}</div>
            <div className="mt-2 flex flex-wrap gap-1">
              {(user?.roles ?? []).map((r) => (
                <Tag key={r} tone={r === 'owner' || r === 'admin' ? 'brand' : 'neutral'}>{roleName(catalogue, r)}</Tag>
              ))}
            </div>
            {workspace && <div className="mt-2 truncate text-[12px] text-dim">{workspace.name}</div>}
          </div>
          <div className="border-t border-line/60 pt-1">
            <MenuItem icon={Settings} label="Settings" onClick={() => { setOpen(false); nav('/settings'); }} />
            <MenuItem icon={KeyRound} label="Change password" onClick={() => { setOpen(false); setChanging(true); }} />
            <MenuItem icon={LogOut} label="Sign out" onClick={() => { setOpen(false); void logout(); }} />
          </div>
        </PopoverContent>
      </Popover>
      <PasswordDialog open={changing} onOpenChange={setChanging} />
    </>
  );
}

function MenuItem({ icon: I, label, onClick }: { icon: LucideIcon; label: string; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="flex w-full items-center gap-2.5 px-2.5 py-2 text-left text-[13.5px] text-ink-2 transition-colors hover:bg-surface-2 hover:text-ink"
      style={{ borderRadius: 'var(--radius)' }}
    >
      <I className="size-4 text-dim" />{label}
    </button>
  );
}

function PasswordDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [again, setAgain] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const ready = !!current && next.length >= 10 && next === again;

  const close = () => { onOpenChange(false); setCurrent(''); setNext(''); setAgain(''); setError(''); };
  const submit = async (e: SyntheticEvent) => {
    e.preventDefault();
    if (!ready || busy) return;
    setBusy(true);
    setError('');
    try {
      await api.changePassword(current, next);
      toast.success('Password changed', { description: 'Your other sessions were signed out. This one stays.' });
      close();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'The local API did not answer. Is it still running?');
    }
    setBusy(false);
  };

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) close(); }}>
      <DialogContent className="sm:max-w-[400px]">
        <form onSubmit={submit} className="grid gap-4">
          <DialogHeader>
            <DialogTitle>Change your password</DialogTitle>
            <DialogDescription>Every other session of yours is signed out. This one stays.</DialogDescription>
          </DialogHeader>
          <Field label="Current password" type="password" value={current} onChange={setCurrent} autoComplete="current-password" autoFocus />
          <Field label="New password" type="password" value={next} onChange={setNext} autoComplete="new-password" hint="At least 10 characters." />
          <Field label="New password, again" type="password" value={again} onChange={setAgain} autoComplete="new-password"
            hint={again && again !== next ? 'The two do not match yet.' : undefined} />
          {error && <p role="alert" className="text-[13px] text-danger">{error}</p>}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={close}>Cancel</Button>
            <Button type="submit" disabled={!ready || busy}>{busy ? 'Saving…' : 'Change password'}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
