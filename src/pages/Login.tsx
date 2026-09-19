import { useState, type KeyboardEvent, type SyntheticEvent } from 'react';
import { ArrowRight, Loader2 } from 'lucide-react';
import { LogoSymbol } from '@/components/os/Logo';
import { SignalGlow } from '@/components/os/Glow';
import { ApiError } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { cn } from '@/lib/utils';

/* The sign-in screen, drawn the way a Mac asks for a password: one centred column, the app's icon, and
   both fields in a single rounded group with the go button inside the password field. Everything is a
   theme token except the mark's marigold — the mark, the light behind it, the focus ring and the go button —
   which looks the same in every theme. */

export default function Login() {
  const { workspace, login } = useAuth();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  // Bumped on each refusal so the shake plays again for a second wrong password, not just the first.
  const [refusals, setRefusals] = useState(0);
  const [capsLock, setCapsLock] = useState(false);
  const ready = !!email.trim() && !!password && !busy;

  const submit = async (e: SyntheticEvent) => {
    e.preventDefault();
    if (!ready) return;
    setBusy(true);
    setError('');
    try {
      await login(email.trim(), password); // on success the workspace replaces this screen
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'The local API did not answer. Is it still running?');
      setRefusals((n) => n + 1);
      setPassword('');
      setBusy(false);
    }
  };
  const watchCaps = (e: KeyboardEvent<HTMLInputElement>) => setCapsLock(e.getModifierState('CapsLock'));

  return (
    <div className="relative flex h-full w-full flex-col overflow-y-auto bg-bg">
      <SignalGlow />

      <main className="relative flex flex-1 flex-col items-center justify-center px-5 py-14">
        <div className="nc-icon-in">
          <LogoSymbol size={76} title="NeuroCode" />
        </div>

        <div className="nc-arrive mt-7 flex w-full max-w-[340px] flex-col items-center [animation-delay:120ms]">
          <h1 className="text-center text-[27px] leading-[1.15] font-semibold tracking-[-0.028em] text-ink">
            Sign in to NeuroCode
          </h1>
          <p className="mt-2 text-center text-[15px] text-soft">
            {workspace ? `${workspace.name} workspace` : 'Your engineering workspace'}
          </p>

          <form onSubmit={submit} className="mt-8 w-full" noValidate>
            <div
              key={refusals}
              className={cn(
                'overflow-hidden rounded-[14px] border bg-surface/80 shadow-[0_22px_60px_-28px_var(--os-shadow)] backdrop-blur-xl transition-[border-color,box-shadow] duration-200',
                error
                  ? 'border-danger/55'
                  : 'border-line focus-within:border-[rgb(255_159_28/0.6)] focus-within:ring-4 focus-within:ring-[rgb(255_159_28/0.16)]',
                refusals > 0 && 'nc-shake',
              )}
            >
              <input
                type="email"
                aria-label="Email"
                placeholder="Email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoComplete="username"
                autoFocus
                spellCheck={false}
                className="block h-[50px] w-full bg-transparent px-4 text-[15px] text-ink placeholder:text-dim focus:outline-none"
              />
              <div className="mx-4 h-px bg-line" aria-hidden />
              <div className="flex items-center">
                <input
                  type="password"
                  aria-label="Password"
                  placeholder="Password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  onKeyDown={watchCaps}
                  onKeyUp={watchCaps}
                  onBlur={() => setCapsLock(false)}
                  autoComplete="current-password"
                  className="block h-[50px] min-w-0 flex-1 bg-transparent pr-2 pl-4 text-[15px] text-ink placeholder:text-dim focus:outline-none"
                />
                <button
                  type="submit"
                  aria-label="Sign in"
                  disabled={!ready}
                  className={cn(
                    'mr-2.5 grid size-8 shrink-0 place-items-center rounded-full transition-[background-color,color,transform] duration-200 focus-visible:ring-2 focus-visible:ring-[rgb(255_159_28/0.6)] focus-visible:outline-none',
                    ready
                      ? 'bg-[#FF9F1C] text-[#1B1C20] hover:scale-[1.06] active:scale-95'
                      : 'cursor-default border border-line text-dim',
                  )}
                >
                  {busy ? <Loader2 className="size-4 animate-spin" /> : <ArrowRight className="size-4" strokeWidth={2.4} />}
                </button>
              </div>
            </div>

            <div className="mt-3 min-h-[20px] text-center text-[13px] leading-relaxed" aria-live="polite">
              {error ? (
                <p role="alert" className="text-danger">{error}</p>
              ) : capsLock ? (
                <p className="text-soft">Caps Lock is on.</p>
              ) : null}
            </div>
          </form>

          <p className="mt-6 max-w-[300px] text-center text-[13px] leading-relaxed text-dim">
            Forgot your password? An Owner or Admin can set a new one in People.
          </p>
        </div>
      </main>

      <footer className="relative pb-7 text-center text-[12px] text-dim">
        NeuroCode {__APP_VERSION__}. Your workspace lives in your own database.
      </footer>
    </div>
  );
}
