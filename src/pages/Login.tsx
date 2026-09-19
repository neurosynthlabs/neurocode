import { useState, type SyntheticEvent } from 'react';
import { ArrowRight, Loader2, LockKeyhole, Mail, ShieldCheck } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Field, LogoMark } from '@/components/os';
import { ApiError } from '@/lib/api';
import { useAuth } from '@/lib/auth';

const POINTS = [
  ['Everyone signs in as themselves', 'What each person can change follows their role, and the server enforces it.'],
  ['Every change is on the record', 'Sign-ins and changes to access, keys or settings land in the audit log.'],
  ['The workspace is yours', 'It lives in your own database. A model sees only what a step sends it.'],
] as const;

export default function Login() {
  const { workspace, login } = useAuth();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const submit = async (e: SyntheticEvent) => {
    e.preventDefault();
    if (!email.trim() || !password || busy) return;
    setBusy(true);
    setError('');
    try {
      await login(email.trim(), password);  // on success the workspace replaces this screen
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'The local API did not answer. Is it still running?');
      setBusy(false);
    }
  };

  return (
    <div className="grid h-full w-full grid-cols-1 overflow-hidden bg-bg lg:grid-cols-[1.05fr_1fr]">
      {/* ── Brand side ───────────────────────────────────────── */}
      <aside className="rail-surface relative hidden min-h-0 flex-col justify-between overflow-hidden p-12 lg:flex">
        <div className="grid-lines pointer-events-none absolute inset-0 opacity-[0.3]" />
        <div className="relative flex items-center gap-3">
          <LogoMark size={40} />
          <span className="text-[20px] tracking-[-0.025em]">
            <span className="font-bold" style={{ color: 'var(--rail-ink)' }}>Neuro</span>
            <span className="font-semibold" style={{ color: 'var(--rail-accent)' }}>Code</span>
          </span>
        </div>
        <div className="relative max-w-md">
          <h1 className="text-[30px] leading-[1.2] font-semibold tracking-[-0.03em]" style={{ color: 'var(--rail-ink)' }}>
            One workspace for the whole engineering team.
          </h1>
          <p className="mt-4 text-[15px] leading-relaxed" style={{ color: 'var(--rail-soft)' }}>
            Requirements become plans, plans become reviewed work, and nothing risky ships without a person’s signature.
          </p>
          <ul className="mt-8 space-y-4">
            {POINTS.map(([title, text]) => (
              <li key={title} className="flex gap-3">
                <ShieldCheck className="mt-0.5 size-4 shrink-0" style={{ color: 'var(--rail-accent)' }} />
                <span>
                  <span className="block text-[14px] font-medium" style={{ color: 'var(--rail-ink)' }}>{title}</span>
                  <span className="mt-0.5 block text-[13px]" style={{ color: 'var(--rail-dim)' }}>{text}</span>
                </span>
              </li>
            ))}
          </ul>
        </div>
        <p className="relative font-mono text-[12px]" style={{ color: 'var(--rail-dim)' }}>v{__APP_VERSION__} · self-hosted</p>
      </aside>

      {/* ── Form side ────────────────────────────────────────── */}
      <main className="flex min-h-0 items-center justify-center overflow-y-auto px-6 py-10">
        <form onSubmit={submit} className="w-full max-w-[380px]">
          <div className="mb-8 flex items-center gap-2.5 lg:hidden">
            <LogoMark size={34} />
            <span className="text-[17px] tracking-[-0.02em]">
              <span className="font-bold text-ink">Neuro</span><span className="font-semibold text-brand">Code</span>
            </span>
          </div>

          <h2 className="text-[22px] font-semibold tracking-[-0.02em] text-ink">
            {workspace ? `Sign in to ${workspace.name}` : 'Sign in'}
          </h2>
          <p className="mt-1.5 text-[14px] text-soft">Use the account an Owner or Admin made for you.</p>

          <div className="mt-7 space-y-3.5">
            <Field label="Email" type="email" value={email} onChange={setEmail} autoComplete="username" autoFocus
              icon={<Mail className="size-3.5" />} placeholder="you@company.com" />
            <Field label="Password" type="password" value={password} onChange={setPassword} autoComplete="current-password"
              icon={<LockKeyhole className="size-3.5" />} />
            {error && <p role="alert" className="text-[13px] text-danger">{error}</p>}
            <Button type="submit" size="lg" className="w-full" disabled={busy || !email.trim() || !password}>
              {busy
                ? <><Loader2 className="size-3.5 animate-spin" />Signing in…</>
                : <>Sign in<ArrowRight className="size-3.5" /></>}
            </Button>
          </div>

          <p className="mt-5 text-[12.5px] leading-relaxed text-dim">
            Forgot your password? An Owner or Admin can set a new one for you in Admin → People.
          </p>

        </form>
      </main>
    </div>
  );
}
