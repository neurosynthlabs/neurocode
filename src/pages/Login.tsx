import { useState, type SyntheticEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowRight, Loader2, LockKeyhole, Mail, ShieldCheck } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Field, LogoMark, Tag } from '@/components/os';
import { ApiError } from '@/lib/api';
import { useAuth } from '@/lib/auth';

const POINTS = [
  ['Everyone signs in as themselves', 'What each person can change follows their role, and the server enforces it.'],
  ['Every change is on the record', 'Sign-ins and changes to access, keys or settings land in the audit log.'],
  ['It all stays on your machine', 'Source, secrets and customer data never leave it.'],
] as const;

export default function Login() {
  const nav = useNavigate();
  const { state, workspace, login } = useAuth();
  const demo = state === 'demo';
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const submit = async (e: SyntheticEvent) => {
    e.preventDefault();
    if (demo) { nav('/'); return; }
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
            {workspace && !demo ? `Sign in to ${workspace.name}` : 'Sign in'}
          </h2>
          <p className="mt-1.5 text-[14px] text-soft">Use the account an Owner or Admin made for you.</p>

          <div className="mt-7 space-y-3.5">
            <Field label="Email" type="email" value={email} onChange={setEmail} autoComplete="username" autoFocus={!demo}
              icon={<Mail className="size-3.5" />} placeholder="you@company.com" disabled={demo} />
            <Field label="Password" type="password" value={password} onChange={setPassword} autoComplete="current-password"
              icon={<LockKeyhole className="size-3.5" />} disabled={demo} />
            {error && <p role="alert" className="text-[13px] text-danger">{error}</p>}
            <Button type="submit" size="lg" className="w-full" disabled={busy || (!demo && (!email.trim() || !password))}>
              {busy
                ? <><Loader2 className="size-3.5 animate-spin" />Signing in…</>
                : <>{demo ? 'Open the demo' : 'Sign in'}<ArrowRight className="size-3.5" /></>}
            </Button>
          </div>

          <p className="mt-5 text-[12.5px] leading-relaxed text-dim">
            Forgot your password? An Owner or Admin can set a new one for you in Admin → People.
          </p>

          {demo && (
            <div className="mt-6 rounded-xl border border-line bg-surface p-3.5 text-[12.5px] leading-relaxed text-soft">
              <Tag tone="neutral" className="mb-1.5">Public demo</Tag>
              <p>There is no server behind this demo, so there is nothing to sign in to. Run it locally with
                <code className="mx-1 font-mono text-ink-2">npm run dev:start</code>for accounts, roles and the audit log.</p>
            </div>
          )}
        </form>
      </main>
    </div>
  );
}
